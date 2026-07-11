from __future__ import annotations

import datetime
import io
import json
import logging
import os
import secrets
import threading
import time
from contextlib import contextmanager, redirect_stdout, redirect_stderr
from pathlib import Path
from typing import Any

from .read import (
    _default_data_dir,
    _default_profile_path,
    _default_tracker_path,
    _safe_json,
    list_application_activity,
    list_recommended_roles,
)


_FEEDBACK_HEADER = (
    "# Scoring Feedback\n\n"
    "Free-form calibration notes - injected into every scoring run.\n"
    "Add entries via `metis feedback` or edit this file directly.\n"
)

_LEGACY_RUNTIME_LOCK = threading.RLock()


def _feedback_id(today: datetime.date | None = None) -> str:
    day = today or datetime.date.today()
    return f"fb_{day.strftime('%Y%m%d')}_{secrets.token_hex(3)}"


def _clean_meta_list(values: list[str] | None) -> list[str]:
    cleaned = []
    for value in values or []:
        token = " ".join(str(value).split())
        token = token.replace("--", "-").replace("<", "").replace(">", "").replace("|", "/")
        token = token.replace("#", "")
        token = token.replace(",", " ")
        token = " ".join(token.split())
        if token:
            cleaned.append(token)
    return cleaned


@contextmanager
def _exclusive_file_lock(path: Path, *, timeout_seconds: float = 10.0):
    lock_path = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + timeout_seconds
    fd: int | None = None
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for lock: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def record_scoring_feedback(
    text: str,
    *,
    data_dir: str | Path | None = None,
    run_id: str | None = None,
    roles: list[str] | None = None,
    dims: list[str] | None = None,
    feedback_id: str | None = None,
    today: datetime.date | None = None,
) -> dict[str, Any]:
    """Append explicit calibration feedback and audit metadata.

    This intentionally records user-provided feedback as-is. It does not run the
    interactive LLM parse/conflict flow from `metis feedback`.
    """
    body = text.strip()
    if not body:
        raise ValueError("Feedback text cannot be empty.")

    resolved_data_dir = Path(data_dir).expanduser() if data_dir else _default_data_dir()
    feedback_path = resolved_data_dir / "feedback.md"
    log_path = resolved_data_dir / "feedback_log.jsonl"
    resolved_roles = _clean_meta_list(roles)
    resolved_dims = _clean_meta_list(dims)
    resolved_id = feedback_id or _feedback_id(today)
    date_str = (today or datetime.date.today()).isoformat()

    comment = (
        f"<!-- id:{resolved_id}"
        f" | run:{run_id or 'unknown'}"
        f" | roles:{','.join(resolved_roles)}"
        f" | dims:{','.join(resolved_dims)} -->"
    )
    entry = f"\n{comment}\n## [user] {date_str}\n\n{body}\n"

    resolved_data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with _exclusive_file_lock(feedback_path):
        if feedback_path.exists():
            feedback_path.write_text(feedback_path.read_text(encoding="utf-8") + entry, encoding="utf-8")
        else:
            feedback_path.write_text(_FEEDBACK_HEADER + entry, encoding="utf-8")
        feedback_path.chmod(0o600)

        record = {
            "feedback_id": resolved_id,
            "run_id": run_id,
            "timestamp": datetime.datetime.now().isoformat(),
            "action_taken": "saved",
            "conflict_count": 0,
            "roles": resolved_roles,
            "dims": resolved_dims,
            "text_length": len(body),
            "source": "service",
        }
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        log_path.chmod(0o600)

    return {
        "feedback_id": resolved_id,
        "saved": True,
        "path": str(feedback_path),
        "log_path": str(log_path),
        "run_id": run_id,
        "roles": resolved_roles,
        "dims": resolved_dims,
    }


def _tracker_fingerprint(applications: list[dict[str, Any]]) -> list[str]:
    return [
        json.dumps(row, sort_keys=True, default=str)
        for row in applications
    ]


class _WarningCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _extract_track_warnings(text: str) -> list[str]:
    warnings = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        low = clean.lower()
        if any(marker in low for marker in ("imap connect failed", "gmail login failed", "could not connect", "authentication failed")):
            warnings.append(clean)
    return warnings


