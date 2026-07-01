"""Outlook OAuth2 via Microsoft Graph API — auth flow, token storage, fetch, and send.

Covers personal Microsoft accounts (outlook.com, hotmail.com, live.com).
Does NOT target enterprise M365/Exchange — this is a personal tool.

Token stored at ~/.job_pipeline/outlook_token.json (chmod 600).

Required Azure setup (one-time, developer-side):
  - Register an app at portal.azure.com → App registrations
  - Supported account types: "Personal Microsoft accounts only"
  - Add redirect URI: http://localhost:8766/oauth/callback (Mobile and desktop applications)
  - Set OUTLOOK_CLIENT_ID in .env (no client secret needed for public desktop apps)

Scopes requested:
  - Mail.Read    — read job alert emails
  - Mail.Send    — send digest back to the user
  - offline_access — required to get a refresh token
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

TOKEN_PATH = Path.home() / ".job_pipeline" / "outlook_token.json"

_AUTH_URL      = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
_TOKEN_URL     = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
_GRAPH_BASE    = "https://graph.microsoft.com/v1.0"
_SCOPES        = ["Mail.Read", "Mail.Send", "offline_access"]
_REDIRECT_PORT = 8766
_REDIRECT_URI  = f"http://localhost:{_REDIRECT_PORT}/oauth/callback"


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
    client_id = os.getenv("OUTLOOK_CLIENT_ID", "")
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "refresh_token": token["refresh_token"],
        "client_id":     client_id,
        "scope":         " ".join(_SCOPES),
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
        raise RuntimeError("Outlook not connected. Run 'metis init' to authenticate.")
    return _refresh_access_token(token)["access_token"]


# ── OAuth flow ────────────────────────────────────────────────────────────────

def _run_oauth_flow() -> dict:
    """Open browser, run localhost callback server, exchange code for tokens."""
    client_id = os.getenv("OUTLOOK_CLIENT_ID", "")
    if not client_id:
        raise RuntimeError(
            "OUTLOOK_CLIENT_ID must be set in .env.\n"
            "Register an app at portal.azure.com → App registrations."
        )

    auth_params = {
        "client_id":     client_id,
        "redirect_uri":  _REDIRECT_URI,
        "response_type": "code",
        "scope":         " ".join(_SCOPES),
        "response_mode": "query",
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
                    b"<h2>Metis connected to Outlook</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
            else:
                received["error"] = qs.get("error", ["unknown"])[0]
                self.send_response(400)
                self.end_headers()

        def log_message(self, *_):
            pass

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

    resp = requests.post(_TOKEN_URL, data={
        "code":          received["code"],
        "client_id":     client_id,
        "redirect_uri":  _REDIRECT_URI,
        "grant_type":    "authorization_code",
        "scope":         " ".join(_SCOPES),
    }, timeout=10)
    resp.raise_for_status()
    token = resp.json()
    _save_token(token)
    return token


def connect() -> str:
    """Run OAuth flow and return the authenticated email address."""
    token = _run_oauth_flow()
    resp = requests.get(
        f"{_GRAPH_BASE}/me",
        headers={"Authorization": f"Bearer {token['access_token']}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("mail") or data.get("userPrincipalName", "")


def is_connected() -> bool:
    return TOKEN_PATH.exists()


# ── Microsoft Graph: read ─────────────────────────────────────────────────────

def fetch_emails(sender: str, since_dt) -> list[dict]:
    """Fetch emails from a specific sender since since_dt via Microsoft Graph.

    Returns [{"text": ..., "html": ...}] — same shape as the Gmail/IMAP fetcher.
    """
    access_token = get_access_token()
    headers = {"Authorization": f"Bearer {access_token}"}

    date_str = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    filter_q = f"from/emailAddress/address eq '{sender}' and receivedDateTime ge {date_str}"

    resp = requests.get(
        f"{_GRAPH_BASE}/me/messages",
        headers=headers,
        params={
            "$filter":  filter_q,
            "$select":  "id,subject,body,uniqueBody",
            "$top":     50,
            "$orderby": "receivedDateTime desc",
        },
        timeout=10,
    )
    resp.raise_for_status()

    results = []
    for msg in resp.json().get("value", []):
        body = msg.get("body", {})
        content_type = body.get("contentType", "").lower()
        content      = body.get("content", "")
        results.append({
            "html": content if content_type == "html" else "",
            "text": content if content_type == "text" else "",
        })

    return results


# ── Microsoft Graph: send ─────────────────────────────────────────────────────

def send_digest(html: str, subject: str, recipient: str) -> None:
    """Send the digest HTML via Microsoft Graph API."""
    access_token = get_access_token()

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html},
            "toRecipients": [{"emailAddress": {"address": recipient}}],
        },
        "saveToSentItems": "true",
    }

    resp = requests.post(
        f"{_GRAPH_BASE}/me/sendMail",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    log.info("Digest sent via Microsoft Graph to %s", recipient)
