"""scorerole/track_imap.py — IMAP fetch layer for candidate emails.

Owns: IMAP connection, email decode helpers, fetch_candidate_emails().
No classification logic lives here — that belongs in track_parse.py.
"""
from __future__ import annotations

import datetime
import email as email_lib
import imaplib
import logging
import re
import time
from email.header import decode_header

log = logging.getLogger(__name__)

_IMAP_MAX_RETRIES = 3
_IMAP_RETRY_DELAY = 30   # seconds between retries on transient network errors

# ATS platform domains to search by sender — catches ATS emails regardless of subject.
# These are the @domain portions used in FROM IMAP searches.
_ATS_FROM_DOMAINS = [
    "greenhouse.io", "greenhouse-mail.io",
    "lever.co",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "ashbyhq.com",
    "icims.com",
    "jobvite.com",
    "bamboohr.com",
    "workday.com",
    "applytojob.com",
    "successfactors.com",
    "taleo.net",
]


# ---------------------------------------------------------------------------
# Decode helpers
# ---------------------------------------------------------------------------

def _decode_header_value(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        parts = decode_header(raw.decode("utf-8", errors="replace"))
    else:
        parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    import html as html_lib
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_decode(payload: bytes, charset: str | None) -> str:
    """Decode bytes to str, falling back gracefully on unknown/binary charsets."""
    for enc in (charset, "utf-8", "latin-1"):
        if not enc or enc.lower() in ("binary", "unknown", "x-unknown"):
            continue
        try:
            return payload.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("latin-1", errors="replace")


def _extract_body(msg) -> str:
    """Extract plain-text body from an email.Message object.

    Prefers text/plain parts. Falls back to stripping text/html when no plain
    text is available — required for Amazon, Netflix, Google DeepMind, etc.
    """
    plain_parts = []
    html_parts  = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = _safe_decode(payload, part.get_content_charset())
            if ct == "text/plain":
                plain_parts.append(decoded)
            elif ct == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = _safe_decode(payload, msg.get_content_charset())
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return _strip_html("\n".join(html_parts))
    return ""


def _normalize_company_search(company: str) -> str:
    """Normalize a company name for use in an IMAP FROM substring search.

    Strips legal suffixes, punctuation, and excess whitespace so that
    'Sigma Computing, Inc.' becomes 'sigma computing' and matches
    any sender whose address or display name contains that string.
    """
    term = re.sub(r",?\s+(?:inc|llc|corp|ltd|co)\.?$", "", company, flags=re.IGNORECASE)
    term = re.sub(r"[^\w\s-]", "", term).strip().lower()
    return term


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------

def fetch_candidate_emails(
    gmail_address: str,
    app_password: str,
    since_dt: datetime.datetime,
    applied_companies: "set[str] | None" = None,
) -> list[dict]:
    """Fetch emails from Gmail that might be confirmations, rejections, or recruiter screens.

    Three-tier server-side fetch gate (all run inside one IMAP connection, results unioned):
      1. Subject keyword search — fast path for ATS-generated emails with predictable subjects.
      2. ATS sender domain search — catches ATS emails with non-standard subjects.
      3. Company name FROM search — catches direct recruiter outreach from company domains,
         which have no predictable subject pattern.
    Full RFC822 bodies are only fetched for the union of matched message IDs.
    """
    from email.utils import parsedate_to_datetime

    since_str = since_dt.strftime("%d-%b-%Y")

    _SUBJECT_SEARCHES = [
        "thank you for applying",
        "thanks for applying",
        "thank you for your application",
        "thanks for your application",
        "your application for",
        "thanks for your interest",
        "thank you for your interest",
        "keep track of your application",
        "we've received your application",
        "we received your application",
        "we got it",
        "next steps",
        "following up on your",
        "application update",
        "update on your",
        "thank you from",
        "application feedback",
        "your application to",
        "phone screen",
        "hello from",
        "application follow up",
        "we look forward to",
    ]

    emails = []

    for attempt in range(1, _IMAP_MAX_RETRIES + 1):
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
                imap.login(gmail_address, app_password)
                imap.select("INBOX")

                matching_ids: set[bytes] = set()

                for term in _SUBJECT_SEARCHES:
                    _, data = imap.search(None, f'SINCE {since_str} SUBJECT "{term}"')
                    if data and data[0]:
                        matching_ids.update(data[0].split())

                for domain in _ATS_FROM_DOMAINS:
                    _, data = imap.search(None, f'SINCE {since_str} FROM "@{domain}"')
                    if data and data[0]:
                        matching_ids.update(data[0].split())

                if applied_companies:
                    for company in applied_companies:
                        term = _normalize_company_search(company)
                        if len(term) >= 4:
                            _, data = imap.search(None, f'SINCE {since_str} FROM "{term}"')
                            if data and data[0]:
                                matching_ids.update(data[0].split())

                if not matching_ids:
                    log.info("track: no candidate emails found since %s", since_str)
                    return []

                log.info("track: %d subject-matched emails — fetching bodies…", len(matching_ids))

                for msg_id in sorted(matching_ids):
                    _, raw = imap.fetch(msg_id, "(RFC822)")
                    if not raw or not raw[0]:
                        continue
                    raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else raw[0]
                    msg = email_lib.message_from_bytes(raw_bytes)

                    subject     = _decode_header_value(msg.get("Subject", ""))
                    sender      = _decode_header_value(msg.get("From", ""))
                    date_header = msg.get("Date", "")

                    if re.search(r"\bout of office\b|auto.?reply|automatic reply|autoreply", subject, re.IGNORECASE):
                        log.debug("track: skipping OOO/auto-reply — %s", subject[:80])
                        continue
                    if msg.get("X-Auto-Submitted", "").lower() not in ("", "no"):
                        log.debug("track: skipping auto-submitted — %s", subject[:80])
                        continue

                    try:
                        email_date = parsedate_to_datetime(date_header).date().isoformat()
                    except Exception:
                        email_date = datetime.date.today().isoformat()

                    body = _extract_body(msg)
                    log.debug("track: fetched — %s | from: %s", subject[:80], sender[:50])
                    emails.append({
                        "subject": subject,
                        "sender":  sender,
                        "body":    body,
                        "date":    email_date,
                    })
            break
        except OSError as e:
            if attempt < _IMAP_MAX_RETRIES:
                log.warning(
                    "track: IMAP connect failed (attempt %d/%d): %s — retrying in %ds…",
                    attempt, _IMAP_MAX_RETRIES, e, _IMAP_RETRY_DELAY,
                )
                time.sleep(_IMAP_RETRY_DELAY)
            else:
                log.error("track: IMAP connect failed after %d attempts: %s", _IMAP_MAX_RETRIES, e)
                return []

    log.info("track: fetched %d candidate emails since %s", len(emails), since_str)
    return emails