def _parse_since(value: str | None, *, now: datetime.datetime | None = None) -> datetime.datetime:
    current = now or datetime.datetime.now(datetime.timezone.utc)
    raw = (value or "3d").strip().lower()
    if raw.endswith("d") and raw[:-1].isdigit():
        return current - datetime.timedelta(days=int(raw[:-1]))
    try:
        parsed_date = datetime.date.fromisoformat(raw)
        return datetime.datetime.combine(parsed_date, datetime.time.min)
    except ValueError as exc:
        raise ValueError(f"Could not parse lookback value: {value!r}") from exc


@contextmanager
def _temporary_environ(updates: dict[str, str]):
    original = {key: os.environ.get(key) for key in updates}
    try:
        for key, value in updates.items():
            if value:
                os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _patched_tracking_paths(data_dir: Path, tracker_path: Path):
    """Temporarily point legacy tracking modules at explicit service paths."""
    from metis import state, xlsx

    with _LEGACY_RUNTIME_LOCK:
        originals = {
            "xlsx_TRACKER_PATH": xlsx.TRACKER_PATH,
            "state_DATA_DIR": state.DATA_DIR,
            "state_LOG_DIR": state.LOG_DIR,
            "state_SKIPPED_FILE": state.SKIPPED_FILE,
            "state_LAST_RUN_FILE": state.LAST_RUN_FILE,
            "state_QUEUE_FILE": state.QUEUE_FILE,
            "state_FEEDBACK_FILE": state.FEEDBACK_FILE,
            "state_FEEDBACK_LOG_FILE": state.FEEDBACK_LOG_FILE,
            "state_RUNS_PATH": state.RUNS_PATH,
        }
        try:
            xlsx.TRACKER_PATH = tracker_path
            state.DATA_DIR = data_dir
            state.LOG_DIR = data_dir / "logs"
            state.SKIPPED_FILE = data_dir / "skipped_roles.json"
            state.LAST_RUN_FILE = data_dir / "last_run.json"
            state.QUEUE_FILE = data_dir / "role_queue.json"
            state.FEEDBACK_FILE = data_dir / "feedback.md"
            state.FEEDBACK_LOG_FILE = data_dir / "feedback_log.jsonl"
            state.RUNS_PATH = data_dir / "runs.jsonl"
            yield
        finally:
            xlsx.TRACKER_PATH = originals["xlsx_TRACKER_PATH"]
            state.DATA_DIR = originals["state_DATA_DIR"]
            state.LOG_DIR = originals["state_LOG_DIR"]
            state.SKIPPED_FILE = originals["state_SKIPPED_FILE"]
            state.LAST_RUN_FILE = originals["state_LAST_RUN_FILE"]
            state.QUEUE_FILE = originals["state_QUEUE_FILE"]
            state.FEEDBACK_FILE = originals["state_FEEDBACK_FILE"]
            state.FEEDBACK_LOG_FILE = originals["state_FEEDBACK_LOG_FILE"]
            state.RUNS_PATH = originals["state_RUNS_PATH"]


