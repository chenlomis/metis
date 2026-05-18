import os, re, json, datetime, hashlib
from pathlib import Path

DATA_DIR  = Path.home() / ".job_pipeline"
LOG_DIR   = DATA_DIR / "logs"
SEEN_FILE = DATA_DIR / "seen_ids.json"


def load_seen_ids() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen_ids(ids: set):
    SEEN_FILE.write_text(json.dumps(list(ids)))


def _role_hash(title: str, company: str) -> str:
    """Stable 12-char hash from normalized title + company."""
    key = re.sub(r"[^a-z0-9]", "", (title + company).lower())
    return hashlib.md5(key.encode()).hexdigest()[:12]


def load_seen_roles(ttl_days: int = 14) -> set:
    """Return hashes of roles seen within the TTL window."""
    p = DATA_DIR / "seen_roles.json"
    if not p.exists():
        return set()
    raw = json.loads(p.read_text())
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              .replace(tzinfo=None) - datetime.timedelta(days=ttl_days))
    return {h for h, ts in raw.items()
            if datetime.datetime.fromisoformat(ts) > cutoff}


def save_seen_roles(new_entries: dict):
    """Merge new {hash: iso_timestamp} entries into the store."""
    p = DATA_DIR / "seen_roles.json"
    existing = json.loads(p.read_text()) if p.exists() else {}
    existing.update(new_entries)
    p.write_text(json.dumps(existing))
