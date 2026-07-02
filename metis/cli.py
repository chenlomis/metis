from __future__ import annotations

import argparse
import datetime
import logging
import re
import sys

from .pipeline import (
    ANTHROPIC_API_KEY,
    GMAIL_ADDRESS,
    GMAIL_APP_PASSWORD,
    DATA_DIR,
    LLM_API_KEY,
    LOG_DIR,
    SEEN_FILE,
    _parse_lookback,
    _since_last_run,
    _validate_env,
    debug_emails,
    run_pipeline,
)


log = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metis",
        description="AI-powered job alert digest — filters, scores, and delivers "
                    "only what's worth your time.",
    )
    parser.add_argument(
        "--lookback", default=None, metavar="DURATION",
        help="Override lookback window. Accepts: '3d', '7d', '2026-05-10'. "
             "Default: since last run (falls back to 3d if no prior run).",
    )
    parser.add_argument(
        "--no-limit", dest="score_all", action="store_true",
        help="Score every role in the lookback window, ignoring MAX_JOBS_PER_RUN. "
             "A Haiku pre-screen runs first to keep API costs down. "
             "Useful for catch-up runs after a long gap or a reset.",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Full run (fetch + score) but no writes: no email sent, no seen_roles saved, no tracker updated.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        metavar="{init,config,reset,schedule,track,sources,feedback,debug,summary}",
    )

    subparsers.add_parser(
        "init",
        help="Conversational profile setup — freeform prompts instead of a form.",
    )

    config_p = subparsers.add_parser(
        "config",
        help="Manage Metis configuration.",
        description=(
            "  metis config access    connect Gmail or Outlook inbox via OAuth"
        ),
    )
    config_sub = config_p.add_subparsers(dest="config_action")
    config_sub.add_parser("access", help="Connect or reconnect your inbox via Gmail or Outlook OAuth.")

    reset_p = subparsers.add_parser("reset", help="Clear seen-role state so all roles reprocess.")
    reset_p.add_argument("--force", action="store_true", help="Skip confirmation prompt.")
    reset_p.add_argument("--profile", action="store_true", help="Also delete your scoring profile (~/.job_pipeline/profile.yaml).")

    schedule_p = subparsers.add_parser(
        "schedule",
        help="Install, inspect, or remove the automated digest schedule.",
        description=(
            "Show the current schedule when called with no action.\n\n"
            "  metis schedule        show current schedule + OS job status\n"
            "  metis schedule set    interactive setup (or update)\n"
            "  metis schedule remove remove the scheduled job"
        ),
    )
    schedule_sub = schedule_p.add_subparsers(dest="schedule_action")
    schedule_sub.add_parser("set", help="Run the interactive setup wizard to install or replace the schedule.")
    schedule_sub.add_parser("pause", help="Temporarily disable the schedule without losing your settings.")
    schedule_sub.add_parser("resume", help="Re-enable a paused schedule.")
    schedule_sub.add_parser("remove", help="Remove the scheduled job and clear ~/.job_pipeline/schedule.json.")
    run_p = schedule_sub.add_parser("run", help=argparse.SUPPRESS)
    run_p.add_argument("--lookback", dest="run_lookback", default="1d", metavar="DURATION")

    track_p = subparsers.add_parser(
        "track",
        help="Parse confirmation and rejection emails, update the Applications tracker.",
        description=(
            "Fetches emails from Gmail, classifies them as confirmations or rejections,\n"
            "and updates the Applications xlsx tracker accordingly.\n\n"
            "  metis track                   # parse last 7 days\n"
            "  metis track --lookback 30d    # extend lookback\n"
            "  metis track --dry-run         # preview matches, no writes"
        ),
    )
    track_p.add_argument(
        "--lookback", default="7d", metavar="DURATION",
        help="How far back to look for emails. Accepts '7d', '30d', '2026-06-01'. Default: 7d",
    )
    track_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Parse and classify emails; print matches to stdout without writing or opening the tracker.",
    )

    sources_p = subparsers.add_parser(
        "sources",
        help="Manage all job sources (email alerts + company career pages).",
        description=(
            "View and manage all job sources.\n\n"
            "  metis sources                    show all active sources\n"
            "  metis sources add                interactive picker (company or alert)\n"
            "  metis sources add Stripe         add a specific company\n"
            "  metis sources add --all          add every company in the pool\n"
            "  metis sources remove             interactively remove sources\n"
            "  metis sources on                 enable company scraping\n"
            "  metis sources off                disable company scraping"
        ),
    )
    sources_sub = sources_p.add_subparsers(dest="sources_action")
    sources_sub.add_parser("list", help="Show all active sources.")
    sources_add_p = sources_sub.add_parser("add", help="Add a company or alert source.")
    sources_add_p.add_argument("source_name", nargs="*", help="Company name to add (omit for interactive).")
    sources_add_p.add_argument("--all", dest="add_all", action="store_true", help="Add all companies in the pool.")
    sources_sub.add_parser("remove", help="Interactively remove sources.")
    sources_sub.add_parser("on", help="Enable company scraping.")
    sources_sub.add_parser("off", help="Disable company scraping.")
    email_p = sources_sub.add_parser("email", help="Manage email alert sources.")
    email_sub = email_p.add_subparsers(dest="email_action")
    email_sub.add_parser("list", help="List email alert sources.")
    email_add_p = email_sub.add_parser(
        "add",
        help="Add an email alert source.",
        description=(
            "Add a new email alert source.\n\n"
            "  metis sources email add                     interactive wizard\n"
            "  metis sources email add team@hi.wellfound.com   fetch + preview + confirm"
        ),
    )
    email_add_p.add_argument(
        "email_sender", nargs="?", default=None,
        help="Sender address to register directly (skips interactive wizard).",
    )
    email_sub.add_parser("remove", help="Remove an email alert source (interactive).")

    feedback_p = subparsers.add_parser(
        "feedback",
        help="Add calibration notes that shape future scoring runs.",
        description=(
            "Collect free-form feedback on past scoring, parsed by Claude and\n"
            "appended to ~/.job_pipeline/feedback.md. Injected into the scoring\n"
            "prompt on every subsequent run.\n\n"
            "  metis feedback add    # interactive prompt\n"
            "  metis feedback list   # show recent entries"
        ),
    )
    feedback_sub = feedback_p.add_subparsers(dest="feedback_action")
    feedback_sub.add_parser("add", help="Add a calibration note interactively.")
    feedback_sub.add_parser("list", help="Show recent feedback entries.")

    subparsers.add_parser("debug", help="Dump the most recent LinkedIn alert email for inspection.")

    summary_p = subparsers.add_parser(
        "summary",
        help="Generate and send the Metis market summary.",
        description=(
            "Compiles cumulative pipeline metrics from the tracker and sends\n"
            "the summary to your email address.\n\n"
            "  metis summary                        # send to email\n"
            "  metis summary --output summary.html  # save as HTML\n"
            "  metis summary --output summary.pdf   # save as PDF\n"
            "  metis summary --lookback 60d         # scope market intel to 60 days\n"
            "  metis summary --preview              # send with [DRAFT PREVIEW] prefix"
        ),
    )
    summary_p.add_argument("--output", default=None, metavar="FILE", help="Save summary to FILE (.html or .pdf) instead of sending by email.")
    summary_p.add_argument("--lookback", default="30d", metavar="DURATION", help="How far back to scope market intelligence sections. Default: 30d")
    summary_p.add_argument("--preview", action="store_true", help="Send with a [DRAFT PREVIEW] subject prefix.")
    summary_p.add_argument("--send", action="store_true", help=argparse.SUPPRESS)

    return parser


