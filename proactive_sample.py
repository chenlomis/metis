#!/usr/bin/env python3
"""One-off proactive scrape for a curated company subset.

Fetches, scores, and emails a digest using the same render.py format as the
regular pipeline. Roles are marked seen after send so they won't re-appear.

Usage:
    python proactive_sample.py              # score + send
    python proactive_sample.py --dry-run   # score only, no email, no seen_roles write
    python proactive_sample.py --lookback 14  # override lookback days (default: 30)
"""
import sys
import datetime
import logging

import importlib.util
if importlib.util.find_spec("metis") is None:
    sys.path.insert(0, __file__.replace("proactive_sample.py", ""))

import anthropic
from dotenv import load_dotenv

from metis.config import Config
from metis.state import load_seen_roles, save_seen_roles, _role_hash
from metis.pipeline import _stage_enrich_and_score, _stage_split_filtered
from metis.render import render_html, send_digest
from metis.sources.proactive import (
    _scrape_greenhouse,
    _build_title_patterns,
    _detect_country,
    DEFAULT_LOOKBACK_DAYS,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

# ── Company subset for this run ───────────────────────────────────────────────
# All Greenhouse — no Playwright dependency.
# Swap entries freely; slug must match the ATS board slug.
COMPANIES = [
    {"slug": "coreweave",  "name": "CoreWeave"},    # AI cloud infrastructure
    {"slug": "datadog",    "name": "Datadog"},       # developer observability
    {"slug": "vercel",     "name": "Vercel"},        # frontend/edge platform
    {"slug": "twilio",     "name": "Twilio"},        # developer APIs
    {"slug": "hightouch",  "name": "Hightouch"},     # data activation / reverse ETL
]


def _parse_args():
    dry_run  = "--dry-run" in sys.argv
    lookback = DEFAULT_LOOKBACK_DAYS
    for i, arg in enumerate(sys.argv):
        if arg == "--lookback" and i + 1 < len(sys.argv):
            try:
                lookback = int(sys.argv[i + 1])
            except ValueError:
                pass
    return dry_run, lookback


def main():
    dry_run, lookback_days = _parse_args()

    load_dotenv(override=True)
    cfg    = Config.from_env()
    client = anthropic.Anthropic(api_key=cfg.anthropic_api_key)

    from metis.profile import load_profile_yaml
    profile = load_profile_yaml() or {}

    # Build title/location filters from profile — required per CLAUDE.md §-1
    target_roles   = profile.get("target", {}).get("roles", [])
    include, excl  = _build_title_patterns(target_roles)
    candidate_loc  = profile.get("candidate", {}).get("location", "")
    target_country = _detect_country(candidate_loc)

    seen_hashes = load_seen_roles()

    # ── Scrape ───────────────────────────────────────────────────────────────
    jobs: list[dict]          = []
    seen_title_co: set[tuple] = set()

    for co in COMPANIES:
        try:
            scraped = _scrape_greenhouse(co, include, excl, target_country, lookback_days, seen_hashes)
            for job in scraped:
                # Within-run dedup: same title + company at different locations = one card
                key = (job["title"].lower().strip(), job["company"].lower().strip())
                if key not in seen_title_co:
                    seen_title_co.add(key)
                    jobs.append(job)
            log.info("%s — %d new roles after dedup", co["name"], len(scraped))
        except Exception as exc:
            log.warning("%s — scrape failed: %s", co["name"], exc)

    if not jobs:
        log.info("No new roles found across %d companies.", len(COMPANIES))
        return

    log.info("Total: %d roles to evaluate across %d companies.", len(jobs), len(COMPANIES))

    # ── Score ─────────────────────────────────────────────────────────────────
    jobs = _stage_enrich_and_score(jobs, client, config=cfg)
    scored_jobs, n_filtered = _stage_split_filtered(jobs)

    if not scored_jobs:
        log.info("No scoreable roles after filtering (filtered=%d).", n_filtered)
        return

    # ── Render via render.py (canonical format — CLAUDE.md §-1) ───────────────
    run_date = datetime.datetime.now().strftime("%B %d, %Y")
    html = render_html(scored_jobs, run_date, deal_breaker_count=n_filtered)

    apply_n    = sum(1 for j in scored_jobs if j.get("eval", {}).get("verdict") == "apply")
    consider_n = sum(1 for j in scored_jobs if j.get("eval", {}).get("verdict") == "consider")
    skip_n     = sum(1 for j in scored_jobs if j.get("eval", {}).get("verdict") == "skipped")
    log.info("Results: %d apply / %d consider / %d skip / %d filtered", apply_n, consider_n, skip_n, n_filtered)

    if dry_run:
        log.info("[DRY RUN] Digest not sent. No seen_roles written.")
        return

    # ── Send ──────────────────────────────────────────────────────────────────
    send_digest(html, run_date, config=cfg)

    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
    new_timestamps = {_role_hash(j["title"], j["company"]): now_iso for j in jobs}
    save_seen_roles(new_timestamps)
    log.info("Sent. Marked %d roles as seen.", len(new_timestamps))


if __name__ == "__main__":
    main()
