from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any


VALID_STATUSES = {
    "opened", "prefilled", "needs_review", "blocked", "applied", "applied_confirmed", "rejected", "recruiter_screen",
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
