from __future__ import annotations
import json, logging, datetime, os
from pathlib import Path

log = logging.getLogger(__name__)

from .state import RUNS_PATH
from .types import JobDict
_PROMPT_VERSION = "2026-06-19"


def write_trace(job: JobDict) -> None:
    """Append one trace record for a job to ~/.job_pipeline/runs.jsonl.

    Called at three points in the pipeline:
      - prescreened-out jobs  (eval.verdict = "prescreened")
      - hard-gate filtered    (eval.verdict = "filtered", gate name in eval.gate)
      - Layer 2 scored        (eval includes full dimensions breakdown)
    """
    from .state import _role_hash
    record = {
        "ts":             datetime.datetime.now().isoformat(timespec="seconds"),
        "role_hash":      _role_hash(job.get("title", ""), job.get("company", "")),
        "title":          job.get("title", ""),
        "company":        job.get("company", ""),
        "location":       job.get("location", ""),
        "source":         job.get("source", ""),
        "extraction":     job.get("extraction", {}),
        "eval":           job.get("eval", {}),
        "prompt_version": _PROMPT_VERSION,
        "model":          os.getenv("MODEL", "claude-sonnet-4-6"),
    }
    try:
        RUNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with RUNS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        log.warning("write_trace failed — skipping trace for %s at %s: %s",
                    job.get("title"), job.get("company"), exc)
