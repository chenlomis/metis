import os, re, datetime, logging
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", GMAIL_ADDRESS)
MODEL              = os.getenv("MODEL", "claude-sonnet-4-6")
MAX_JOBS_PER_RUN   = int(os.getenv("MAX_JOBS_PER_RUN", "20"))
DEFAULT_LOOKBACK   = "3d"

from .state import (
    DATA_DIR, LOG_DIR, SEEN_FILE,
    load_seen_roles, save_seen_roles, _role_hash,
)
from .sources import fetch_alerts
from .sources.linkedin import extract_jobs, extract_jobs_html, _extract_text
from .score import score_jobs_batch, rank_jobs
from .render import render_html, send_digest

# ---------------------------------------------------------------------------
# Startup validation — fail fast with a clear message if config is missing
# ---------------------------------------------------------------------------
def _validate_env(require_gmail: bool = True):
    errors = []
    if not ANTHROPIC_API_KEY:
        errors.append(
            "  ANTHROPIC_API_KEY is not set.\n"
            "  Get one at https://console.anthropic.com (separate from Claude.ai subscription)."
        )
    if require_gmail:
        if not GMAIL_ADDRESS:
            errors.append(
                "  GMAIL_ADDRESS is not set.\n"
                "  Add your Gmail address to .env."
            )
        if not GMAIL_APP_PASSWORD:
            errors.append(
                "  GMAIL_APP_PASSWORD is not set.\n"
                "  Generate one at https://myaccount.google.com/apppasswords (requires 2FA)."
            )
    if errors:
        print("❌  Missing required configuration:\n")
        for e in errors:
            print(e)
        print("\nSee .env.example for all required fields.")
        raise SystemExit(1)

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
# Helpers
# ---------------------------------------------------------------------------

def _parse_lookback(value: str) -> datetime.datetime | None:
    """Parse '3d', '7d', '2026-05-10', 'yesterday' → datetime. Returns None on failure."""
    if re.match(r'^\d+d$', value):
        return datetime.datetime.now() - datetime.timedelta(days=int(value[:-1]))
    import dateparser
    return dateparser.parse(value, settings={"RETURN_AS_TIMEZONE_AWARE": False})


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_pipeline(since_dt: datetime.datetime):
    """Fetch LinkedIn alert emails since since_dt, score unseen roles, deliver digest.

    seen_roles.json (14-day TTL) is the dedup gate — roles already scored
    within the last 14 days are skipped automatically.
    """
    log.info(f"=== Pipeline run starting — lookback since {since_dt.strftime('%Y-%m-%d')} ===")
    client     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    seen_roles = load_seen_roles()
    new_role_timestamps: dict = {}

    # Stage 1: Ingest
    threads = fetch_alerts(set(), since_dt)
    if not threads:
        log.info("No emails in lookback window. Done.")
        return

    all_jobs: list[dict] = []
    seen_job_ids:   set[str]   = set()
    seen_role_keys: set[tuple] = set()  # within-run dedup (same role, different location)
    for t in threads:
        jobs_from_thread = extract_jobs(t["body"])
        # Recommendation emails ("Company is hiring" / "Similar jobs") have no plain-text
        # "View job:" line — fall back to HTML link extraction
        if not jobs_from_thread and t.get("html"):
            jobs_from_thread = extract_jobs_html(t["html"])
            if jobs_from_thread:
                log.info(f"HTML extraction found {len(jobs_from_thread)} jobs in recommendation email")
        for job in jobs_from_thread:
            role_key  = (job["title"].lower().strip(), job["company"].lower().strip())
            role_hash = _role_hash(job["title"], job["company"])
            if (job["job_id"] not in seen_job_ids
                    and role_key not in seen_role_keys
                    and role_hash not in seen_roles):
                seen_job_ids.add(job["job_id"])
                seen_role_keys.add(role_key)
                seen_roles.add(role_hash)
                new_role_timestamps[role_hash] = (
                    datetime.datetime.now(datetime.timezone.utc)
                    .replace(tzinfo=None).isoformat()
                )
                all_jobs.append(job)

    if not all_jobs:
        log.info("No new roles to evaluate — all already seen within the past 14 days.")
        return

    if len(all_jobs) > MAX_JOBS_PER_RUN:
        log.info(f"{len(all_jobs)} jobs found, capping at {MAX_JOBS_PER_RUN} "
                 f"(set MAX_JOBS_PER_RUN in .env to change)")
        all_jobs = all_jobs[:MAX_JOBS_PER_RUN]
    else:
        log.info(f"{len(all_jobs)} unique roles to evaluate")

    # Stage 2: Enrich
    from .sources.linkedin import enrich_jobs
    all_jobs = enrich_jobs(all_jobs)

    # Stage 3: Score + rank
    all_jobs = score_jobs_batch(client, all_jobs)
    all_jobs = rank_jobs(all_jobs)

    # Stage 4: Deliver digest email
    run_date = datetime.datetime.now().strftime("%B %d, %Y")
    html = render_html(all_jobs, run_date)
    send_digest(html, run_date)

    # Persist — update seen_roles so the same role isn't scored again for 14 days
    save_seen_roles(new_role_timestamps)

    apply_n    = sum(1 for j in all_jobs if j["eval"].get("verdict") == "apply")
    consider_n = sum(1 for j in all_jobs if j["eval"].get("verdict") == "consider")
    log.info(f"=== Done — {len(all_jobs)} evaluated: {apply_n} apply, {consider_n} consider ===")


