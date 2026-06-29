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
    """Unified source entry point — returns all job alerts since since_dt.

    Merges three source types:
      - LinkedIn email alerts  (always active)
      - Non-LinkedIn email alerts  (opt-in via ~/.job_pipeline/email_sources.yaml)
      - Proactive career-page scraping  (opt-in via profile.yaml proactive_sources)

    Email-alert jobs carry source='email_alert' and a pre-fetched `jd` field.
    Proactive jobs carry source='proactive' and a pre-fetched `jd` field.
    Both skip the enrich_jobs() HTTP fetch step in pipeline.py.
    """
    gmail_address      = config.gmail_address      if config else ""
    gmail_app_password = config.gmail_app_password if config else ""

    results = fetch_linkedin_alerts_since(
        since_dt,
        gmail_address=gmail_address,
        gmail_app_password=gmail_app_password,
    )

    # Non-LinkedIn email alert sources (e.g. Waymo, GitHub)
    try:
        from .email_alerts import fetch_email_alerts, load_email_sources
        email_sources = load_email_sources()
        if email_sources:
            email_jobs = fetch_email_alerts(
                since_dt,
                email_sources,
                gmail_address=gmail_address,
                gmail_app_password=gmail_app_password,
            )
            if email_jobs:
                log.info("email-alerts: %d new role(s) from %d source(s)",
                         len(email_jobs), len(email_sources))
            results = results + email_jobs
    except Exception as e:
        log.warning("email-alert source failed (continuing without): %s", e)

    # Proactive career-page scraping
    if profile and profile.get("proactive_sources", {}).get("enabled", False):
        try:
            from .proactive import fetch_proactive
            from ..state import load_seen_roles
            seen = load_seen_roles()
            proactive_jobs = fetch_proactive(profile, seen_hashes=set(seen.keys()))
            if proactive_jobs:
                log.info("proactive source: %d new role(s)", len(proactive_jobs))
            results = results + proactive_jobs
        except Exception as e:
            log.warning("proactive source failed (continuing without): %s", e)

    return results
