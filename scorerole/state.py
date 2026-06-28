from __future__ import annotations
import os, re, json, datetime, hashlib
from pathlib import Path

DATA_DIR      = Path(os.environ["SCOREROLE_DATA_DIR"]) if "SCOREROLE_DATA_DIR" in os.environ else Path.home() / ".job_pipeline"
LOG_DIR       = DATA_DIR / "logs"
SEEN_FILE     = DATA_DIR / "seen_roles.json"     # canonical dedup store
SKIPPED_FILE  = DATA_DIR / "skipped_roles.json"  # metadata for skipped roles (backport store)
LAST_RUN_FILE = DATA_DIR / "last_run.json"        # summary of most recent pipeline run
QUEUE_FILE    = DATA_DIR / "role_queue.json"      # roles capped out of prior runs, awaiting scoring
FEEDBACK_FILE     = DATA_DIR / "feedback.md"          # user calibration notes (appended via `scorerole feedback`)
FEEDBACK_LOG_FILE = DATA_DIR / "feedback_log.jsonl"   # structured audit log of parsed feedback entries
RUNS_PATH         = DATA_DIR / "runs.jsonl"            # per-job trace records written by trace.py
SKIPPED_TTL_DAYS = 90


def _normalize_company(name: str) -> str:
    """Strip trailing legal/branding suffixes so 'NVIDIA AI' and 'NVIDIA' hash identically."""
    return re.sub(
        r"\s+(ai|inc\.?|corp\.?|ltd\.?|llc|group|holdings|corporation|technologies?|co\.)$",
        "", name.strip(), flags=re.IGNORECASE,
    )


def _role_hash(title: str, company: str) -> str:
    """Stable 12-char hash from normalized title + company."""
    key = re.sub(r"[^a-z0-9]", "", (title + _normalize_company(company)).lower())
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


def load_seen_roles(ttl_days: int = 30) -> set:
    """Return hashes of roles seen within the TTL window."""
    p = DATA_DIR / "seen_roles.json"
    raw = _read_seen_json(p)
    if not raw:
        return set()
    cutoff = (datetime.datetime.now(datetime.timezone.utc)
              .replace(tzinfo=None) - datetime.timedelta(days=ttl_days))
    return {h for h, ts in raw.items()
            if datetime.datetime.fromisoformat(ts) > cutoff}


def save_skipped_roles(jobs: list[dict]) -> None:
    """Persist metadata for skipped-verdict roles so they can be backported later.

    Keyed by role_hash (same as seen_roles). Entries expire after SKIPPED_TTL_DAYS
    (90 days) — long enough that a delayed application can still be matched.
    Only writes; never removes entries until they expire or are promoted to a tracker row.
    """
    if not jobs:
        return
    p = SKIPPED_FILE
    existing: dict = {}
    if p.exists():
        try:
            existing = json.loads(p.read_text())
            if not isinstance(existing, dict):
                existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}

    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
    for job in jobs:
        h = _role_hash(job["title"], job["company"])
        existing[h] = {
            "role_title":    job["title"],
            "company":       job["company"],
            "match_score":   job.get("eval", {}).get("score"),
            "date_suggested": now_iso[:10],   # YYYY-MM-DD
            "url":           job.get("url", ""),
            "saved_at":      now_iso,
        }

    cutoff = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
              - datetime.timedelta(days=SKIPPED_TTL_DAYS)).isoformat()
    pruned = {h: v for h, v in existing.items() if v.get("saved_at", "") > cutoff}

    p.write_text(json.dumps(pruned, indent=2))
    p.chmod(0o600)


def lookup_skipped_role(title: str, company: str) -> dict | None:
    """Return stored metadata for a skipped role, or None if not found / expired."""
    if not SKIPPED_FILE.exists():
        return None
    try:
        data = json.loads(SKIPPED_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data.get(_role_hash(title, company))


def promote_skipped_role(title: str, company: str) -> None:
    """Remove a skipped role from the sidecar once it has been promoted to a tracker row."""
    if not SKIPPED_FILE.exists():
        return
    try:
        data = json.loads(SKIPPED_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return
    h = _role_hash(title, company)
    if h in data:
        del data[h]
        SKIPPED_FILE.write_text(json.dumps(data, indent=2))
        SKIPPED_FILE.chmod(0o600)


def save_seen_roles(new_entries: dict, ttl_days: int = 30):
    """Merge new {hash: iso_timestamp} entries into the store, pruning expired ones.

    Prunes on every write so the file stays bounded to ~30 days of roles
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


def load_role_queue() -> list[dict]:
    """Load roles staged from prior capped runs. Returns [] if file missing or corrupt."""
    if not QUEUE_FILE.exists():
        return []
    try:
        data = json.loads(QUEUE_FILE.read_text())
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError) as exc:
        _log.warning("role_queue.json unreadable (%s) — starting with empty queue", exc)
        return []


def save_role_queue(roles: list[dict]) -> None:
    """Persist excess roles for next run. Pass [] to clear the queue."""
    QUEUE_FILE.write_text(json.dumps(roles, indent=2))
    QUEUE_FILE.chmod(0o600)