def debug_emails():
    """Dump the most recent LinkedIn email body to ~/.job_pipeline/debug_email.txt."""
    import imaplib, email as email_lib
    from .sources.linkedin import _LINKEDIN_SENDER_SEARCH
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        _, data = imap.search(None, _LINKEDIN_SENDER_SEARCH)
        all_ids = data[0].split()
        if not all_ids:
            print("No LinkedIn emails found.")
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

    parser = argparse.ArgumentParser(
        prog="scorerole",
        description="AI-powered job alert digest — filters, scores, and delivers "
                    "only what's worth your time.",
    )
    parser.add_argument(
        "--lookback", default=DEFAULT_LOOKBACK, metavar="DURATION",
        help=f"How far back to fetch emails. Accepts: '3d', '7d', '2026-05-10'. "
             f"Default: {DEFAULT_LOOKBACK}",
    )

    subparsers = parser.add_subparsers(dest="command")

    # init subcommand
    init_p = subparsers.add_parser(
        "init",
        help="Create your scoring profile from a resume (PDF, DOCX, or TXT).",
    )
    init_p.add_argument(
        "--resume", metavar="PATH",
        help="Path to your resume (PDF, DOCX, or TXT). Prompted interactively if omitted.",
    )
    init_p.add_argument(
        "--supplement", metavar="PATH",
        help="Optional: LinkedIn export PDF, bio, or any supplementary text file.",
    )

    # reset subcommand
    reset_p = subparsers.add_parser("reset", help="Clear seen-role state so all roles reprocess.")
    reset_p.add_argument("--force", action="store_true", help="Skip confirmation prompt.")

    # debug subcommand
    subparsers.add_parser("debug", help="Dump the most recent LinkedIn alert email for inspection.")

    args = parser.parse_args()

    if args.command == "init":
        _validate_env(require_gmail=False)   # only needs API key to parse resume
        from .init_cmd import run_init
        run_init(
            api_key=ANTHROPIC_API_KEY,
            resume_path_arg=getattr(args, "resume", "") or "",
            supplement_path_arg=getattr(args, "supplement", "") or "",
        )

    elif args.command == "reset":
        if not args.force:
            ans = input("Clear all state? Roles will be re-scored on the next run. [y/N] ")
            if ans.strip().lower() != "y":
                print("Aborted.")
                return
        SEEN_FILE.unlink(missing_ok=True)
        (DATA_DIR / "seen_roles.json").unlink(missing_ok=True)
        print("State cleared — all roles will re-score on the next run.")

    elif args.command == "debug":
        _validate_env()
        debug_emails()

    else:
        # Default: run the digest pipeline
        _validate_env()
        since_dt = _parse_lookback(args.lookback)
        if not since_dt:
            print(f"Could not parse --lookback '{args.lookback}'. "
                  f"Try: '3d', '7d', '2026-05-10'")
            raise SystemExit(1)
        run_pipeline(since_dt=since_dt)


if __name__ == "__main__":
    main()
