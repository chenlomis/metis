"""scorerole feedback — interactive calibration feedback capture.

Shows a summary of the last pipeline run, then prompts for free-form notes.
Each entry is timestamped and appended to ~/.job_pipeline/feedback.md, which
gets injected into the scoring system prompt on every subsequent run.

Usage:
    scorerole feedback
"""
from __future__ import annotations

from __future__ import annotations
import datetime
import json
import logging
from pathlib import Path

from .state import DATA_DIR, LAST_RUN_FILE, FEEDBACK_FILE

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Last-run summary — written by pipeline, read here for display context
# ---------------------------------------------------------------------------

def save_last_run(scored_jobs: list[dict], run_date: str, filtered_count: int = 0) -> None:
    """Persist a compact run summary to last_run.json for the feedback prompt."""
    apply_roles   = [j for j in scored_jobs if j["eval"].get("verdict") == "apply"]
    consider_roles = [j for j in scored_jobs if j["eval"].get("verdict") == "consider"]
    skipped_count = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "skipped")

    # Keep apply + consider roles for display, sorted by score descending
    display_roles = sorted(
        [j for j in scored_jobs if j["eval"].get("verdict") in ("apply", "consider")],
        key=lambda x: -x["eval"].get("score", 0),
    )

    payload = {
        "run_date":       run_date,
        "run_timestamp":  datetime.datetime.now().isoformat(),
        "total_evaluated": len(scored_jobs),
        "apply_count":    len(apply_roles),
        "consider_count": len(consider_roles),
        "skipped_count":  skipped_count,
        "filtered_count": filtered_count,
        "roles": [
            {
                "title":   j["title"],
                "company": j["company"],
                "score":   j["eval"].get("score", 0),
                "verdict": j["eval"].get("verdict"),
            }
            for j in display_roles
        ],
    }
    try:
        DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        LAST_RUN_FILE.write_text(json.dumps(payload, indent=2))
        LAST_RUN_FILE.chmod(0o600)
    except OSError as exc:
        log.warning("Could not save last_run.json: %s", exc)


def load_last_run() -> dict | None:
    if not LAST_RUN_FILE.exists():
        return None
    try:
        return json.loads(LAST_RUN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Feedback file I/O
# ---------------------------------------------------------------------------

def save_feedback_entry(text: str) -> None:
    """Append a dated feedback entry to ~/.job_pipeline/feedback.md."""
    date_str = datetime.date.today().isoformat()
    entry    = f"\n## {date_str}\n\n{text}\n"
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if FEEDBACK_FILE.exists():
        FEEDBACK_FILE.write_text(FEEDBACK_FILE.read_text() + entry)
    else:
        header = (
            "# Scoring Feedback\n\n"
            "Free-form calibration notes — injected into every scoring run.\n"
            "Add entries via `scorerole feedback` or edit this file directly.\n"
        )
        FEEDBACK_FILE.write_text(header + entry)
    FEEDBACK_FILE.chmod(0o600)


def load_feedback_text() -> str | None:
    """Return feedback.md contents, or None if the file doesn't exist or is empty."""
    if not FEEDBACK_FILE.exists():
        return None
    content = FEEDBACK_FILE.read_text().strip()
    return content if content else None


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _show_last_run(run: dict) -> None:
    filtered_note = (
        f" · {run['filtered_count']} filtered"
        if run.get("filtered_count") else ""
    )
    print(f"\n  Last run: {run.get('run_date', 'unknown')}")
    print(
        f"  {run.get('total_evaluated', 0)} roles evaluated — "
        f"{run.get('apply_count', 0)} apply · "
        f"{run.get('consider_count', 0)} consider · "
        f"{run.get('skipped_count', 0)} skipped"
        f"{filtered_note}"
    )
    print()

    roles    = run.get("roles", [])
    apply_r  = [r for r in roles if r["verdict"] == "apply"]
    consider_r = [r for r in roles if r["verdict"] == "consider"]

    if apply_r:
        print("  Apply:")
        for r in apply_r:
            print(f"    {r['score']}%  {r['title']} — {r['company']}")
    if consider_r:
        print("  Consider:")
        for r in consider_r[:4]:
            print(f"    {r['score']}%  {r['title']} — {r['company']}")
        if len(consider_r) > 4:
            print(f"    … and {len(consider_r) - 4} more")
    print()


def _collect_lines() -> str:
    """Multiline input — blank line or Ctrl+C to finish."""
    print("  Your feedback (blank line when done, Ctrl+C to cancel):")
    lines: list[str] = []
    try:
        while True:
            line = input("  > ")
            if not line.strip():
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        print()
        return ""
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_feedback() -> None:
    """Interactive feedback prompt — show last run, collect notes, persist to feedback.md."""
    run = load_last_run()
    if run:
        _show_last_run(run)
    else:
        print("\n  No previous run on record. You can still add calibration notes.\n")

    existing = load_feedback_text()
    if existing:
        # Show how many entries are already saved so the user knows it's accumulating
        n_entries = existing.count("\n## ")
        print(f"  ({n_entries} prior feedback entr{'y' if n_entries == 1 else 'ies'} already saved)\n")

    text = _collect_lines()
    if not text:
        print("  (skipped — no changes made)\n")
        return

    save_feedback_entry(text)

    n_lines = len(text.splitlines())
    line_note = (
        f" — {n_lines} line{'s' if n_lines != 1 else ''} saved"
        if n_lines > 1 else ""
    )
    print(f"\n  ✓ Feedback saved{line_note}. Will apply from next run.\n")
