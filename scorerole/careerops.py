"""career-ops integration — writes qualifying jobs to the pipeline queue
and triggers career-ops evaluation via the Claude Code CLI.

Setup (one-time):
  git clone https://github.com/santifer/career-ops ~/career-ops
  cd ~/career-ops && npm install

Then set CAREER_OPS_DIR in .env if you cloned it elsewhere.
career-ops evaluates each job URL (A-F report + tailored PDF).
Form-filling (/career-ops apply) is run separately from Claude Code.
"""
import os, subprocess, logging
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CAREER_OPS_DIR = Path(
    os.getenv("CAREER_OPS_DIR", str(Path.home() / "career-ops"))
).expanduser()
PIPELINE_FILE  = CAREER_OPS_DIR / "data" / "pipeline.md"
CLAUDE_BIN     = os.getenv("CLAUDE_BIN", "/opt/homebrew/bin/claude")

# Minimum score to include in the career-ops queue.
# Mirrors the pipeline's "consider" threshold — change via CAREER_OPS_MIN_SCORE in .env.
CAREER_OPS_MIN_SCORE = int(os.getenv("CAREER_OPS_MIN_SCORE", "60"))


# ---------------------------------------------------------------------------
# Queue management
# ---------------------------------------------------------------------------

def _is_available() -> bool:
    if not CAREER_OPS_DIR.exists():
        log.warning(
            f"career-ops not found at {CAREER_OPS_DIR}. "
            f"Set CAREER_OPS_DIR in .env or clone: "
            f"git clone https://github.com/santifer/career-ops {CAREER_OPS_DIR}"
        )
        return False
    return True


def write_pipeline_queue(jobs: list[dict]) -> int:
    """Append qualifying jobs (score ≥ CAREER_OPS_MIN_SCORE, has external apply URL)
    to career-ops data/pipeline.md under the ## Pending section.

    Returns the number of URLs actually added.
    """
    if not _is_available():
        return 0

    qualifying = [
        j for j in jobs
        if j.get("apply_url")
        and j.get("eval", {}).get("score", 0) >= CAREER_OPS_MIN_SCORE
    ]

    easy_apply_skipped = [
        j for j in jobs
        if not j.get("apply_url")
        and j.get("eval", {}).get("score", 0) >= CAREER_OPS_MIN_SCORE
    ]
    if easy_apply_skipped:
        log.info(
            f"career-ops: {len(easy_apply_skipped)} role(s) skipped (LinkedIn Easy Apply — "
            f"no external ATS URL): "
            + ", ".join(f"{j['title']} @ {j['company']}" for j in easy_apply_skipped)
        )

    if not qualifying:
        log.info("career-ops: no qualifying jobs with external apply URLs to queue.")
        return 0

    PIPELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = PIPELINE_FILE.read_text() if PIPELINE_FILE.exists() else ""

    new_lines: list[str] = []
    for job in qualifying:
        url = job["apply_url"]
        if url in existing:
            log.debug(f"career-ops: already queued — skipping {url}")
            continue
        score   = job["eval"]["score"]
        verdict = job["eval"].get("verdict", "consider")
        comment = f"{job['title']} at {job['company']} ({verdict} · {score}%)"
        new_lines.append(f"- [ ] {url} <!-- {comment} -->")

    if not new_lines:
        log.info("career-ops: all qualifying jobs already in pipeline queue.")
        return 0

    # Ensure a ## Pending section exists
    if "## Pending" not in existing:
        existing = "## Pending\n\n" + existing

    insert_at = existing.index("## Pending") + len("## Pending")
    updated = (
        existing[:insert_at]
        + "\n\n"
        + "\n".join(new_lines)
        + "\n"
        + existing[insert_at:].lstrip("\n")
    )
    PIPELINE_FILE.write_text(updated)

    added = len(new_lines)
    log.info(f"career-ops: queued {added} job(s) → {PIPELINE_FILE}")
    for line in new_lines:
        log.info(f"  {line}")
    return added


# ---------------------------------------------------------------------------
# Pipeline trigger
# ---------------------------------------------------------------------------

def trigger_pipeline() -> None:
    """Run career-ops pipeline in the background via Claude Code CLI.

    career-ops reads data/pipeline.md, evaluates each URL (A-F report +
    tailored PDF), and updates the tracker. Runs async — does not block
    digest delivery. Output is captured to ~/.job_pipeline/careerops.log.
    """
    if not _is_available():
        return

    claude = Path(CLAUDE_BIN)
    if not claude.exists():
        log.error(
            f"Claude CLI not found at {CLAUDE_BIN}. "
            f"Set CLAUDE_BIN in .env if installed elsewhere."
        )
        return

    log_file = Path.home() / ".job_pipeline" / "careerops.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    log.info(
        f"Launching career-ops pipeline in background "
        f"(output → {log_file})"
    )

    with open(log_file, "a") as fh:
        import datetime as _dt
        fh.write(f"\n\n=== career-ops run {_dt.datetime.now().isoformat()} ===\n")
        fh.flush()
        subprocess.Popen(
            [str(claude), "--print", "/career-ops pipeline"],
            cwd=str(CAREER_OPS_DIR),
            stdout=fh,
            stderr=fh,
            # Detach from current process so scorerole exits cleanly
            start_new_session=True,
        )

    log.info(
        f"career-ops pipeline started — check {log_file} for progress, "
        f"reports will appear in {CAREER_OPS_DIR / 'reports'}/"
    )
