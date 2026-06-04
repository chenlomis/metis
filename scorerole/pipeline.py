import os, re, datetime, logging
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "chenlomis@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", "chenlomis@gmail.com")
MODEL              = "claude-sonnet-4-6"
MAX_JOBS_PER_RUN   = int(os.getenv("MAX_JOBS_PER_RUN", "20"))

from .state import (
    DATA_DIR, LOG_DIR, SEEN_FILE,
    load_seen_ids, save_seen_ids,
    load_seen_roles, save_seen_roles, _role_hash,
)
from .sources import fetch_alerts
from .sources.linkedin import extract_jobs, extract_jobs_html, _extract_text
from .score import score_jobs_batch, rank_jobs, SCORE_SYSTEM
from .render import render_html, send_digest

# ---------------------------------------------------------------------------
# Logging — set up after DATA_DIR is imported
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / f"{datetime.date.today()}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_pipeline(since_dt=None):
    log.info("=== Pipeline run starting ===")
    client    = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    seen      = load_seen_ids()
    is_rerun  = since_dt is not None
    seen_roles          = load_seen_roles()
    new_role_timestamps: dict = {}

    # Stage 1: Ingest
    threads = fetch_alerts(seen, since_dt)
    if not threads:
        log.info("No new alert emails. Done.")
        return

    all_jobs: list[dict] = []
    seen_job_ids:   set[str]   = set()
    seen_role_keys: set[tuple] = set()  # dedup same title+company from different locations
    for t in threads:
        jobs_from_thread = extract_jobs(t["body"])
        # Recommendation emails ("Company is hiring" / "Similar jobs") have no plain-text
        # "View job:" lines — fall back to HTML link extraction
        if not jobs_from_thread and t.get("html"):
            jobs_from_thread = extract_jobs_html(t["html"])
            if jobs_from_thread:
                log.info(f"HTML extraction found {len(jobs_from_thread)} jobs in recommendation email")
        for job in jobs_from_thread:
            role_key  = (job["title"].lower().strip(), job["company"].lower().strip())
            role_hash = _role_hash(job["title"], job["company"])
            if (job["job_id"] not in seen_job_ids
                    and role_key not in seen_role_keys
                    and (is_rerun or role_hash not in seen_roles)):
                seen_job_ids.add(job["job_id"])
                seen_role_keys.add(role_key)
                seen_roles.add(role_hash)
                new_role_timestamps[role_hash] = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .replace(tzinfo=None).isoformat()
                )
                all_jobs.append(job)
        if jobs_from_thread:
            seen.add(t["msg_id"])  # only mark seen if we got jobs from it

    if not all_jobs:
        log.info("Emails found but no jobs parsed — run with --debug to inspect email format.")
        return  # don't save seen_ids so emails are retried next run

    if len(all_jobs) > MAX_JOBS_PER_RUN:
        log.info(f"{len(all_jobs)} jobs found, capping at {MAX_JOBS_PER_RUN} (set MAX_JOBS_PER_RUN in .env to change)")
        all_jobs = all_jobs[:MAX_JOBS_PER_RUN]
    else:
        log.info(f"{len(all_jobs)} unique jobs to evaluate")

    # Stage 2: Enrich
    from .sources.linkedin import enrich_jobs
    all_jobs = enrich_jobs(all_jobs)

    # Stage 3: Score + rank
    all_jobs = score_jobs_batch(client, all_jobs)
    all_jobs = rank_jobs(all_jobs)

    # Stage 4: Deliver
    run_date = datetime.datetime.now().strftime("%B %d, %Y")
    html = render_html(all_jobs, run_date)
    send_digest(html, run_date)

    # Persist state only on normal runs — reruns are read-only
    if not is_rerun:
        save_seen_ids(seen)
        save_seen_roles(new_role_timestamps)

    apply_n    = sum(1 for j in all_jobs if j["eval"].get("verdict") == "apply")
    consider_n = sum(1 for j in all_jobs if j["eval"].get("verdict") == "consider")
    log.info(f"=== Done — {len(all_jobs)} evaluated: {apply_n} apply, {consider_n} consider ===")


def debug_emails():
    """Dump the first raw email body to ~/.job_pipeline/debug_email.txt for regex inspection."""
    import imaplib, email as email_lib
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        _, data = imap.search(None, 'FROM "jobalerts-noreply@linkedin.com"')
        all_ids = data[0].split()
        if not all_ids:
            print("No LinkedIn alert emails found.")
            return
        _, raw = imap.fetch(all_ids[-1], "(RFC822)")
        msg = email_lib.message_from_bytes(raw[0][1])
        body = _extract_text(msg)
    out = DATA_DIR / "debug_email.txt"
    out.write_text(body)
    print(f"Raw email body written to: {out}")
    print("--- First 2000 chars ---")
    print(body[:2000])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(prog="scorerole")
    parser.add_argument("--debug", action="store_true",
                        help="Dump a raw email body to inspect format")
    parser.add_argument("--reset", action="store_true",
                        help="Clear seen-IDs and seen-roles so all emails reprocess")
    parser.add_argument(
        "--since", metavar="DATE",
        help="Reprocess emails since DATE. Examples: '7d', 'yesterday', '2026-05-10'. "
             "Read-only — does not update seen_ids or seen_roles."
    )
    args = parser.parse_args()

    if args.reset:
        SEEN_FILE.unlink(missing_ok=True)
        (DATA_DIR / "seen_roles.json").unlink(missing_ok=True)
        print("Seen IDs and seen roles cleared — all emails will reprocess on next run.")
    elif args.debug:
        debug_emails()
    elif args.since:
        import dateparser
        since_str = args.since
        if re.match(r'^\d+d$', since_str):
            days = int(since_str[:-1])
            since_dt = datetime.datetime.now() - datetime.timedelta(days=days)
        else:
            since_dt = dateparser.parse(since_str, settings={"RETURN_AS_TIMEZONE_AWARE": False})
        if not since_dt:
            print(f"Could not parse --since '{args.since}'. "
                  f"Try: '7d', 'yesterday', '2026-05-10'")
            raise SystemExit(1)
        log.info(f"Rerun mode: fetching emails since {since_dt.strftime('%Y-%m-%d')}")
        run_pipeline(since_dt=since_dt)
    else:
        run_pipeline()


if __name__ == "__main__":
    main()
