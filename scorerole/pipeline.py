from __future__ import annotations
import os, re, sys, datetime, logging
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
DEFAULT_LOOKBACK   = os.getenv("DEFAULT_LOOKBACK", "3d")

from .state import (
    DATA_DIR, LOG_DIR, SEEN_FILE,
    load_seen_roles, save_seen_roles, save_skipped_roles, _role_hash,
)
from .feedback_cmd import save_last_run
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

# Module-level logger reference — handlers are wired up inside main()
# so that importing pipeline doesn't create directories or touch the root logger.
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


def _prompt_score_all(n_found: int, cap: int) -> bool:
    """Interactively ask whether to score all roles. Returns True = score all."""
    from .score import estimate_cost
    print(f"\n  ⚠  Found {n_found} new roles in your lookback window.")
    print(f"     Your cap is {cap} (MAX_JOBS_PER_RUN in .env).")
    print(f"     Scoring all {n_found}: ~{estimate_cost(n_found)} estimated")
    print(f"     (A Haiku pre-screen runs first to filter obvious mismatches —")
    print(f"     actual cost is typically 40–60% lower than the estimate above.)")
    print(f"     Roles beyond the cap stay unseen until their 14-day TTL expires.\n")
    try:
        ans = input(f"  Score all {n_found} roles? [y/N]: ").strip().lower()
        return ans == "y"
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# ---------------------------------------------------------------------------
# Pipeline stages (private — called in sequence by run_pipeline)
# ---------------------------------------------------------------------------

def _stage_ingest(since_dt: datetime.datetime, seen_roles: set, profile=None) -> "list[dict] | None":
    """Fetch job alerts (LinkedIn + proactive sources) and return deduplicated new jobs.

    Applies three dedup layers:
      1. job_id exact duplicate within this run
      2. title+company key (same role, different location email)
      3. role_hash in seen_roles (14-day cross-run TTL gate)

    Returns:
      None           — no emails found and no proactive jobs
      []  (empty)    — sources returned jobs but all already seen within 14 days
      [...]           — new, unseen roles ready for cap + scoring
    """
    threads = fetch_alerts(since_dt, profile=profile)
    if not threads:
        log.info("No emails in lookback window. Done.")
        return None

    all_jobs: list[dict] = []
    seen_job_ids:   set[str]   = set()
    seen_role_keys: set[tuple] = set()

    for t in threads:
        jobs_from_thread = extract_jobs(t["body"])
        # Recommendation emails ("Company is hiring" / "Similar jobs") have no plain-text
        # "View job:" line — fall back to HTML link extraction.
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
                seen_roles.add(role_hash)   # in-memory dedup; disk write deferred until after cap
                all_jobs.append(job)

    return all_jobs


def _stage_cap(
    all_jobs: list[dict],
    score_all: bool,
    client,                  # anthropic.Anthropic — passed through to prescreen if needed
) -> tuple[list[dict], bool]:
    """Apply the per-run cap and optionally run the Haiku pre-screen.

    Returns (jobs_to_score, did_prescreen).

    Cap logic (in priority order):
      --no-limit flag            → skip prompt, run Haiku pre-screen, score everything that survives
      interactive TTY       → ask user; if yes, run pre-screen; if no, cap to MAX_JOBS_PER_RUN
      non-interactive cron  → cap silently, log warning

    IMPORTANT: This function must be called BEFORE building new_role_timestamps so that
    only the final survivors get persisted to seen_roles.json (role-burial fix).
    """
    n_found = len(all_jobs)
    should_prescreen = False

    # Show cost estimate upfront when --no-limit is used (spec requirement).
    if score_all:
        from .score import estimate_cost
        print(
            f"\n  --no-limit: {n_found} role{'s' if n_found != 1 else ''} in window. "
            f"Estimated cost: ~{estimate_cost(n_found)}\n"
            f"  (Haiku pre-screen will filter obvious mismatches — actual cost typically lower.)\n"
        )

    if MAX_JOBS_PER_RUN > 0 and n_found > MAX_JOBS_PER_RUN:
        if score_all:
            log.info(f"{n_found} roles to evaluate (--no-limit flag; Haiku pre-screen will filter)")
            should_prescreen = True
        elif sys.stdin.isatty():
            wants_all = _prompt_score_all(n_found, MAX_JOBS_PER_RUN)
            if wants_all:
                log.info(f"Scoring all {n_found} roles (Haiku pre-screen will filter first)")
                should_prescreen = True
            else:
                all_jobs = all_jobs[:MAX_JOBS_PER_RUN]
                log.info(f"Capped at {MAX_JOBS_PER_RUN} roles")
        else:
            log.warning(
                f"{n_found} roles found but capped at {MAX_JOBS_PER_RUN} "
                f"(non-interactive run). Use --no-limit to score everything, "
                f"or set MAX_JOBS_PER_RUN=0 in .env to remove the cap entirely."
            )
            all_jobs = all_jobs[:MAX_JOBS_PER_RUN]
    else:
        log.info(f"{n_found} unique roles to evaluate")

    if should_prescreen:
        from .score import prescreen_jobs_batch
        all_jobs = prescreen_jobs_batch(client, all_jobs)

    return all_jobs, should_prescreen


