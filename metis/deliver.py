"""metis/deliver.py — email delivery (SMTP send).

Owns the side-effectful half of digest delivery: credentials, SMTP
connection, and send. Pure HTML generation lives in render.py.

Keeping these separate means render.py is fully testable without
credentials, and deliver.py is the only module that touches smtplib.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger(__name__)

GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", GMAIL_ADDRESS)


def send_digest(html: str, run_date: str, label: str = "", job_count: int = 0) -> None:
    """Send the HTML digest via Gmail SMTP.

    Raises smtplib.SMTPException on delivery failure — caller (pipeline.py)
    is responsible for deciding whether to propagate or handle. State is
    intentionally saved AFTER this call so a failure doesn't lose roles.
    """
    msg = MIMEMultipart("alternative")
    prefix = f"[{label}] " if label else ""
    role_phrase = f"{job_count} new role{'s' if job_count != 1 else ''}" if job_count else "new roles"
    msg["Subject"] = f"{prefix}Metis Digest — {role_phrase} for you — {run_date}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        log.error("DIGEST NOT DELIVERED — Gmail authentication failed. Check GMAIL_APP_PASSWORD in .env: %s", e)
        raise
    except smtplib.SMTPException as e:
        log.error("DIGEST NOT DELIVERED — SMTP error: %s", e)
        raise
    log.info("Digest sent to %s", RECIPIENT_EMAIL)
