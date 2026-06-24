"""
Persona test runner — runs the full pipeline for each persona profile without
touching ~/.job_pipeline/ or profile.yaml.

Both SCOREROLE_PROFILE and SCOREROLE_DATA_DIR are set per persona so that
seen_roles, last_run, runs.jsonl, and all state land in a fully isolated dir.
Your real pipeline is never read or written.

Usage:
  python run_persona_test.py                     # all personas, 7-day lookback
  python run_persona_test.py --lookback 14       # override window
  python run_persona_test.py --personas pm       # pm group only
  python run_persona_test.py --personas designer mle  # specific groups
  python run_persona_test.py --dry-run           # no email, no state writes

Groups: pm, designer, mle
"""
import argparse, datetime, logging, os, sys
from pathlib import Path

HOME = Path.home()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("persona_test")

# ---------------------------------------------------------------------------
# Persona registry
# Each entry: (group, profile_path, data_dir, display_label)
# ---------------------------------------------------------------------------
_PERSONA_REGISTRY = [
    # ── Product Manager personas ────────────────────────────────────────────
    ("pm", HOME / ".job_pipeline_pm_priya",  HOME / ".job_pipeline_pm_priya",  "PM — Priya Mehta (Senior→Staff, B2B Fintech)"),
    ("pm", HOME / ".job_pipeline_pm_marcus", HOME / ".job_pipeline_pm_marcus", "PM — Marcus Webb (Director-track, Consumer Marketplace)"),
    ("pm", HOME / ".job_pipeline_pm_sarah",  HOME / ".job_pipeline_pm_sarah",  "PM — Sarah Okonkwo (Technical PM, Platform/API)"),
    ("pm", HOME / ".job_pipeline_pm_jake",   HOME / ".job_pipeline_pm_jake",   "PM — Jake Torres (Growth/Monetization, FinTech)"),
    ("pm", HOME / ".job_pipeline_pm_diana",  HOME / ".job_pipeline_pm_diana",  "PM — Diana Walsh (Generalist→AI Director, HealthTech)"),
    # ── Legacy personas (pre-existing) ─────────────────────────────────────
    ("designer", HOME / ".job_pipeline_designer", HOME / ".job_pipeline_designer", "Designer — Jordan Rivera (Staff Designer, AI Tools)"),
    ("mle",      HOME / ".job_pipeline_mle",      HOME / ".job_pipeline_mle",      "MLE — Alex Kim (Staff SWE/MLE, AI Infra)"),
]


def run_for_persona(
    profile_dir: Path,
    data_dir: Path,
    label: str,
    lookback_days: int = 7,
    dry_run: bool = False,
) -> bool:
    log.info("=" * 60)
    log.info(f"PERSONA: {label}")
    log.info("=" * 60)

    profile_path = profile_dir / "profile.yaml"
    if not profile_path.exists():
        log.error(f"Profile not found: {profile_path}")
        return False

    # Isolate both profile and all pipeline state (seen_roles, last_run, etc.)
    os.environ["SCOREROLE_PROFILE"]  = str(profile_path)
    os.environ["SCOREROLE_DATA_DIR"] = str(data_dir)

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
    # Re-apply overrides — dotenv (override=True) won't clobber pre-existing env
    # vars, so re-set defensively to ensure isolation sticks after dotenv.
    os.environ["SCOREROLE_PROFILE"]  = str(profile_path)
    os.environ["SCOREROLE_DATA_DIR"] = str(data_dir)

    import importlib
    import scorerole.profile as profile_mod
    importlib.reload(profile_mod)
    import scorerole.pipeline as pipeline_mod
    importlib.reload(pipeline_mod)
    import scorerole.score as score_mod
    importlib.reload(score_mod)
    import scorerole.render as render_mod
    importlib.reload(render_mod)
    import scorerole.state as state_mod
    importlib.reload(state_mod)

    _orig_send = render_mod.send_digest

    def _labelled_send(html, run_date, deal_breaker_count=0):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import smtplib
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{label}] Job Alert Digest — {run_date}"
        msg["From"]    = os.getenv("GMAIL_ADDRESS", "")
        msg["To"]      = os.getenv("RECIPIENT_EMAIL", os.getenv("GMAIL_ADDRESS", ""))
        msg.attach(MIMEText(html, "html"))
        pw = os.getenv("GMAIL_APP_PASSWORD")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(msg["From"], pw)
            smtp.send_message(msg)
        log.info(f"Digest sent → {msg['To']}  subject: {msg['Subject']}")

    if not dry_run:
        render_mod.send_digest = _labelled_send

    since_dt = datetime.datetime.now() - datetime.timedelta(days=lookback_days)
    try:
        pipeline_mod.run_pipeline(
            since_dt=since_dt,
            score_all=True,
            dry_run=dry_run,
        )
    except SystemExit as e:
        log.warning(f"Pipeline exited: {e}")
    finally:
        render_mod.send_digest = _orig_send
        os.environ.pop("SCOREROLE_PROFILE", None)
        os.environ.pop("SCOREROLE_DATA_DIR", None)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run scorerole end-to-end for each persona. Real pipeline is never touched.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Groups: pm, designer, mle\n\nExample:\n  python run_persona_test.py --personas pm --dry-run",
    )
    parser.add_argument("--lookback", type=int, default=7, metavar="DAYS",
                        help="Days to look back for emails (default: 7)")
    parser.add_argument("--personas", nargs="+", metavar="GROUP",
                        help="Run only these groups: pm, designer, mle (default: all)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Score and render but skip email send and state writes")
    args = parser.parse_args()

    wanted_groups = set(args.personas) if args.personas else None
    personas = [
        (profile_dir, data_dir, label)
        for group, profile_dir, data_dir, label in _PERSONA_REGISTRY
        if wanted_groups is None or group in wanted_groups
    ]

    if not personas:
        log.error(f"No personas matched groups: {args.personas}")
        sys.exit(1)

    missing = [
        str(profile_dir / "profile.yaml")
        for profile_dir, _, _ in personas
        if not (profile_dir / "profile.yaml").exists()
    ]
    if missing:
        log.error("Missing profile.yaml files:\n  " + "\n  ".join(missing))
        sys.exit(1)

    dry_tag = " [DRY RUN — no email, no state writes]" if args.dry_run else ""
    log.info(f"Running {len(personas)} persona(s), {args.lookback}d lookback{dry_tag}")

    for profile_dir, data_dir, label in personas:
        run_for_persona(
            profile_dir=profile_dir,
            data_dir=data_dir,
            label=label,
            lookback_days=args.lookback,
            dry_run=args.dry_run,
        )

    log.info("Done. Check your inbox for labelled digests.")
    log.info("Your real ~/.job_pipeline/ was never read or modified.")
