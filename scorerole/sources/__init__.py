import datetime
import logging
from typing import Optional, TYPE_CHECKING

from .linkedin import fetch_linkedin_alerts_since

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)


def fetch_alerts(
    since_dt: datetime.datetime,
    profile: Optional[dict] = None,
    *,
    config: "Config | None" = None,
) -> list[dict]:
    """Unified source entry point — returns all job alert emails since since_dt.

    Merges two source types:
      - Reactive: LinkedIn email alerts (existing behaviour, unchanged)
      - Proactive: direct company career page scraping (new, opt-in via profile)

    Both sources return job dicts; proactive jobs have source='proactive' and
    a pre-populated `jd` field so they skip the enrich_jobs() HTTP fetch step.
    """
    gmail_address      = config.gmail_address      if config else ""
    gmail_app_password = config.gmail_app_password if config else ""

    results = fetch_linkedin_alerts_since(
        since_dt,
        gmail_address=gmail_address,
        gmail_app_password=gmail_app_password,
    )

    if profile and profile.get("proactive_sources", {}).get("enabled", False):
        try:
            from .proactive import fetch_proactive
            from ..state import load_seen_roles
            seen = load_seen_roles()
            proactive_jobs = fetch_proactive(profile, seen_hashes=set(seen.keys()))
            if proactive_jobs:
                log.info(f"proactive source: {len(proactive_jobs)} new roles")
            results = results + proactive_jobs
        except Exception as e:
            log.warning(f"proactive source failed (LinkedIn-only this run): {e}")

    return results
