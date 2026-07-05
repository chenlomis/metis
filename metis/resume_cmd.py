from __future__ import annotations

import datetime as _dt
import ipaddress
import json
import os
import re
import socket
from pathlib import Path
from urllib.parse import urlparse
from typing import Any

from .llm import create_llm_client, normalize_provider, provider_api_key_env, resolve_stage_models
from .profile import load_profile_yaml
from .state import RUNS_PATH, _role_hash
from .tailor import (
    assess_hard_technical_gap,
    build_evidence_index,
    build_evidence_units_from_profile_index,
    build_resume_evidence_index,
    build_hard_gap_plan,
    generate_tailoring_plan,
    retrieve_evidence_for_jd,
    write_tailoring_artifacts,
)


_MAX_JD_BYTES = 750_000
_ALLOWED_URL_SCHEMES = {"http", "https"}


def _data_dir() -> Path:
    return Path(os.environ.get("METIS_DATA_DIR", str(Path.home() / ".job_pipeline")))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug[:80] or "role"


def _has_tailoring_source(role: dict[str, Any]) -> bool:
    return bool(role.get("url") or role.get("jd"))


def _is_public_hostname(hostname: str) -> bool:
    if not hostname:
        return False
    lowered = hostname.lower().strip(".")
    if lowered in {"localhost"} or lowered.endswith(".localhost"):
        return False
    try:
        addresses = socket.getaddrinfo(lowered, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    for info in addresses:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def _validate_fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError("Posting URL must use http or https.")
    if not parsed.hostname or not _is_public_hostname(parsed.hostname):
        raise ValueError("Posting URL host is not a public internet host.")
    return url


def _shorten(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _role_label(role: dict[str, Any]) -> str:
    eval_data = role.get("eval") or {}
    score = eval_data.get("score", "")
    verdict = eval_data.get("verdict", "")
    company = _shorten(role.get("company", ""), 22)
    title = _shorten(role.get("title", ""), 58)
    return f"{score}% {verdict:<8} | {company:<22} | {title}"


def _role_key(role: dict[str, Any]) -> str:
    return str(role.get("role_hash") or _role_hash(role.get("title", ""), role.get("company", "")))


def _role_date(role: dict[str, Any]) -> str:
    ts = str(role.get("ts") or "")
    return ts[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", ts) else ""


_SELECT_ALL = "__metis_select_all__"
_CANCEL = "__metis_cancel__"


def _load_recent_roles(limit: int = 40) -> list[dict[str, Any]]:
    if not RUNS_PATH.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        verdict = (row.get("eval") or {}).get("verdict")
        if verdict not in {"apply", "consider"}:
            continue
        rows.append(row)

    latest_date = max((_role_date(row) for row in rows), default="")
    if latest_date:
        rows = [row for row in rows if _role_date(row) == latest_date]

    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        deduped[_role_key(row)] = row
    return list(reversed(list(deduped.values())))[:limit]


def _tailoring_status_by_role_hash(data_dir: Path | None = None) -> dict[str, str]:
    root = (data_dir or _data_dir()) / "resume_tailor"
    statuses: dict[str, str] = {}
    if not root.exists():
        return statuses
    for record_path in root.glob("*/*/tailoring_record.json"):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        role = record.get("role") or {}
        role_key = _role_key(role)
        fit = str((((record.get("plan") or {}).get("employer_lens") or {}).get("fit_assessment") or "")).lower()
        if fit.startswith("not_recommended"):
            statuses[role_key] = "not_recommended"
        elif record.get("artifacts") and statuses.get(role_key) != "not_recommended":
            statuses[role_key] = "tailored"
    return statuses


def _eligible_recent_roles(
    roles: list[dict[str, Any]],
    statuses: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    statuses = statuses or {}
    return [
        role for role in roles
        if _has_tailoring_source(role) and statuses.get(_role_key(role)) != "not_recommended"
    ]


def _score(role: dict[str, Any]) -> int:
    try:
        return int((role.get("eval") or {}).get("score") or 0)
    except (TypeError, ValueError):
        return 0


def _sort_roles_for_picker(roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        roles,
        key=lambda role: (_score(role), str(role.get("company", "")), str(role.get("title", ""))),
        reverse=True,
    )


def _expand_selection(selection: list[Any], roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _CANCEL in selection:
        raise SystemExit("Cancelled.")
    if _SELECT_ALL in selection:
        return roles
    by_key = {_role_key(role): role for role in roles}
    return [by_key[value] for value in selection if value in by_key]


def _select_roles_interactively(roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not roles:
        return []
    statuses = _tailoring_status_by_role_hash()
    eligible = _sort_roles_for_picker(_eligible_recent_roles(roles, statuses))
    if not eligible:
        raise SystemExit(
            "Recent Solid/Moderate roles do not include posting URLs yet. "
            "Those rows were likely scored before resume tailoring added URL lineage. "
            "Run `metis` once on this branch so Metis can save posting URLs, then rerun `metis resume tailor`."
        )
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
        from InquirerPy.separator import Separator
        from .theme import console
        from .theme import INQUIRER_STYLE
    except Exception:
        return eligible[:1]

    skipped = len(roles) - len(eligible)
    console.print()
    console.print(
        f"[dim]Choose roles to tailor. {len(eligible)} eligible roles, sorted highest match first.[/dim]"
    )
    if skipped:
        console.print(f"[dim]Skipped {skipped} older trace rows without posting URLs.[/dim]")

    choices = [
        Choice(_SELECT_ALL, f"Select all {len(eligible)} roles"),
        Choice(_CANCEL, "Cancel / exit"),
        Separator(),
        *[
            Choice(
                _role_key(role),
                f"{idx:>2}. {_role_label(role)}"
                + ("  [tailored]" if statuses.get(_role_key(role)) == "tailored" else ""),
            )
            for idx, role in enumerate(eligible, 1)
        ],
    ]
    selected = inquirer.checkbox(
        message="Resume tailoring",
        choices=choices,
        style=INQUIRER_STYLE,
        instruction="Space toggles, Enter confirms",
        validate=lambda result: bool(result),
        invalid_message="Press Space to select at least one role, or Ctrl-C to cancel.",
    ).execute()
    return _expand_selection(selected, eligible)


def _fetch_jd(role: dict[str, Any]) -> str:
    if role.get("jd"):
        return str(role.get("jd") or "")[:5000]
    url = role.get("url") or ""
    if not url:
        return role.get("jd", "")
    try:
        url = _validate_fetch_url(url)
    except ValueError:
        return role.get("jd", "")
    if "linkedin.com/jobs" in url:
        from .sources.linkedin import enrich_jobs
        enriched = enrich_jobs([dict(role)])
        role.update(enriched[0])
        return role.get("jd", "")

    try:
        import httpx
        from bs4 import BeautifulSoup

        with httpx.Client(timeout=15, follow_redirects=True, max_redirects=5) as client:
            response = client.get(url)
        response.raise_for_status()
        final_url = str(response.url)
        _validate_fetch_url(final_url)
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > _MAX_JD_BYTES:
            return role.get("jd", "")
        content = response.content[:_MAX_JD_BYTES + 1]
        if len(content) > _MAX_JD_BYTES:
            return role.get("jd", "")
        text = BeautifulSoup(content, "html.parser").get_text("\n", strip=True)
        role["jd"] = text[:5000]
        return role["jd"]
    except Exception:
        return role.get("jd", "")


def _make_output_dir(base_dir: Path, role: dict[str, Any]) -> Path:
    day = _dt.datetime.now().strftime("%Y%m%d")
    company = _slug(str(role.get("company", ""))).replace("-", "_")
    title = str(role.get("title", "")).lower()
    title = re.sub(r"\b(senior|staff|principal|lead|product|manager|technical|pm)\b", " ", title)
    title_slug = _slug(title).replace("-", "_")
    folder = "_".join(part for part in [company, title_slug[:36]] if part).strip("_")
    return base_dir / "resume_tailor" / day / (folder or "role")


def _load_profile_index_evidence() -> list[Any]:
    try:
        import yaml
        from .profile_evidence import ensure_evidence_index

        index_path = ensure_evidence_index()
        index = yaml.safe_load(index_path.read_text(encoding="utf-8")) or {}
        return build_evidence_units_from_profile_index(index)
    except Exception:
        return []


def _resolve_resume_path(profile: dict, resume_path: str | None = None) -> Path:
    candidates: list[Path] = []
    if resume_path:
        candidates.append(Path(resume_path).expanduser())
    if os.getenv("METIS_RESUME"):
        candidates.append(Path(os.environ["METIS_RESUME"]).expanduser())
    profile_resume = (profile.get("resume") or {}).get("path") if isinstance(profile.get("resume"), dict) else None
    if profile_resume:
        candidates.append(Path(str(profile_resume)).expanduser())
    personal_dir = Path.home() / "Documents" / "personal"
    if personal_dir.exists():
        candidates.extend(sorted(personal_dir.glob("*resume*.docx"), key=lambda p: p.stat().st_mtime, reverse=True))

    for candidate in candidates:
        if candidate.exists() and candidate.suffix.lower() == ".docx":
            return candidate
    raise SystemExit(
        "No source resume DOCX found. Set METIS_RESUME or pass `metis resume tailor --resume PATH`."
    )


def run_resume_tailor(
    *,
    api_key: str | None = None,
    resume_path: str | None = None,
    out_dir: str | None = None,
    limit: int = 40,
    non_interactive: bool = False,
    tailor_all: bool = False,
    top_n: int | None = None,
) -> list[dict[str, str]]:
    profile = load_profile_yaml()
    if not profile:
        raise SystemExit("No profile.yaml found. Run `metis init` first.")
    source_resume_path = _resolve_resume_path(profile, resume_path)

    recent = _load_recent_roles(limit=limit)
    if not recent:
        raise SystemExit(
            "No recent Solid/Moderate roles found in runs.jsonl. "
            "Run `metis` first so resume tailoring has recent roles to use."
        )
    statuses = _tailoring_status_by_role_hash()
    eligible = _sort_roles_for_picker(_eligible_recent_roles(recent, statuses))
    if not eligible:
        raise SystemExit(
            "Recent Solid/Moderate roles do not include posting URLs yet. "
            "Those rows were likely scored before resume tailoring added URL lineage. "
            "Run `metis` once on this branch so Metis can save posting URLs, then rerun `metis resume tailor`."
        )
    if top_n is not None:
        if top_n <= 0:
            raise SystemExit("--top must be a positive integer.")
        roles = eligible[:top_n]
    elif tailor_all:
        roles = eligible
    elif non_interactive or not os.isatty(0):
        raise SystemExit("Run `metis resume tailor --all`, or run interactively to choose roles.")
    else:
        roles = _select_roles_interactively(recent)

    roles = [r for r in roles if _has_tailoring_source(r)]
    if not roles:
        raise SystemExit("No roles selected.")

    provider = normalize_provider(os.getenv("METIS_LLM_PROVIDER", os.getenv("LLM_PROVIDER", "anthropic")))
    model = resolve_stage_models(provider)["model"]
    key = api_key or os.getenv(provider_api_key_env(provider), "")
    client = create_llm_client(provider=provider, api_key=key)

    evidence = build_resume_evidence_index(source_resume_path)
    evidence.extend(_load_profile_index_evidence())
    if not evidence:
        evidence = build_evidence_index(profile)
    if not evidence:
        raise SystemExit("Source resume/profile does not contain evidence to ground tailoring.")

    data_dir = _data_dir()
    base_output_dir = Path(out_dir).expanduser() if out_dir else data_dir
    if not _is_relative_to(base_output_dir, data_dir):
        raise SystemExit("Resume tailoring output must stay under METIS_DATA_DIR.")
    artifacts: list[dict[str, str]] = []
    for role in roles:
        jd_text = _fetch_jd(role)
        if not jd_text:
            raise SystemExit(f"Could not fetch JD text for {role.get('title')} at {role.get('company')}.")
        matches = retrieve_evidence_for_jd(jd_text, evidence)
        technical_gap = assess_hard_technical_gap(jd_text, evidence)
        if technical_gap.get("is_hard_gap"):
            plan = build_hard_gap_plan(role, technical_gap)
        else:
            plan = generate_tailoring_plan(
                client,
                model=model,
                role=role,
                jd_text=jd_text,
                profile=profile,
                evidence_matches=matches,
            )
        written = write_tailoring_artifacts(
            role=role,
            profile=profile,
            jd_text=jd_text,
            evidence_matches=matches,
            plan=plan,
            output_dir=_make_output_dir(base_output_dir, role),
            source_resume_path=source_resume_path,
        )
        artifacts.append({
            "role": f"{role.get('title', '')} at {role.get('company', '')}",
            "clean_resume": str(written.clean_resume_path),
            "review": str(written.review_path),
            "record": str(written.record_path),
        })

    return artifacts
