import datetime
from .linkedin import fetch_linkedin_alerts_since


def fetch_alerts(since_dt: datetime.datetime) -> list[dict]:
    """Unified source entry point — returns all LinkedIn alert emails since since_dt.

    Routes to the correct source parser. Additional sources (Indeed, Glassdoor, etc.)
    can be added here when the extensible source layer is built out (spec §2 Q3).
    """
    return fetch_linkedin_alerts_since(since_dt)
