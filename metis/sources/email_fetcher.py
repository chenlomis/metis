"""EmailFetcher — provider-agnostic interface for fetching job alert emails.

Routes to the configured provider:
  - "gmail_oauth"   → Gmail API (gmail_oauth.py)
  - "outlook_oauth" → Microsoft Graph (outlook_oauth.py)
  - "imap"          → legacy Gmail IMAP + app password (fallback)

The downstream parsers (_parse_wellfound, _parse_ladders, etc.) only ever
see [{"text": ..., "html": ...}] dicts — they are completely unaware of
which provider fetched the email.
"""
from __future__ import annotations

import datetime
import imaplib
import logging
import email as _email_lib
import os

log = logging.getLogger(__name__)


def get_provider() -> str:
    """Return the active email provider based on what's configured/connected."""
    from pathlib import Path
    gmail_token   = Path.home() / ".job_pipeline" / "gmail_token.json"
    outlook_token = Path.home() / ".job_pipeline" / "outlook_token.json"

    # Explicit override
    override = os.getenv("METIS_EMAIL_PROVIDER", "").lower()
    if override in ("gmail_oauth", "outlook_oauth", "imap"):
        return override

    # Auto-detect from stored tokens
    if gmail_token.exists():
        return "gmail_oauth"
    if outlook_token.exists():
        return "outlook_oauth"

    # Fallback: legacy IMAP
    return "imap"


def fetch_emails_from_sender(sender: str, since_dt: datetime.datetime) -> list[dict]:
    """Fetch all emails from a specific sender since since_dt.

    Returns [{"text": str, "html": str}] regardless of provider.
    """
    provider = get_provider()

    if provider == "gmail_oauth":
        from ..auth import gmail_oauth
        return gmail_oauth.fetch_emails(sender, since_dt)

    if provider == "outlook_oauth":
        from ..auth import outlook_oauth
        return outlook_oauth.fetch_emails(sender, since_dt)

    # IMAP fallback
    return _fetch_via_imap(sender, since_dt)


def _fetch_via_imap(sender: str, since_dt: datetime.datetime) -> list[dict]:
    """Legacy IMAP fetch — used when no OAuth token is present."""
    gmail_address      = os.getenv("GMAIL_ADDRESS", "")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "")
    if not gmail_address or not gmail_app_password:
        log.warning("No OAuth token and no GMAIL_APP_PASSWORD — skipping %s", sender)
        return []

    date_str = since_dt.strftime("%d-%b-%Y")
    results  = []
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(gmail_address, gmail_app_password)
            imap.select("INBOX")
            _, data = imap.search(None, f'FROM "{sender}" SINCE "{date_str}"')
            for mid in data[0].split():
                try:
                    _, raw = imap.fetch(mid, "(RFC822)")
                    msg = _email_lib.message_from_bytes(raw[0][1])
                    body_text = body_html = ""
                    for part in msg.walk():
                        ct = part.get_content_type()
                        if ct == "text/plain":
                            body_text = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        elif ct == "text/html":
                            body_html = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    results.append({"text": body_text, "html": body_html})
                except Exception as e:
                    log.warning("IMAP: failed to read message %s from %s: %s", mid, sender, e)
    except imaplib.IMAP4.error as e:
        log.error("IMAP error: %s", e)

    return results


def send_digest(html: str, subject: str, recipient: str) -> None:
    """Send the digest via the active provider."""
    provider = get_provider()

    if provider == "gmail_oauth":
        from ..auth import gmail_oauth
        gmail_oauth.send_digest(html, subject, recipient)
        return

    if provider == "outlook_oauth":
        from ..auth import outlook_oauth
        outlook_oauth.send_digest(html, subject, recipient)
        return

    # IMAP fallback uses existing SMTP deliver.py
    from ..deliver import send_digest as _smtp_send
    _smtp_send(html, subject)