@contextmanager
def _patched_job_search_runtime(
    *,
    data_dir: Path,
    profile_path: Path,
    tracker_path: Path,
    env: dict[str, str],
):
    """Temporarily point legacy pipeline globals at explicit service config."""
    env_updates = {
        **env,
        "METIS_DATA_DIR": str(data_dir),
        "METIS_PROFILE": str(profile_path),
        "TRACKER_PATH": str(tracker_path),
    }
    with _LEGACY_RUNTIME_LOCK, _temporary_environ(env_updates):
        from metis import deliver, pipeline, profile, state, trace, xlsx
        from metis.auth import state as auth_state
        from metis.llm import normalize_provider, provider_api_key_env, resolve_stage_models
        from metis.sources import linkedin

        provider = normalize_provider(env_updates.get("METIS_LLM_PROVIDER", env_updates.get("LLM_PROVIDER", "anthropic")))
        provider_key = provider_api_key_env(provider)
        models = resolve_stage_models(provider)
        originals = {
            "pipeline_LLM_PROVIDER": pipeline.LLM_PROVIDER,
            "pipeline_LLM_API_KEY": pipeline.LLM_API_KEY,
            "pipeline_GMAIL_ADDRESS": pipeline.GMAIL_ADDRESS,
            "pipeline_GMAIL_APP_PASSWORD": pipeline.GMAIL_APP_PASSWORD,
            "pipeline_RECIPIENT_EMAIL": pipeline.RECIPIENT_EMAIL,
            "pipeline_MODEL": pipeline.MODEL,
            "pipeline_PRESCREEN_MODEL": pipeline.PRESCREEN_MODEL,
            "pipeline_EXTRACT_MODEL": pipeline.EXTRACT_MODEL,
            "pipeline_MAX_JOBS_PER_RUN": pipeline.MAX_JOBS_PER_RUN,
            "deliver_GMAIL_ADDRESS": deliver.GMAIL_ADDRESS,
            "deliver_GMAIL_APP_PASSWORD": deliver.GMAIL_APP_PASSWORD,
            "deliver_RECIPIENT_EMAIL": deliver.RECIPIENT_EMAIL,
            "profile_YAML_PATH": profile.YAML_PATH,
            "profile_MD_PATH": profile.MD_PATH,
            "xlsx_TRACKER_PATH": xlsx.TRACKER_PATH,
            "state_DATA_DIR": state.DATA_DIR,
            "state_LOG_DIR": state.LOG_DIR,
            "state_SEEN_FILE": state.SEEN_FILE,
            "state_SKIPPED_FILE": state.SKIPPED_FILE,
            "state_LAST_RUN_FILE": state.LAST_RUN_FILE,
            "state_QUEUE_FILE": state.QUEUE_FILE,
            "state_FEEDBACK_FILE": state.FEEDBACK_FILE,
            "state_FEEDBACK_LOG_FILE": state.FEEDBACK_LOG_FILE,
            "state_RUNS_PATH": state.RUNS_PATH,
            "trace_RUNS_PATH": trace.RUNS_PATH,
            "auth_DATA_DIR": auth_state.DATA_DIR,
            "auth_ACTIVE_PROVIDER_PATH": auth_state.ACTIVE_PROVIDER_PATH,
            "linkedin_GMAIL_ADDRESS": linkedin._GMAIL_ADDRESS_ENV,
            "linkedin_GMAIL_APP_PASSWORD": linkedin._GMAIL_APP_PASSWORD_ENV,
        }
        try:
            gmail = env_updates.get("GMAIL_ADDRESS", "")
            app_password = env_updates.get("GMAIL_APP_PASSWORD", "")
            pipeline.LLM_PROVIDER = provider
            pipeline.LLM_API_KEY = env_updates.get(provider_key, "")
            pipeline.GMAIL_ADDRESS = gmail
            pipeline.GMAIL_APP_PASSWORD = app_password
            pipeline.RECIPIENT_EMAIL = env_updates.get("RECIPIENT_EMAIL", gmail)
            pipeline.MODEL = models["model"]
            pipeline.PRESCREEN_MODEL = models["prescreen_model"]
            pipeline.EXTRACT_MODEL = models["extract_model"]
            if env_updates.get("MAX_JOBS_PER_RUN"):
                pipeline.MAX_JOBS_PER_RUN = int(env_updates["MAX_JOBS_PER_RUN"])

            deliver.GMAIL_ADDRESS = gmail
            deliver.GMAIL_APP_PASSWORD = app_password
            deliver.RECIPIENT_EMAIL = env_updates.get("RECIPIENT_EMAIL", gmail)
            profile.YAML_PATH = profile_path
            profile.MD_PATH = data_dir / "profile.md"
            xlsx.TRACKER_PATH = tracker_path
            state.DATA_DIR = data_dir
            state.LOG_DIR = data_dir / "logs"
            state.SEEN_FILE = data_dir / "seen_roles.json"
            state.SKIPPED_FILE = data_dir / "skipped_roles.json"
            state.LAST_RUN_FILE = data_dir / "last_run.json"
            state.QUEUE_FILE = data_dir / "role_queue.json"
            state.FEEDBACK_FILE = data_dir / "feedback.md"
            state.FEEDBACK_LOG_FILE = data_dir / "feedback_log.jsonl"
            state.RUNS_PATH = data_dir / "runs.jsonl"
            trace.RUNS_PATH = data_dir / "runs.jsonl"
            auth_state.DATA_DIR = data_dir
            auth_state.ACTIVE_PROVIDER_PATH = data_dir / "email_provider.json"
            linkedin._GMAIL_ADDRESS_ENV = gmail
            linkedin._GMAIL_APP_PASSWORD_ENV = app_password
            yield pipeline
        finally:
            pipeline.LLM_PROVIDER = originals["pipeline_LLM_PROVIDER"]
            pipeline.LLM_API_KEY = originals["pipeline_LLM_API_KEY"]
            pipeline.GMAIL_ADDRESS = originals["pipeline_GMAIL_ADDRESS"]
            pipeline.GMAIL_APP_PASSWORD = originals["pipeline_GMAIL_APP_PASSWORD"]
            pipeline.RECIPIENT_EMAIL = originals["pipeline_RECIPIENT_EMAIL"]
            pipeline.MODEL = originals["pipeline_MODEL"]
            pipeline.PRESCREEN_MODEL = originals["pipeline_PRESCREEN_MODEL"]
            pipeline.EXTRACT_MODEL = originals["pipeline_EXTRACT_MODEL"]
            pipeline.MAX_JOBS_PER_RUN = originals["pipeline_MAX_JOBS_PER_RUN"]
            deliver.GMAIL_ADDRESS = originals["deliver_GMAIL_ADDRESS"]
            deliver.GMAIL_APP_PASSWORD = originals["deliver_GMAIL_APP_PASSWORD"]
            deliver.RECIPIENT_EMAIL = originals["deliver_RECIPIENT_EMAIL"]
            profile.YAML_PATH = originals["profile_YAML_PATH"]
            profile.MD_PATH = originals["profile_MD_PATH"]
            xlsx.TRACKER_PATH = originals["xlsx_TRACKER_PATH"]
            state.DATA_DIR = originals["state_DATA_DIR"]
            state.LOG_DIR = originals["state_LOG_DIR"]
            state.SEEN_FILE = originals["state_SEEN_FILE"]
            state.SKIPPED_FILE = originals["state_SKIPPED_FILE"]
            state.LAST_RUN_FILE = originals["state_LAST_RUN_FILE"]
            state.QUEUE_FILE = originals["state_QUEUE_FILE"]
            state.FEEDBACK_FILE = originals["state_FEEDBACK_FILE"]
            state.FEEDBACK_LOG_FILE = originals["state_FEEDBACK_LOG_FILE"]
            state.RUNS_PATH = originals["state_RUNS_PATH"]
            trace.RUNS_PATH = originals["trace_RUNS_PATH"]
            auth_state.DATA_DIR = originals["auth_DATA_DIR"]
            auth_state.ACTIVE_PROVIDER_PATH = originals["auth_ACTIVE_PROVIDER_PATH"]
            linkedin._GMAIL_ADDRESS_ENV = originals["linkedin_GMAIL_ADDRESS"]
            linkedin._GMAIL_APP_PASSWORD_ENV = originals["linkedin_GMAIL_APP_PASSWORD"]


