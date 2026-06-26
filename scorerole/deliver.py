"""scorerole/deliver.py — email delivery (SMTP send).

Owns the side-effectful half of digest delivery: credentials, SMTP
connection, and send. Pure HTML generation lives in render.py.

Keeping these separate means render.py is fully testable without
credentials, and deliver.py is the only module that touches smtplib.
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from .config import Config

log = logging.getLogger(__name__)


def send_digest(html: str, run_date: str, label: str = "", *, config: Config) -> None:
    """Send the HTML digest via Gmail SMTP.

    Raises smtplib.SMTPException on delivery failure — caller (pipeline.py)
    is responsible for deciding whether to propagate or handle. State is
    intentionally saved AFTER this call (T-07) so a failure doesn't lose roles.
    """
    msg = MIMEMultipart("alternative")
    prefix = f"[{label}] " if label else ""
    msg["Subject"] = f"{prefix}Personalized Job Alert Digest — {run_date}"
    msg["From"]    = config.gmail_address
    msg["To"]      = config.recipient_email
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(config.gmail_address, config.gmail_app_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        log.error("DIGEST NOT DELIVERED — Gmail authentication failed. Check GMAIL_APP_PASSWORD in .env: %s", e)
        raise
    except smtplib.SMTPException as e:
        log.error("DIGEST NOT DELIVERED — SMTP error: %s", e)
        raise
    log.info("Digest sent to %s", config.recipient_email)
