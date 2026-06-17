"""
Persona test runner — swaps profile.yaml, resets seen_roles, runs the pipeline,
then restores original state. Sends labelled digest emails for each persona.

Usage:  python run_persona_test.py
"""
import shutil, datetime, logging, sys
from pathlib import Path

DATA_DIR   = Path.home() / ".job_pipeline"
PROFILE    = DATA_DIR / "profile.yaml"
SEEN       = DATA_DIR / "seen_roles.json"
BACKUP_P   = DATA_DIR / "profile.yaml.real_backup"
BACKUP_S   = DATA_DIR / "seen_roles.json.real_backup"

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

    # Swap profile
    shutil.copy(profile_src, PROFILE)
    PROFILE.chmod(0o600)

    # Clear seen_roles so the same emails re-score under this profile
    SEEN.unlink(missing_ok=True)

    # Import pipeline fresh (profile is re-read at scoring time, so no stale state)
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)

    import importlib
    import scorerole.pipeline as pipeline_mod
    importlib.reload(pipeline_mod)

    import scorerole.score as score_mod
    importlib.reload(score_mod)

    # Monkey-patch send_digest to inject the label into the subject
    import scorerole.render as render_mod
    importlib.reload(render_mod)
    _orig_send = render_mod.send_digest

    def _labelled_send(html, run_date, deal_breaker_count=0):
        _orig_send.__wrapped__ = True
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        import smtplib, os
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
        pipeline_mod.run_pipeline(since_dt=since_dt, score_all=True)
    except SystemExit as e:
        log.warning(f"Pipeline exited: {e}")
    finally:
        render_mod.send_digest = _orig_send


def restore():
    log.info("Restoring original profile and seen_roles...")
    if BACKUP_P.exists():
        shutil.copy(BACKUP_P, PROFILE)
        PROFILE.chmod(0o600)
    if BACKUP_S.exists():
        shutil.copy(BACKUP_S, SEEN)
        SEEN.chmod(0o600)
    log.info("Original state restored. Backups kept at *.real_backup")


if __name__ == "__main__":
    personas = [
        (DATA_DIR / "profile_ml_eng.yaml",   "ML Engineer — Alex Rivera"),
        (DATA_DIR / "profile_designer.yaml",  "Designer — Jordan Lee"),
    ]

    for profile_src, label in personas:
        if not profile_src.exists():
            log.error(f"Profile not found: {profile_src}")
            sys.exit(1)
        run_for_persona(profile_src, label, lookback_days=7)

    restore()
    log.info("Done. Check your inbox for 2 labelled digests.")