def run_job_search(
    *,
    data_dir: str | Path | None = None,
    profile_path: str | Path | None = None,
    tracker_path: str | Path | None = None,
    lookback: str = "3d",
    score_all: bool = False,
    dry_run: bool = True,
    confirm_send: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run or preview the digest pipeline through an explicit service boundary."""
    env_map = dict(env or {})
    resolved_data_dir = Path(data_dir).expanduser() if data_dir else _default_data_dir(env_map)
    resolved_profile = (
        Path(profile_path).expanduser()
        if profile_path
        else _default_profile_path(data_dir=resolved_data_dir, env=env_map)
    )
    resolved_tracker = (
        Path(tracker_path).expanduser()
        if tracker_path
        else _default_tracker_path(data_dir=resolved_data_dir, env=env_map)
    )
    provider = env_map.get("METIS_LLM_PROVIDER", env_map.get("LLM_PROVIDER", "anthropic"))
    provider_key = "OPENAI_API_KEY" if provider.lower().replace("-", "_") in {"openai", "open_ai"} else "ANTHROPIC_API_KEY"

    missing = []
    if not resolved_profile.exists():
        missing.append("profile")
    if not env_map.get(provider_key):
        missing.append(provider_key)
    email_oauth_provider = _safe_json(resolved_data_dir / "email_provider.json", {}).get("provider")
    email_has_oauth = bool(
        email_oauth_provider
        and (resolved_data_dir / f"{email_oauth_provider.split('_')[0]}_token.json").exists()
    )
    if not email_has_oauth and (not env_map.get("GMAIL_ADDRESS") or not env_map.get("GMAIL_APP_PASSWORD")):
        missing.append("gmail_credentials")
    if missing:
        return {
            "ran": False,
            "status": "missing_configuration",
            "missing": missing,
            "dry_run": dry_run,
            "paths": {
                "data_dir": str(resolved_data_dir),
                "profile": str(resolved_profile),
                "tracker": str(resolved_tracker),
            },
        }
    if not dry_run and not confirm_send:
        return {
            "ran": False,
            "status": "confirmation_required",
            "message": "Set confirm_send=True to allow email delivery and tracker/state writes.",
            "dry_run": dry_run,
        }

    try:
        since_dt = _parse_since(lookback)
    except ValueError as exc:
        return {
            "ran": False,
            "status": "invalid_input",
            "error": str(exc),
            "dry_run": dry_run,
            "lookback": lookback,
        }
    before_roles = list_recommended_roles(data_dir=resolved_data_dir, limit=-1, latest_run_only=False)
    stdout = io.StringIO()
    stderr = io.StringIO()
    resolved_data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with _patched_job_search_runtime(
            data_dir=resolved_data_dir,
            profile_path=resolved_profile,
            tracker_path=resolved_tracker,
            env=env_map,
        ) as pipeline, redirect_stdout(stdout), redirect_stderr(stderr):
            pipeline.run_pipeline(since_dt=since_dt, score_all=score_all, dry_run=dry_run)
    except (Exception, SystemExit) as exc:
        after_failure = list_recommended_roles(data_dir=resolved_data_dir, limit=-1, latest_run_only=False)
        return {
            "ran": True,
            "status": "failed",
            "dry_run": dry_run,
            "lookback": lookback,
            "since": since_dt.isoformat(),
            "score_all": score_all,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "paths": {
                "data_dir": str(resolved_data_dir),
                "profile": str(resolved_profile),
                "tracker": str(resolved_tracker),
            },
            "recommended_roles_before": before_roles["count"],
            "recommended_roles_after": after_failure["count"],
            "stdout": stdout.getvalue().strip(),
            "stderr": stderr.getvalue().strip(),
        }

    after_roles = list_recommended_roles(data_dir=resolved_data_dir, limit=-1, latest_run_only=False)
    return {
        "ran": True,
        "status": "completed",
        "dry_run": dry_run,
        "lookback": lookback,
        "since": since_dt.isoformat(),
        "score_all": score_all,
        "paths": {
            "data_dir": str(resolved_data_dir),
            "profile": str(resolved_profile),
            "tracker": str(resolved_tracker),
        },
        "recommended_roles_before": before_roles["count"],
        "recommended_roles_after": after_roles["count"],
        "stdout": stdout.getvalue().strip(),
        "stderr": stderr.getvalue().strip(),
    }


def track_applications(
    *,
    data_dir: str | Path | None = None,
    tracker_path: str | Path | None = None,
    gmail_address: str | None = None,
    app_password: str | None = None,
    api_key: str | None = None,
    since_dt: datetime.datetime | None = None,
    lookback_days: int = 7,
    dry_run: bool = True,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Scan application emails and update/preview the Applications tracker.

    This wraps the existing `metis track` implementation with explicit paths and
    a structured result for agent/MCP callers. The underlying tracking flow is
    still Gmail IMAP based, so callers must supply Gmail credentials or an env
    mapping containing them.
    """
    env_map = env or {}
    resolved_data_dir = Path(data_dir).expanduser() if data_dir else _default_data_dir(env_map)
    resolved_tracker = (
        Path(tracker_path).expanduser()
        if tracker_path
        else _default_tracker_path(data_dir=resolved_data_dir, env=env_map)
    )
    resolved_gmail = gmail_address if gmail_address is not None else env_map.get("GMAIL_ADDRESS", "")
    resolved_password = app_password if app_password is not None else env_map.get("GMAIL_APP_PASSWORD", "")
    resolved_api_key = api_key if api_key is not None else env_map.get("ANTHROPIC_API_KEY")
    resolved_since = since_dt or (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)
    )
    if lookback_days <= 0 and since_dt is None:
        return {
            "ran": False,
            "status": "invalid_input",
            "dry_run": dry_run,
            "message": "lookback_days must be a positive integer.",
            "path": str(resolved_tracker),
        }

    if not resolved_gmail or not resolved_password:
        return {
            "ran": False,
            "status": "missing_credentials",
            "dry_run": dry_run,
            "message": "GMAIL_ADDRESS and GMAIL_APP_PASSWORD are required by the current tracking implementation.",
            "path": str(resolved_tracker),
        }

    before = list_application_activity(
        data_dir=resolved_data_dir,
        tracker_path=resolved_tracker,
        limit=-1,
    )
    before_rows = before.get("applications", [])
    before_fingerprint = _tracker_fingerprint(before_rows)

    stdout = io.StringIO()
    stderr = io.StringIO()
    warning_handler = _WarningCapture()
    warning_handler.setFormatter(logging.Formatter("%(message)s"))
    track_logger = logging.getLogger("metis.track")
    track_logger.addHandler(warning_handler)
    resolved_data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with _patched_tracking_paths(resolved_data_dir, resolved_tracker), redirect_stdout(stdout), redirect_stderr(stderr):
            from metis.track import run_track

            run_track(
                gmail_address=resolved_gmail,
                app_password=resolved_password,
                since_dt=resolved_since,
                dry_run=dry_run,
                api_key=resolved_api_key,
            )
    except (Exception, SystemExit) as exc:
        return {
            "ran": True,
            "status": "failed",
            "dry_run": dry_run,
            "since": resolved_since.isoformat(),
            "path": str(resolved_tracker),
            "rows_before": len(before_rows),
            "rows_after": len(before_rows),
            "rows_added": 0,
            "rows_changed": 0,
            "stdout": stdout.getvalue().strip(),
            "stderr": stderr.getvalue().strip(),
            "warnings": warning_handler.messages,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "applications": before_rows,
        }
    finally:
        track_logger.removeHandler(warning_handler)

    after = list_application_activity(
        data_dir=resolved_data_dir,
        tracker_path=resolved_tracker,
        limit=-1,
    )
    after_rows = after.get("applications", [])
    after_fingerprint = _tracker_fingerprint(after_rows)
    stdout_text = stdout.getvalue().strip()
    stderr_text = stderr.getvalue().strip()
    warnings = []
    for message in warning_handler.messages + _extract_track_warnings(stdout_text) + _extract_track_warnings(stderr_text):
        if message not in warnings:
            warnings.append(message)

    return {
        "ran": True,
        "status": "completed_with_warnings" if warnings else "completed",
        "dry_run": dry_run,
        "since": resolved_since.isoformat(),
        "path": str(resolved_tracker),
        "rows_before": len(before_rows),
        "rows_after": len(after_rows),
        "rows_added": max(0, len(after_rows) - len(before_rows)),
        "rows_changed": sum(
            1 for idx, row in enumerate(after_fingerprint)
            if idx >= len(before_fingerprint) or before_fingerprint[idx] != row
        ),
        "stdout": stdout_text,
        "stderr": stderr_text,
        "warnings": warnings,
        "applications": after_rows,
    }
