import datetime
from .linkedin import fetch_linkedin_alerts, fetch_linkedin_alerts_since


def fetch_alerts(seen_ids: set, since_dt=None) -> list[dict]:
    """Unified entry point — routes to correct source parser."""
    if since_dt is not None:
        return fetch_linkedin_alerts_since(since_dt)
    return fetch_linkedin_alerts(seen_ids)
