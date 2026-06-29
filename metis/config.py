"""scorerole/config.py — runtime configuration as an explicit dataclass.

Build once in main() via Config.from_env() after load_dotenv(). Pass as a
parameter to run_pipeline() and run_track() — never read os.getenv() inside
module bodies or at import time.

This is the prerequisite for the MCP server (Stage 1 of the interface roadmap):
an MCP tool call can construct Config from the request context and call
run_pipeline(config) without touching the ambient environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    anthropic_api_key:  str = ""
    gmail_address:      str = ""
    gmail_app_password: str = ""
    recipient_email:    str = ""
    model:              str = "claude-sonnet-4-6"
    prescreen_model:    str = "claude-haiku-4-5"
    extract_model:      str = "claude-haiku-4-5"
    max_jobs_per_run:   int = 20
    default_lookback:   str = "3d"

    @classmethod
    def from_env(cls) -> "Config":
        """Build Config from environment variables (call once after load_dotenv())."""
        gmail = os.getenv("GMAIL_ADDRESS", "")
        return cls(
            anthropic_api_key  = os.getenv("ANTHROPIC_API_KEY", ""),
            gmail_address      = gmail,
            gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", ""),
            recipient_email    = os.getenv("RECIPIENT_EMAIL", gmail),
            model              = os.getenv("MODEL",              "claude-sonnet-4-6"),
            prescreen_model    = os.getenv("PRESCREEN_MODEL",    "claude-haiku-4-5"),
            extract_model      = os.getenv("EXTRACT_MODEL",      "claude-haiku-4-5"),
            max_jobs_per_run   = int(os.getenv("MAX_JOBS_PER_RUN", "20")),
            default_lookback   = os.getenv("DEFAULT_LOOKBACK",   "3d"),
        )
