"""
Persona test runner — runs the full pipeline for each persona profile without
touching ~/.job_pipeline/profile.yaml. Uses SCOREROLE_PROFILE env var to point
at a different profile for each run.

Usage:  python run_persona_test.py [--lookback DAYS]

Your real profile.yaml is never modified. Safe to Ctrl-C at any time.
"""
import argparse, datetime, logging, os, sys
from pathlib import Path

DATA_DIR = Path.home() / ".job_pipeline"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("persona_test")


def run_for_persona(profile_src: Path, label: str, lookback_days: int = 7):
    log.info("=" * 60)
    log.info(f"PERSONA: {label}")
    log.info("=" * 60)

    if not profile_src.exists():
        log.error(f"Profile not found: {profile_src}")
        return False

    # Point the profile loader at this persona's file — no file swap needed.
    os.environ["SCOREROLE_PROFILE"] = str(profile_src)

    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)
    # Re-apply override after dotenv (dotenv won't clobber existing env vars
    # with override=True, but re-set defensively)
    os.environ["SCOREROLE_PROFILE"] = str(profile_src)

    import importlib
    import scorerole.profile as profile_mod
    importlib.reload(profile_mod)
    import scorerole.pipeline as pipeline_mod
    importlib.reload(pipeline_mod)
    import scorerole.score as score_mod
    importlib.reload(score_mod)
    import scorerole.render as render_mod
    importlib.reload(render_mod)

    _orig_send = render_mod.send_digest

    def _labelled_send(html, run_date, deal_breaker_count=0):
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import smtplib
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{label}] Personalized Job Alert Digest — {run_date}"
        msg["From"]    = os.getenv("GMAIL_ADDRESS", "")
        msg["To"]      = os.getenv("RECIPIENT_EMAIL", os.getenv("GMAIL_ADDRESS", ""))
        msg.attach(MIMEText(html, "html"))
        pw = os.getenv("GMAIL_APP_PASSWORD")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(msg["From"], pw)
            smtp.send_message(msg)
        log.info(f"Digest sent → {msg['To']}  subject: {msg['Subject']}")

    render_mod.send_digest = _labelled_send

    since_dt = datetime.datetime.now() - datetime.timedelta(days=lookback_days)
    try:
        pipeline_mod.run_pipeline(since_dt=since_dt, score_all=True, no_tracker=True)
    except SystemExit as e:
        log.warning(f"Pipeline exited: {e}")
    finally:
        render_mod.send_digest = _orig_send
        os.environ.pop("SCOREROLE_PROFILE", None)

    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run scorerole end-to-end for each persona profile.")
    parser.add_argument("--lookback", type=int, default=7, metavar="DAYS",
                        help="How many days back to fetch emails (default: 7)")
    args = parser.parse_args()

    personas = [
        (DATA_DIR / "profile_ml_eng.yaml",  "ML Engineer — Alex Rivera"),
        (DATA_DIR / "profile_designer.yaml", "Designer — Jordan Lee"),
    ]

    # Optionally add PM persona if a profile exists for it
    pm_profile = DATA_DIR / "profile_pm.yaml"
    if pm_profile.exists():
        personas.insert(0, (pm_profile, "PM — Lomis Chen (test copy)"))

    missing = [str(p) for p, _ in personas if not p.exists()]
    if missing:
        log.error("Missing persona profiles:\n  " + "\n  ".join(missing))
        sys.exit(1)

    for profile_src, label in personas:
        run_for_persona(profile_src, label, lookback_days=args.lookback)

    log.info("Done. Check your inbox for labelled digests.")
    log.info("Your real profile.yaml was never modified.")