def _stage_enrich_and_score(jobs: list[dict], client) -> list[dict]:
    """Fetch JDs (enrich), run Layer 1 extraction + gate checks, then score + rank with Sonnet.

    Layer 1 (Haiku, temperature=0):
      - Extracts structured fields (salary, work model, degree req, domain, etc.)
      - Hard gates: blank JDs skip Sonnet; disclosed salary below floor → filtered
      - Extraction failures fall back gracefully — scoring is never blocked

    Layer 2 (Sonnet):
      - Receives extraction context per job as grounding
      - Only runs on roles that passed all hard gates

    Returns ranked jobs with 'eval' key populated.
    Raises SystemExit if the profile is missing.
    """
    from .sources.linkedin import enrich_jobs
    from .extract import extract_jd_structs, check_hard_gates, format_extraction_for_scoring
    from .profile import load_profile_yaml

    jobs = enrich_jobs(jobs)

    # Load profile for gate checks
    try:
        profile_data = load_profile_yaml() or {}
    except Exception:
        profile_data = {}

    # Layer 1: structured extraction
    try:
        extractions = extract_jd_structs(client, jobs)
    except Exception as exc:
        log.warning("Layer 1 extraction failed (%s) — scoring without extraction context", exc)
        extractions = [{} for _ in jobs]

    # Attach extraction structs and apply hard gates
    to_score: list[dict] = []
    for job, ext in zip(jobs, extractions):
        job["extraction"] = ext
        passes, gate_name = check_hard_gates(ext, profile_data)
        if not passes:
            job["eval"] = {
                "score": 0,
                "verdict": "filtered",
                "leveragePoints": [],
                "frictionPoints": [],
                "tags": [{"text": f"gate: {gate_name}", "sentiment": "red"}],
            }
            log.info("Gate filtered: %s at %s — %s", job["title"], job["company"], gate_name)
        else:
            to_score.append(job)

    if to_score:
        try:
            score_jobs_batch(client, to_score)   # mutates job["eval"] in-place
        except FileNotFoundError:
            raise SystemExit(
                "\n❌  No scoring profile found.\n"
                "   Run `scorerole init` to create one from your resume.\n"
            )

    return rank_jobs(jobs)


def _stage_split_filtered(jobs: list[dict]) -> tuple[list[dict], int]:
    """Separate deal-breaker-filtered roles from scoreable ones.

    Filtered roles must not appear in digest sections — only in the footer count.
    Their hashes are already recorded in new_role_timestamps (built before this call)
    so they ARE marked seen and won't reappear next run.

    Returns (scored_jobs, n_filtered).
    """
    filtered = [j for j in jobs if j["eval"].get("verdict") == "filtered"]
    scored   = [j for j in jobs if j["eval"].get("verdict") != "filtered"]
    if filtered:
        log.info("%d role(s) filtered by deal-breaker — excluded from digest sections", len(filtered))
    return scored, len(filtered)


