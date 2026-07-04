"""Service boundary for agent-facing Metis capabilities.

These functions are intentionally call-time configured and avoid importing the
CLI/pipeline modules that still read environment at import time.
"""
from .read import (
    get_metis_status,
    get_role_details,
    list_application_activity,
    list_recommended_roles,
    list_scoring_feedback,
)
from .progress import generate_progress_summary
from .write import record_scoring_feedback, run_job_search, track_applications

__all__ = [
    "generate_progress_summary",
    "get_metis_status",
    "get_role_details",
    "list_application_activity",
    "list_recommended_roles",
    "list_scoring_feedback",
    "record_scoring_feedback",
    "run_job_search",
    "track_applications",
]
