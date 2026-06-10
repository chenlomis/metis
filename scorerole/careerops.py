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


def _qualifying(jobs: list[dict]) -> list[dict]:
    """Return all jobs scoring ≥ CAREER_OPS_MIN_SCORE that have a LinkedIn URL."""
    return [
        j for j in jobs
        if j.get("eval", {}).get("score", 0) >= CAREER_OPS_MIN_SCORE
        and j.get("url")
    ]


# ---------------------------------------------------------------------------
# Queue (record-keeping)
# ---------------------------------------------------------------------------

def write_pipeline_queue(jobs: list[dict]) -> list[dict]:
    """Persist qualifying jobs to data/pipeline.md for record-keeping.

    Records the LinkedIn job URL (starting point for apply-basic.mjs).
    Returns the list of jobs newly added (deduped against existing entries).
    """
    if not _is_available():
        return []

    qualifying = _qualifying(jobs)
    if not qualifying:
        log.info("career-ops: no qualifying jobs to queue (score < threshold or no URL).")
        return []

    PIPELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    existing = PIPELINE_FILE.read_text() if PIPELINE_FILE.exists() else ""

    newly_added: list[dict] = []
    new_lines:   list[str]  = []
    for job in qualifying:
        url     = job["url"]           # LinkedIn job URL — apply-basic.mjs starts here
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
    log.info(f"career-ops: queued {len(new_lines)} job(s) in {PIPELINE_FILE}")
    return newly_added


# ---------------------------------------------------------------------------
# Browser opener + pre-fill
# ---------------------------------------------------------------------------

def open_and_prefill(jobs: list[dict]) -> None:
    """Open each job in a visible browser via LinkedIn auth, detect apply type,
    skip Easy Apply, and pre-fill contact + EEO fields on external ATS pages.

    Uses apply-basic.mjs (Playwright + li_at cookie).
    Browser stays open after all tabs are processed — review and submit.
    """
    if not _is_available():
        return

    if not APPLY_SCRIPT.exists():
        log.error(
            f"apply-basic.mjs not found at {APPLY_SCRIPT}. "
            f"Make sure career-ops is up to date."
        )
        return

    # Pass LinkedIn job URLs — apply-basic.mjs authenticates, finds Apply button,
    # skips Easy Apply, follows external redirects, and fills ATS forms.
    linkedin_urls = [j["url"] for j in jobs if j.get("url")]
    if not linkedin_urls:
        return

    log.info(f"Opening {len(linkedin_urls)} job(s) via LinkedIn (Easy Apply will be skipped):")
    for job in jobs:
        log.info(
            f"  {job['title']} @ {job['company']} "
            f"({job['eval']['score']}%) → {job['url']}"
        )

    # Inherit full env (includes LINKEDIN_COOKIE loaded by dotenv in pipeline.py)
    result = subprocess.run(
        [NODE_BIN, str(APPLY_SCRIPT)] + linkedin_urls,
        cwd=str(CAREER_OPS_DIR),
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        log.error("apply-basic.mjs exited with an error — check output above.")
