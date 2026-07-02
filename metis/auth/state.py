"""Shared email-auth state."""
from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path.home() / ".job_pipeline"
ACTIVE_PROVIDER_PATH = DATA_DIR / "email_provider.json"
VALID_PROVIDERS = {"gmail_oauth", "outlook_oauth"}


def _provider_token_path(provider: str) -> Path:
    if provider == "gmail_oauth":
        return DATA_DIR / "gmail_token.json"
    if provider == "outlook_oauth":
        return DATA_DIR / "outlook_token.json"
    raise ValueError(f"Unknown email provider: {provider}")


def set_active_provider(provider: str) -> None:
    """Persist the latest successfully connected OAuth provider."""
    if provider not in VALID_PROVIDERS:
        raise ValueError(f"Unknown email provider: {provider}")
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    ACTIVE_PROVIDER_PATH.write_text(json.dumps({"provider": provider}, indent=2))
    ACTIVE_PROVIDER_PATH.chmod(0o600)


def get_active_provider() -> str | None:
    """Return the active OAuth provider when its token still exists."""
    try:
        data = json.loads(ACTIVE_PROVIDER_PATH.read_text())
    except Exception:
        return None
    provider = data.get("provider")
    if provider in VALID_PROVIDERS and _provider_token_path(provider).exists():
        return provider
    return None


def infer_connected_provider() -> str | None:
    """Return active provider, or newest connected provider for old installs."""
    active = get_active_provider()
    if active:
        return active

    existing = [
        provider
        for provider in ("gmail_oauth", "outlook_oauth")
        if _provider_token_path(provider).exists()
    ]
    if not existing:
        return None
    return max(existing, key=lambda provider: _provider_token_path(provider).stat().st_mtime)


def provider_label(provider: str) -> str:
    return {
        "gmail_oauth": "Gmail",
        "outlook_oauth": "Outlook",
    }.get(provider, provider)


def provider_token_path(provider: str) -> Path:
    return _provider_token_path(provider)