def _stage_deliver(
    scored_jobs: list[dict],
    n_filtered: int,
    new_role_timestamps: dict,
    no_tracker: bool = False,
) -> None:
    """Render the HTML digest and deliver it via SMTP.

    Raises SystemExit(1) on SMTP failure (so seen_roles.json is NOT written — the roles
    remain unseen and will be re-scored on the next run, per SPEC §8 / T-07).
    On success, persists new_role_timestamps to seen_roles.json and writes Apply/Consider
    rows to the Applications xlsx tracker.

    Skipped-role metadata is saved to skipped_roles.json BEFORE delivery so it survives
    even if SMTP fails — it's needed for future backport and costs nothing to keep.
    """
    # Save skipped role metadata before delivery (safe to write regardless of SMTP outcome)
    skipped_jobs = [j for j in scored_jobs if j.get("eval", {}).get("verdict") == "skipped"]
    if skipped_jobs:
        save_skipped_roles(skipped_jobs)

    run_date = datetime.datetime.now().strftime("%B %d, %Y")
    html = render_html(scored_jobs, run_date, deal_breaker_count=n_filtered)
    try:
        send_digest(html, run_date)
    except Exception:
        log.error("Pipeline finished scoring but failed to deliver digest — check SMTP settings in .env")
        raise SystemExit(1)

    save_seen_roles(new_role_timestamps)
    save_last_run(scored_jobs, run_date, filtered_count=n_filtered)

    if not no_tracker:
        from .tracker import write_to_tracker, TRACKER_PATH
        write_to_tracker(scored_jobs, run_date=datetime.date.today().isoformat())
        print(f"  Tracker → {TRACKER_PATH}")

    apply_n    = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "apply")
    consider_n = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "consider")
    filter_note = f", {n_filtered} filtered by deal-breaker" if n_filtered else ""
    log.info(
        f"=== Done — {len(scored_jobs)} evaluated: {apply_n} apply, {consider_n} consider{filter_note} ==="
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_pipeline(since_dt: datetime.datetime, score_all: bool = False, no_tracker: bool = False):
    """Fetch LinkedIn alert emails since since_dt, score unseen roles, deliver digest.

    seen_roles.json (14-day TTL) is the dedup gate — roles already scored
    within the last 14 days are skipped automatically.

    score_all=True (--no-limit flag) bypasses the cap and runs a Haiku pre-screen
    before full Sonnet scoring to keep costs down.

    no_tracker=True skips writing to the Applications xlsx (useful for test runs).
    """
    log.info(f"=== Pipeline run starting — lookback since {since_dt.strftime('%Y-%m-%d')} ===")
    client     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    seen_roles = load_seen_roles()

    # Load profile early — needed for proactive source config and gate checks
    from .profile import load_profile_yaml
    profile_data = load_profile_yaml() or {}

    # Stage 1: Ingest + deduplicate
    all_jobs = _stage_ingest(since_dt, seen_roles, profile=profile_data)
    if all_jobs is None:
        return   # "No emails in lookback window" already logged inside _stage_ingest
    if not all_jobs:
        log.info("No new roles to evaluate — all already seen within the past 14 days.")
        return

    # Stage 2: Apply cap, optionally run Haiku pre-screen
    # new_role_timestamps is built AFTER this so only surviving roles get persisted.
    # (Pre-fix: capped roles were written to seen_roles.json and locked out for 14 days
    #  without ever being evaluated — the role-burial bug.)
    all_jobs, _prescreened = _stage_cap(all_jobs, score_all, client)
    if not all_jobs:
        log.info("Pre-screen filtered all roles — nothing left to score.")
        return

    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
    new_role_timestamps = {
        _role_hash(j["title"], j["company"]): now_iso
        for j in all_jobs
    }

    # Stage 3: Enrich (fetch JDs) + score + rank
    all_jobs = _stage_enrich_and_score(all_jobs, client)

    # Stage 4: Split deal-breaker filtered roles
    scored_jobs, n_filtered = _stage_split_filtered(all_jobs)

    if not scored_jobs:
        if n_filtered:
            log.info(
                "All %d role(s) were filtered by deal-breaker rules — no digest sent.\n"
                "If this seems wrong, run `scorerole init` → Quick edits → Deal-breakers "
                "to review your rules.",
                n_filtered,
            )
        else:
            log.info("No scoreable roles after filtering — no digest sent.")
        save_seen_roles(new_role_timestamps)
        return

    # Stage 5: Render + deliver digest (persists seen_roles on success)
    _stage_deliver(scored_jobs, n_filtered, new_role_timestamps, no_tracker=no_tracker)


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

    # Set up logging here (not at module level) so importing pipeline
    # doesn't create directories or hijack the root logger.
    LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)  # restrict ~/.job_pipeline to owner
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_DIR / f"{datetime.date.today()}.log"),
            logging.StreamHandler(),
        ],
    )

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
    parser.add_argument(
        "--no-limit", dest="score_all", action="store_true",
        help="Score every role in the lookback window, ignoring MAX_JOBS_PER_RUN. "
             "A Haiku pre-screen runs first to keep API costs down. "
             "Useful for catch-up runs after a long gap or a reset.",
    )
    parser.add_argument(
        "--no-tracker", dest="no_tracker", action="store_true",
        help="Skip writing to the Applications xlsx tracker. Useful for test/dry runs.",
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
    reset_p.add_argument("--force",   action="store_true", help="Skip confirmation prompt.")
    reset_p.add_argument("--profile", action="store_true", help="Also delete your scoring profile (~/.job_pipeline/profile.yaml).")

    # schedule subcommand  (git-style nested actions)
    schedule_p = subparsers.add_parser(
        "schedule",
        help="Install, inspect, or remove the automated digest schedule.",
        description=(
            "Show the current schedule when called with no action.\n\n"
            "  scorerole schedule        show current schedule + OS job status\n"
            "  scorerole schedule set    interactive setup (or update)\n"
            "  scorerole schedule remove remove the scheduled job"
        ),
    )
    schedule_sub = schedule_p.add_subparsers(dest="schedule_action")
    schedule_sub.add_parser(
        "set",
        help="Run the interactive setup wizard to install or replace the schedule.",
    )
    schedule_sub.add_parser(
        "remove",
        help="Remove the scheduled job and clear ~/.job_pipeline/schedule.json.",
    )

    # track subcommand
    track_p = subparsers.add_parser(
        "track",
        help="Parse confirmation and rejection emails, update the Applications tracker.",
        description=(
            "Fetches emails from Gmail, classifies them as confirmations or rejections,\n"
            "and updates the Applications xlsx tracker accordingly.\n\n"
            "  scorerole track                   # parse last 7 days\n"
            "  scorerole track --lookback 30d    # extend lookback\n"
            "  scorerole track --dry-run         # preview matches, no writes"
        ),
    )
    track_p.add_argument(
        "--lookback", default="7d", metavar="DURATION",
        help="How far back to look for emails. Accepts '7d', '30d', '2026-06-01'. Default: 7d",
    )
    track_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Parse and print matches without writing to the tracker.",
    )

    # feedback subcommand
    subparsers.add_parser(
        "feedback",
        help="Add calibration notes that shape future scoring runs.",
        description=(
            "Shows a summary of the last run, then prompts for free-form feedback.\n"
            "Notes are appended to ~/.job_pipeline/feedback.md and injected into\n"
            "the scoring prompt on every subsequent run.\n\n"
            "  scorerole feedback    # interactive prompt"
        ),
    )

    # debug subcommand
    subparsers.add_parser("debug", help="Dump the most recent LinkedIn alert email for inspection.")

    # tracker subcommand
    subparsers.add_parser(
        "tracker",
        help="Show the tracker path and open the Applications spreadsheet.",
    )

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
        targets = [SEEN_FILE]
        if args.profile:
            targets.append(DATA_DIR / "profile.yaml")

        existing = [p for p in targets if p.exists()]
        if not existing:
            print("Nothing to reset — no state files found.")
            return

        names = ", ".join(p.name for p in existing)
        if not args.force:
            suffix = " + your scoring profile" if args.profile else ""
            ans = input(f"Clear dedup state{suffix}? This cannot be undone. [y/N] ")
            if ans.strip().lower() != "y":
                print("Aborted.")
                return

        for p in existing:
            p.unlink(missing_ok=True)
        print(f"Cleared: {names}")
        if args.profile and (DATA_DIR / "profile.yaml") in existing:
            print("Run `scorerole init` to rebuild your scoring profile.")

    elif args.command == "schedule":
        from .schedule_cmd import show_schedule, run_schedule_wizard, remove_schedule
        action = getattr(args, "schedule_action", None)
        if action == "remove":
            removed = remove_schedule()
            print("  Schedule removed." if removed else "  No schedule was configured.")
        elif action == "set":
            run_schedule_wizard()
        else:
            show_schedule()

    elif args.command == "track":
        _validate_env()
        since_dt = _parse_lookback(getattr(args, "lookback", "7d"))
        if not since_dt:
            print(f"Could not parse --lookback '{args.lookback}'. Try: '7d', '30d', '2026-06-01'")
            raise SystemExit(1)
        from .track import run_track
        run_track(
            gmail_address=GMAIL_ADDRESS,
            app_password=GMAIL_APP_PASSWORD,
            since_dt=since_dt,
            dry_run=getattr(args, "dry_run", False),
        )

    elif args.command == "feedback":
        from .feedback_cmd import run_feedback
        run_feedback()

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
        run_pipeline(since_dt=since_dt, score_all=args.score_all, no_tracker=args.no_tracker)


if __name__ == "__main__":
    main()
