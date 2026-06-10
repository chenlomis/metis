"""career-ops integration — writes qualifying jobs to the pipeline queue
and opens each ATS application page with basic contact fields pre-filled.

Setup (one-time):
  git clone https://github.com/santifer/career-ops ~/career-ops
  cd ~/career-ops && npm install

Then set CAREER_OPS_DIR in .env if you cloned it elsewhere.

What this does:
  1. write_pipeline_queue() — records qualifying jobs in data/pipeline.md
  2. open_and_prefill()     — opens each ATS URL in a visible browser window
                             and fills: first name, last name, email, phone,
                             LinkedIn, location from config/profile.yml.
                             Browser stays open so you complete and submit.
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
PIPELINE_FILE    = CAREER_OPS_DIR / "data" / "pipeline.md"
APPLY_SCRIPT     = CAREER_OPS_DIR / "apply-basic.mjs"
NODE_BIN         = os.getenv("NODE_BIN", "node")

# Minimum score to open an application. Change via CAREER_OPS_MIN_SCORE in .env.
CAREER_OPS_MIN_SCORE = int(os.getenv("CAREER_OPS_MIN_SCORE", "60"))


# ---------------------------------------------------------------------------
# Helpers
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


def _qualifying(jobs: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split jobs into (has_external_url, easy_apply_only)."""
    has_url, easy_apply = [], []
    for j in jobs:
        if j.get("eval", {}).get("score", 0) < CAREER_OPS_MIN_SCORE:
            continue
        if j.get("apply_url"):
            has_url.append(j)
        else:
            easy_apply.append(j)
    return has_url, easy_apply


# ---------------------------------------------------------------------------
# Queue (record-keeping)
# ---------------------------------------------------------------------------

def write_pipeline_queue(jobs: list[dict]) -> list[dict]:
    """Persist qualifying jobs with external ATS URLs to data/pipeline.md.

    Returns the list of jobs that were newly added (for passing to open_and_prefill).
    """
    if not _is_available():
        return []

    to_open, easy_apply = _qualifying(jobs)

    if easy_apply:
        log.info(
            f"career-ops: {len(easy_apply)} LinkedIn Easy Apply role(s) — "
            f"no external URL, open manually: "
            + ", ".join(f"{j['title']} @ {j['company']}" for j in easy_apply)
        )

    if not to_open:
        log.info("career-ops: no qualifying jobs with external apply URLs.")
        return []

    PIPELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = PIPELINE_FILE.read_text() if PIPELINE_FILE.exists() else ""

    newly_added: list[dict] = []
    new_lines:   list[str]  = []
    for job in to_open:
        url = job["apply_url"]
        if url in existing:
            log.debug(f"career-ops: already queued — {url}")
            continue
        score   = job["eval"]["score"]
        verdict = job["eval"].get("verdict", "consider")
        comment = f"{job['title']} at {job['company']} ({verdict} · {score}%)"
        new_lines.append(f"- [ ] {url} <!-- {comment} -->")
        newly_added.append(job)

    if not new_lines:
        log.info("career-ops: all qualifying jobs already in queue.")
        return []

    if "## Pending" not in existing:
        existing = "## Pending\n\n" + existing

    insert_at = existing.index("## Pending") + len("## Pending")
    updated   = (
        existing[:insert_at]
        + "\n\n"
        + "\n".join(new_lines)
        + "\n"
        + existing[insert_at:].lstrip("\n")
    )
    PIPELINE_FILE.write_text(updated)

    log.info(f"career-ops: recorded {len(new_lines)} job(s) in {PIPELINE_FILE}")
    return newly_added


# ---------------------------------------------------------------------------
# Browser opener + pre-fill
# ---------------------------------------------------------------------------

def open_and_prefill(jobs: list[dict]) -> None:
    """Open each job's ATS URL in a visible browser and pre-fill contact fields.

    Uses apply-basic.mjs (Playwright) to fill: first name, last name, email,
    phone, LinkedIn URL, location from config/profile.yml.
    Browser stays open — review, complete remaining fields, and submit.
    """
    if not _is_available():
        return

    if not APPLY_SCRIPT.exists():
        log.error(
            f"apply-basic.mjs not found at {APPLY_SCRIPT}. "
            f"Make sure career-ops is up to date."
        )
        return

    urls = [j["apply_url"] for j in jobs if j.get("apply_url")]
    if not urls:
        return

    log.info(
        f"Opening {len(urls)} application(s) in browser with contact fields pre-filled..."
    )
    for job in jobs:
        if job.get("apply_url"):
            log.info(
                f"  {job['title']} @ {job['company']} "
                f"({job['eval']['score']}%) → {job['apply_url']}"
            )

    # Run Playwright script — headed (visible) browser, does NOT detach
    # so scorerole waits for the script to finish opening all tabs before exiting.
    result = subprocess.run(
        [NODE_BIN, str(APPLY_SCRIPT)] + urls,
        cwd=str(CAREER_OPS_DIR),
    )
    if result.returncode != 0:
        log.error("apply-basic.mjs exited with an error — check output above.")
