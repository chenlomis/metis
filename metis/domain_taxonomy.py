from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_domain_taxonomy() -> dict[str, Any]:
    """Load the packaged domain taxonomy used by scoring and tailoring."""
    path = Path(__file__).with_name("domain_taxonomy.yaml")
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def render_domain_taxonomy(taxonomy: dict[str, Any] | None = None) -> str:
    """Render compact prompt guidance from the domain taxonomy."""
    taxonomy = taxonomy or load_domain_taxonomy()
    if not taxonomy:
        return ""

    lines: list[str] = []
    for rule in taxonomy.get("rules") or []:
        lines.append(f"- {rule}")

    domains = taxonomy.get("domains") or {}
    if domains:
        lines.append("")
        lines.append("Domain reference:")
    for name, info in domains.items():
        if not isinstance(info, dict):
            continue
        label = str(name).replace("_", " ")
        hard = ", ".join(info.get("hard_barriers") or []) or "none"
        native = ", ".join(info.get("native_signals") or []) or "none"
        adjacent = ", ".join(info.get("adjacent_signals") or []) or "none"
        lines.append(f"- {label}: native=[{native}]; adjacent=[{adjacent}]; hard_barriers=[{hard}]")

    return "\n".join(lines)
