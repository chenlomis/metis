"""metis/config.py — runtime configuration as an explicit dataclass.

Target pattern: build once in main() via Config.from_env() after load_dotenv(),
then pass as a parameter to pipeline and tracking functions. Some legacy CLI
paths still read environment variables directly; new code should prefer this
dataclass instead of adding more module-level config.

This is the path toward wrappers such as an MCP server, where callers need to
construct config from request context without mutating the ambient environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .llm import normalize_provider, resolve_stage_models


@dataclass
class Config:
    llm_provider:       str = "anthropic"
    anthropic_api_key:  str = ""
    openai_api_key:     str = ""
    gemini_api_key:     str = ""
    xai_api_key:        str = ""
    gmail_address:      str = ""
    gmail_app_password: str = ""
    recipient_email:    str = ""
    model:              str = "claude-sonnet-4-6"
    prescreen_model:    str = "claude-haiku-4-5"
    extract_model:      str = "claude-haiku-4-5"
    max_jobs_per_run:   int = 40
    default_lookback:   str = "3d"

    @classmethod
    def from_env(cls) -> "Config":
        """Build Config from environment variables (call once after load_dotenv())."""
        gmail = os.getenv("GMAIL_ADDRESS", "")
        llm_provider = normalize_provider(os.getenv("METIS_LLM_PROVIDER", os.getenv("LLM_PROVIDER", "anthropic")))
        models = resolve_stage_models(llm_provider)
        return cls(
            llm_provider      = llm_provider,
            anthropic_api_key  = os.getenv("ANTHROPIC_API_KEY", ""),
            openai_api_key     = os.getenv("OPENAI_API_KEY", ""),
            gemini_api_key     = os.getenv("GEMINI_API_KEY", ""),
            xai_api_key        = os.getenv("XAI_API_KEY", ""),
            gmail_address      = gmail,
            gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", ""),
            recipient_email    = os.getenv("RECIPIENT_EMAIL", gmail),
            model              = models["model"],
            prescreen_model    = models["prescreen_model"],
            extract_model      = models["extract_model"],
            max_jobs_per_run   = int(os.getenv("MAX_JOBS_PER_RUN", "40")),
            default_lookback   = os.getenv("DEFAULT_LOOKBACK",   "3d"),
        )
