"""Shared OAuth security helpers for local browser auth flows."""
from __future__ import annotations

import base64
import hashlib
import secrets


def generate_state() -> str:
    """Return a random OAuth state value for CSRF protection."""
    return secrets.token_urlsafe(32)


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def extract_callback_code(query: dict[str, list[str]], expected_state: str) -> str:
    """Validate callback state and return the authorization code.

    Raises RuntimeError for denied consent, state mismatch, or missing code.
    """
    returned_state = query.get("state", [""])[0]
    if not returned_state or not secrets.compare_digest(returned_state, expected_state):
        raise RuntimeError("OAuth state mismatch — login rejected.")

    if "error" in query:
        error = query.get("error", ["unknown"])[0]
        description = query.get("error_description", [""])[0]
        detail = f": {description}" if description else ""
        raise RuntimeError(f"OAuth error: {error}{detail}")

    code = query.get("code", [""])[0]
    if not code:
        raise TimeoutError("OAuth timed out — no authorization code from browser.")
    return code
