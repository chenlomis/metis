from __future__ import annotations

import datetime as dt
import json
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


VALID_STATUSES = {
    "opened", "opened_linkedin", "prefilled", "needs_review", "blocked", "auth_required",
    "applied", "applied_confirmed", "rejected", "recruiter_screen",
}


def data_dir() -> Path:
    return Path(os.environ.get("METIS_DATA_DIR", str(Path.home() / ".job_pipeline"))).expanduser()


def state_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "application_state.json"


def load_application_state(root: Path | None = None) -> dict[str, dict[str, Any]]:
    path = state_path(root)
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def update_application_state(
    role_key: str,
    *,
    status: str,
    root: Path | None = None,
    **fields: Any,
) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"Unknown application status: {status}")
    path = state_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    state = load_application_state(root)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    entry = dict(state.get(role_key) or {})
    entry.update({key: value for key, value in fields.items() if value is not None})
    entry["status"] = status
    entry["updated_at"] = now
    entry.setdefault("created_at", now)
    state[role_key] = entry

    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)
    path.chmod(0o600)
    return entry


def reconcile_application_event(
    company: str,
    role: str | None,
    classification: str,
    *,
    event_date: str | None = None,
    root: Path | None = None,
) -> str | None:
    """Match an email event to browser state and return the updated role key."""
    if not role or classification not in {"confirmation", "rejection", "recruiter_screen"}:
        return None

    def norm(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def company_tokens(value: str) -> set[str]:
        ignored = {"careers", "company", "inc", "llc", "studios", "jobs", "the"}
        return {
            token for token in re.findall(r"[a-z0-9]+", value.lower())
            if len(token) >= 4 and token not in ignored
        }

    state = load_application_state(root)
    best_key: str | None = None
    best_score = 0.0
    for role_key, entry in state.items():
        saved_role = entry.get("role") or {}
        saved_company = str(saved_role.get("company") or "")
        saved_title = str(saved_role.get("title") or "")
        company_score = SequenceMatcher(None, norm(company), norm(saved_company)).ratio()
        role_score = SequenceMatcher(None, norm(role), norm(saved_title)).ratio()
        has_company_token = bool(company_tokens(company) & company_tokens(saved_company))
        company_matches = company_score >= 0.70 or has_company_token
        if company_matches and role_score >= 0.55:
            score = (company_score + role_score) / 2
            if score > best_score:
                best_key, best_score = role_key, score
    if best_key is None:
        return None
    status = {
        "confirmation": "applied_confirmed",
        "rejection": "rejected",
        "recruiter_screen": "recruiter_screen",
    }[classification]
    update_application_state(
        best_key, status=status, root=root,
        email_evidence={"classification": classification, "date": event_date},
    )
    return best_key
