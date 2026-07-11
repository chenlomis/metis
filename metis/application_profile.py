from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from .application_state import data_dir


ENV_FIELDS = {
    "first_name": "METIS_FIRST_NAME", "last_name": "METIS_LAST_NAME",
    "email": "GMAIL_ADDRESS", "phone": "METIS_PHONE", "location": "METIS_LOCATION",
    "linkedin": "METIS_LINKEDIN_URL", "github": "METIS_GITHUB_URL",
    "pronouns": "METIS_PRONOUNS", "current_employer": "METIS_CURRENT_EMPLOYER",
    "gender_identity": "METIS_GENDER_IDENTITY", "transgender": "METIS_TRANSGENDER",
    "hispanic_latino": "METIS_HISPANIC_LATINO", "race": "METIS_RACE",
    "veteran_status": "METIS_VETERAN_STATUS", "disability": "METIS_DISABILITY",
    "work_authorized": "METIS_WORK_AUTHORIZED",
    "sponsorship_required": "METIS_SPONSORSHIP_REQUIRED",
    "willing_to_relocate": "METIS_WILLING_TO_RELOCATE",
    "referral_source": "METIS_REFERRAL_SOURCE", "default_resume": "METIS_DEFAULT_RESUME",
    "chrome_profile_dir": "METIS_CHROME_PROFILE_DIR",
    "chrome_profile_name": "METIS_CHROME_PROFILE_NAME",
}


def application_profile_path(root: Path | None = None) -> Path:
    return (root or data_dir()) / "application_profile.yaml"


def load_application_profile(root: Path | None = None) -> dict[str, Any]:
    path = application_profile_path(root)
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return value if isinstance(value, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


def save_application_profile(values: dict[str, Any], root: Path | None = None) -> Path:
    path = application_profile_path(root)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_suffix(".yaml.tmp")
    temp.write_text(yaml.safe_dump(values, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)
    path.chmod(0o600)
    return path


def env_application_profile() -> dict[str, str]:
    return {field: os.getenv(env, "").strip() for field, env in ENV_FIELDS.items() if os.getenv(env, "").strip()}


def application_value(field: str, default: str = "") -> str:
    env_name = ENV_FIELDS.get(field)
    if env_name and os.getenv(env_name, "").strip():
        return os.environ[env_name].strip()
    return str(load_application_profile().get(field, default) or "").strip()
