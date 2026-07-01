"""Gmail OAuth2 — authorization flow, token storage, and authenticated fetch/send.

Replaces GMAIL_APP_PASSWORD for both IMAP reads and SMTP sends.
Token is stored at ~/.job_pipeline/gmail_token.json (chmod 600).

Required Google Cloud setup (one-time, developer-side):
  - Create a project at console.cloud.google.com
  - Enable Gmail API
  - Create OAuth 2.0 credentials (Desktop app)
  - Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env

Scopes requested:
  - gmail.readonly  — read job alert emails
  - gmail.send      — send digest back to the user
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import webbrowser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

log = logging.getLogger(__name__)

TOKEN_PATH = Path.home() / ".job_pipeline" / "gmail_token.json"

_AUTH_URL   = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL  = "https://oauth2.googleapis.com/token"
_SCOPES     = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
_REDIRECT_PORT = 8765
_REDIRECT_URI  = f"http://127.0.0.1:{_REDIRECT_PORT}/oauth/callback"


# ── Token storage ─────────────────────────────────────────────────────────────

def _load_token() -> dict | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        return json.loads(TOKEN_PATH.read_text())
    except Exception:
        return None


def _save_token(token: dict) -> None:
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(token, indent=2))
    TOKEN_PATH.chmod(0o600)


# ── Token refresh ─────────────────────────────────────────────────────────────

def _refresh_access_token(token: dict) -> dict:
    client_id     = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id":     client_id,
        "client_secret": client_secret,
    }, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    token["access_token"] = data["access_token"]
    if "refresh_token" in data:
        token["refresh_token"] = data["refresh_token"]
    _save_token(token)
    return token


def get_access_token() -> str:
    """Return a valid access token, refreshing if necessary."""
    token = _load_token()
    if not token:
        raise RuntimeError("Gmail not connected. Run 'metis init' to authenticate.")
    return _refresh_access_token(token)["access_token"]


# ── OAuth flow ────────────────────────────────────────────────────────────────

def _run_oauth_flow() -> dict:
    """Open browser, run localhost callback server, exchange code for tokens."""
    client_id     = os.getenv("GMAIL_CLIENT_ID", "")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in .env.\n"
            "Create OAuth 2.0 credentials at console.cloud.google.com."
        )

    auth_params = {
        "client_id":             client_id,
        "redirect_uri":          _REDIRECT_URI,
        "response_type":         "code",
        "scope":                 " ".join(_SCOPES),
        "access_type":           "offline",
        "prompt":                "consent",  # ensure refresh_token is always returned
    }
    auth_url = f"{_AUTH_URL}?{urlencode(auth_params)}"

    received: dict = {}
    server_ready = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                received["code"] = qs["code"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:40px'>"
                    b"<h2>Metis connected to Gmail</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
            else:
                received["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_):
            pass  # suppress HTTP server logs

    httpd = HTTPServer(("127.0.0.1", _REDIRECT_PORT), _Handler)

    def _serve():
        server_ready.set()
        httpd.handle_request()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    server_ready.wait()

    webbrowser.open(auth_url)

    t.join(timeout=120)
    if "error" in received:
        raise RuntimeError(f"OAuth error: {received['error']}")
    if "code" not in received:
        raise RuntimeError("OAuth timed out — no response from browser.")

    # Exchange auth code for tokens
    resp = requests.post(_TOKEN_URL, data={
        "code":          received["code"],
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  _REDIRECT_URI,
        "grant_type":    "authorization_code",
    }, timeout=10)
    resp.raise_for_status()
    token = resp.json()
    _save_token(token)
    return token


def connect() -> str:
    """Run OAuth flow and return the authenticated Gmail address."""
    token = _run_oauth_flow()
    access_token = token["access_token"]
    # Fetch the user's email address from the Gmail profile endpoint
    resp = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/profile",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json().get("emailAddress", "")


def is_connected() -> bool:
    return TOKEN_PATH.exists()


# ── Gmail API: read ───────────────────────────────────────────────────────────

def fetch_emails(sender: str, since_dt) -> list[dict]:
    """Fetch emails from a specific sender since since_dt via Gmail API.

    Returns [{"text": ..., "html": ...}] — same shape as the IMAP fetcher.
    """
    import datetime
    access_token = get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    date_str = since_dt.strftime("%Y/%m/%d")
    query    = f"from:{sender} after:{date_str}"

    resp = requests.get(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
        headers=headers,
        params={"q": query, "maxResults": 50},
        timeout=10,
    )
    resp.raise_for_status()
    message_ids = [m["id"] for m in resp.json().get("messages", [])]

    results = []
    for mid in message_ids:
        try:
            msg_resp = requests.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                headers=headers,
                params={"format": "full"},
                timeout=10,
            )
            msg_resp.raise_for_status()
            results.append(_extract_body(msg_resp.json()))
        except Exception as e:
            log.warning("Gmail API: failed to fetch message %s: %s", mid, e)

    return results


def _extract_body(msg: dict) -> dict:
    """Extract text/plain and text/html parts from a Gmail API message."""
    body_text = body_html = ""

    def _walk(parts):
        nonlocal body_text, body_html
        for part in parts:
            mime = part.get("mimeType", "")
            if mime == "text/plain" and not body_text:
                data = part.get("body", {}).get("data", "")
                body_text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            elif mime == "text/html" and not body_html:
                data = part.get("body", {}).get("data", "")
                body_html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            if "parts" in part:
                _walk(part["parts"])

    payload = msg.get("payload", {})
    if "parts" in payload:
        _walk(payload["parts"])
    else:
        data = payload.get("body", {}).get("data", "")
        if data:
            body_text = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    return {"text": body_text, "html": body_html}


# ── Gmail API: send ───────────────────────────────────────────────────────────

def send_digest(html: str, subject: str, recipient: str) -> None:
    """Send the digest HTML via Gmail API (replaces SMTP + app password)."""
    access_token = get_access_token()

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["To"]      = recipient
    msg.attach(MIMEText(html, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    resp = requests.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        json={"raw": raw},
        timeout=15,
    )
    resp.raise_for_status()
    log.info("Digest sent via Gmail API to %s", recipient)
