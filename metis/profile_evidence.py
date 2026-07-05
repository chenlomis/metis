from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import re
from pathlib import Path
from typing import Any

from .profile import YAML_PATH, load_profile_yaml
from .state import DATA_DIR


THEME_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("enterprise data", ("enterprise data", "agreement", "data platform", "data quality")),
    ("data modeling", ("data model", "modeling", "structured", "unstructured", "semantic")),
    ("data governance", ("governance", "lineage", "provenance", "reliability", "quality")),
    ("ai systems", ("ai", "ml", "llm", "model", "gen ai", "agentic", "rlhf")),
    ("automation", ("automated", "automation", "workflow", "auto-placement", "extraction")),
    ("evaluation", ("evaluation", "eval", "precision", "recall", "quality", "benchmark")),
    ("developer platform", ("developer", "api", "cli", "platform", "tooling")),
    ("experimentation", ("experiment", "cohort", "analytics", "insights", "metrics")),
    ("enterprise saas", ("enterprise", "customer", "paying customers", "smb", "saas")),
    ("gtm alignment", ("gtm", "sales", "field enablement", "pricing", "packaging")),
    ("cross-functional leadership", ("stakeholder", "cross-functional", "engineering", "executive", "legal")),
    ("operating style", ("ambiguity", "research", "iteration", "strategy", "roadmap")),
]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:36] or "item"


def _stable_id(prefix: str, parts: list[Any]) -> str:
    raw = "|".join(str(p) for p in parts)
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{digest}"


def _source_metadata(path: Path | None = None) -> dict[str, Any]:
    path = path or YAML_PATH
    data = path.read_bytes()
    stat = path.stat()
    return {
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": stat.st_size,
        "mtime": _dt.datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def derive_themes(text: str) -> list[str]:
    normalized = " ".join((text or "").lower().split())
    themes: list[str] = []
    for theme, patterns in THEME_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            themes.append(theme)
    return themes


def _candidate_section(profile: dict) -> dict:
    candidate = profile.setdefault("candidate", {})
    if not isinstance(candidate, dict):
        profile["candidate"] = {}
        return profile["candidate"]
    return candidate


def _experience_list(profile: dict) -> list[dict]:
    candidate = _candidate_section(profile)
    experience = candidate.get("experience")
    if not isinstance(experience, list):
        experience = profile.get("experience")
    if not isinstance(experience, list):
        return []
    return [item for item in experience if isinstance(item, dict)]


def _education_list(profile: dict) -> list[dict]:
    candidate = _candidate_section(profile)
    education = candidate.get("education")
    if not isinstance(education, list):
        education = profile.get("education")
    if not isinstance(education, list):
        return []
    return [item for item in education if isinstance(item, dict)]


def _skills_list(profile: dict) -> list[str]:
    candidate = _candidate_section(profile)
    skills = candidate.get("skills") or profile.get("skills") or []
    return [str(skill) for skill in skills if str(skill).strip()]


def build_evidence_index(profile: dict, *, source_path: Path | None = None) -> dict[str, Any]:
    """Build a generated retrieval index from profile.yaml.

    Unlike annotate_profile_evidence(), this does not copy the full profile.
    It stores only derived lookup records plus a source hash so callers can
    cheaply detect staleness.
    """
    evidence_items: list[dict[str, Any]] = []
    profile_copy = copy.deepcopy(profile or {})
    source_path = source_path or YAML_PATH

    for exp_idx, exp in enumerate(_experience_list(profile_copy)):
        company = str(exp.get("company") or f"experience_{exp_idx + 1}")
        title = str(exp.get("title") or "")
        highlights = exp.get("highlights") or []
        for h_idx, highlight in enumerate(highlights):
            if not isinstance(highlight, str) or not highlight.strip():
                continue
            claim = " ".join(highlight.split())
            evidence_items.append({
                "id": _stable_id(f"exp_{_slug(company)}_{h_idx + 1}", [company, h_idx, claim]),
                "evidence_role": "claim",
                "claim": claim,
                "source": "profile",
                "confidence": "high",
                "context": {
                    "company": company,
                    "title": title,
                },
                "anchors": {
                    "profile_path": f"candidate.experience[{exp_idx}].highlights[{h_idx}]",
                    "resume_highlight_index": h_idx,
                },
                "themes": derive_themes(claim),
            })

    for edu_idx, edu in enumerate(_education_list(profile_copy)):
        degree = str(edu.get("degree") or "")
        institution = str(edu.get("institution") or "")
        text = f"{degree} {institution}".strip()
        if not text:
            continue
        themes = derive_themes(text)
        if "data science" in text.lower() and "data governance" not in themes:
            themes.extend(["enterprise data", "data modeling", "data governance"])
        if not themes:
            continue
        evidence_items.append({
            "id": _stable_id(f"edu_{_slug(institution or degree)}", [institution, degree]),
            "evidence_role": "claim",
            "claim": text,
            "source": "profile",
            "confidence": "medium",
            "context": {
                "institution": institution,
                "degree": degree,
            },
            "anchors": {
                "profile_path": f"candidate.education[{edu_idx}]",
            },
            "validation": {"status": "profile_attested", "external_url": None},
            "themes": sorted(set(themes)),
        })

    skill_index = [
        {
            "skill": skill,
            "source": "profile",
            "evidence_role": "retrieval_hint",
            "themes": derive_themes(skill),
        }
        for skill in _skills_list(profile_copy)
    ]

    return {
        "schema_version": 1,
        "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "generated_from": str(source_path),
        "source": _source_metadata(source_path),
        "evidence_items": evidence_items,
        "skill_index": skill_index,
        "theme_taxonomy": [theme for theme, _patterns in THEME_PATTERNS],
    }


def _default_index_path() -> Path:
    return DATA_DIR / "profile.evidence.index.yaml"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def write_evidence_index(output: str | None = None, *, allow_unsafe_path: bool = False) -> Path:
    profile = load_profile_yaml()
    if not profile:
        raise SystemExit("No profile.yaml found. Run `metis init` first.")
    index = build_evidence_index(profile)
    out_path = Path(output).expanduser() if output else _default_index_path()
    if not allow_unsafe_path and not _is_relative_to(out_path, DATA_DIR):
        raise ValueError("Evidence index output must be under METIS_DATA_DIR.")
    out_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    import yaml

    out_path.write_text(yaml.dump(index, allow_unicode=True, sort_keys=False), encoding="utf-8")
    out_path.chmod(0o600)
    return out_path


def evidence_index_is_stale(path: str | Path | None = None) -> bool:
    index_path = Path(path).expanduser() if path else _default_index_path()
    if not index_path.exists() or not YAML_PATH.exists():
        return True
    try:
        import yaml

        index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        indexed_sha = ((index.get("source") or {}).get("sha256"))
        return indexed_sha != _source_metadata(YAML_PATH)["sha256"]
    except Exception:
        return True


def ensure_evidence_index(path: str | Path | None = None) -> Path:
    index_path = Path(path).expanduser() if path else _default_index_path()
    if evidence_index_is_stale(index_path):
        return write_evidence_index(str(index_path))
    return index_path