def _configure_logging() -> None:
    LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_DIR / f"{datetime.date.today()}.log"),
            logging.StreamHandler(),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "init_bak":
        legacy = argparse.ArgumentParser(prog="metis init_bak")
        legacy.add_argument(
            "--resume", metavar="PATH",
            help="Path to your resume (PDF, DOCX, or TXT). Prompted interactively if omitted.",
        )
        legacy.add_argument(
            "--linkedin", metavar="PATH",
            help="Optional: LinkedIn export PDF or data archive for profile enrichment.",
        )
        args = legacy.parse_args(raw_argv[1:])
        _configure_logging()
        _validate_env(require_gmail=False)
        from .init_bak_cmd import run_init
        run_init(
            api_key=ANTHROPIC_API_KEY,
            resume_path_arg=getattr(args, "resume", "") or "",
            supplement_path_arg=getattr(args, "linkedin", "") or "",
        )
        return

    parser = _build_parser()
    args = parser.parse_args(raw_argv)
    _configure_logging()

    if args.command == "config":
        action = getattr(args, "config_action", None)
        if action == "access":
            from .config_access_cmd import run_config_access
            run_config_access()
        else:
            parser.parse_args(["config", "--help"])

    elif args.command == "init":
        _validate_env(require_gmail=False)
        from .init_cmd import run_init
        run_init(api_key=LLM_API_KEY)

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
            print("Run `metis init` to rebuild your scoring profile.")

    elif args.command == "schedule":
        from .schedule_cmd import (
            show_schedule, run_schedule_wizard, remove_schedule,
            pause_schedule, resume_schedule,
        )
        action = getattr(args, "schedule_action", None)
        if action == "set":
            run_schedule_wizard()
        elif action == "pause":
            paused = pause_schedule()
            print("  Schedule paused. Run `metis schedule resume` to re-enable." if paused else "  Nothing to pause — schedule is already paused or not configured.")
        elif action == "resume":
            resumed = resume_schedule()
            print("  Schedule resumed." if resumed else "  Nothing to resume — schedule is already active or not configured.")
        elif action == "remove":
            removed = remove_schedule()
            print("  Schedule removed." if removed else "  No schedule was configured.")
        elif action == "run":
            _validate_env()
            lookback_str = getattr(args, "run_lookback", "1d") or "1d"
            since_dt = _parse_lookback(lookback_str)
            if not since_dt:
                print(f"Could not parse --lookback '{lookback_str}'. Try: '1d', '4d', '7d'")
                raise SystemExit(1)
            log.info("=== Scheduled run: digest + track (lookback %s) ===", lookback_str)
            run_pipeline(since_dt=since_dt)
            from .track import run_track
            run_track(
                gmail_address=GMAIL_ADDRESS,
                app_password=GMAIL_APP_PASSWORD,
                since_dt=since_dt,
                dry_run=False,
                api_key=LLM_API_KEY,
            )
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
            api_key=LLM_API_KEY,
        )

    elif args.command == "sources":
        action = getattr(args, "sources_action", None)
        name_parts = getattr(args, "source_name", None)
        email_action = getattr(args, "email_action", None)
        email_sender = getattr(args, "email_sender", None)
        if action == "add" and name_parts and name_parts[0].lower() == "email":
            action = "email"
            email_action = "add"
            email_sender = name_parts[1] if len(name_parts) > 1 else None
            name_parts = []
        from .sources_cmd import run_sources
        name = " ".join(name_parts) if name_parts else None
        add_all = getattr(args, "add_all", False)
        run_sources(action, name or None, add_all=add_all, email_action=email_action,
                    email_sender=email_sender)

    elif args.command == "feedback":
        _validate_env(require_gmail=False)
        action = getattr(args, "feedback_action", None)
        if action == "list":
            from .feedback import run_feedback_list
            run_feedback_list()
        else:
            from .feedback import run_feedback
            run_feedback(api_key=LLM_API_KEY)

    elif args.command == "debug":
        _validate_env()
        debug_emails()

    elif args.command == "summary":
        _validate_env()
        from .report_cmd import run_report
        from .xlsx import TRACKER_PATH
        lookback_str = getattr(args, "lookback", "30d") or "30d"
        lookback_days = int(re.sub(r"[^\d]", "", lookback_str) or 30)
        run_report(
            tracker_path=TRACKER_PATH,
            gmail_address=GMAIL_ADDRESS,
            app_password=GMAIL_APP_PASSWORD,
            output=getattr(args, "output", None),
            preview=getattr(args, "preview", False),
            lookback_days=lookback_days,
        )

    else:
        _validate_env()
        if args.lookback:
            since_dt = _parse_lookback(args.lookback)
            if not since_dt:
                print(f"Could not parse --lookback '{args.lookback}'. Try: '3d', '7d', '2026-05-10'")
                raise SystemExit(1)
        else:
            since_dt, label = _since_last_run()
            log.info("Lookback window: %s", label)
        run_pipeline(since_dt=since_dt, score_all=args.score_all, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
