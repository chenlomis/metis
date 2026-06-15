import os, re, json, datetime, hashlib
from pathlib import Path

DATA_DIR  = Path.home() / ".job_pipeline"
LOG_DIR   = DATA_DIR / "logs"
SEEN_FILE = DATA_DIR / "seen_roles.json"   # canonical dedup store


def _role_hash(title: str, company: str) -> str:
    """Stable 12-char hash from normalized title + company."""
    key = re.sub(r"[^a-z0-9]", "", (title + company).lower())
    return hashlib.md5(key.encode()).hexdigest()[:12]


import logging as _logging
_log = _logging.getLogger(__name__)


def _read_seen_json(p: Path) -> dict:
    """Read seen_roles.json safely — returns {} on missing, empty, or corrupt file."""
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        if isinstance(data, dict):
            return data
        _log.warning("seen_roles.json has unexpected format — treating as empty")
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("seen_roles.json is corrupted (%s) — starting fresh. "
                     "Run `scorerole reset` to clear it manually if this persists.", exc)
        return {}


def load_seen_roles(ttl_days: int = 14) -> set:
    """Return hashes of roles seen within the TTL window."""
    p = DATA_DIR / "seen_roles.json"
    raw = _read_seen_json(p)
    if not raw:
        return set()
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              .replace(tzinfo=None) - datetime.timedelta(days=ttl_days))
    return {h for h, ts in raw.items()
            if datetime.datetime.fromisoformat(ts) > cutoff}


def save_seen_roles(new_entries: dict, ttl_days: int = 14):
    """Merge new {hash: iso_timestamp} entries into the store, pruning expired ones.

    Prunes on every write so the file stays bounded to ~14 days of roles
    rather than growing unboundedly over months of daily use.
    """
    p = DATA_DIR / "seen_roles.json"
    existing = _read_seen_json(p)
    existing.update(new_entries)
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              .replace(tzinfo=None) - datetime.timedelta(days=ttl_days)).isoformat()
    pruned = {h: ts for h, ts in existing.items() if ts > cutoff}
    p.write_text(json.dumps(pruned))
    p.chmod(0o600)   # profile-equivalent sensitivity — restrict to owner only
