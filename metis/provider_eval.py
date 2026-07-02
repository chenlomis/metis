"""Offline comparison helpers for provider bakeoffs.

These functions compare normalized Metis eval objects. They do not call model
APIs; live provider runs should record raw outputs separately, then replay those
outputs through this module for stable regression checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


REQUIRED_DIMENSIONS = [
    "seniority_scope",
    "experience_relevance",
    "compensation_fit",
    "culture_values",
    "domain_background",
    "company_stage",
]
VALID_VERDICTS = {"apply", "consider", "skipped", "filtered"}
VALID_SENTIMENTS = {"green", "amber", "red"}


@dataclass(frozen=True)
class EvalSchemaResult:
    valid: bool
    errors: list[str]


@dataclass(frozen=True)
class ProviderComparison:
    total: int
    verdict_matches: int
    threshold_flips: int
    avg_score_delta: float
    max_score_delta: int
    top_n_overlap: int


def validate_eval_schema(eval_obj: dict[str, Any]) -> EvalSchemaResult:
    errors: list[str] = []

    verdict = eval_obj.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"invalid verdict: {verdict!r}")

    score = eval_obj.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        errors.append("score must be an integer from 0 to 100")

    dims = eval_obj.get("dimensions", [])
    dim_names = [d.get("name") for d in dims if isinstance(d, dict)]
    if dim_names != REQUIRED_DIMENSIONS:
        errors.append("dimensions must match the canonical order")

    leverage = eval_obj.get("leveragePoints", [])
    if not isinstance(leverage, list) or len(leverage) != 2:
        errors.append("leveragePoints must contain exactly 2 items")

    friction = eval_obj.get("frictionPoints", [])
    if not isinstance(friction, list) or len(friction) != 1:
        errors.append("frictionPoints must contain exactly 1 item")

    tags = eval_obj.get("tags", [])
    if not isinstance(tags, list):
        errors.append("tags must be a list")
    else:
        for tag in tags:
            if not isinstance(tag, dict):
                errors.append("tags must contain objects")
                continue
            if tag.get("sentiment") not in VALID_SENTIMENTS:
                errors.append(f"invalid tag sentiment: {tag.get('sentiment')!r}")

    return EvalSchemaResult(valid=not errors, errors=errors)


def _bucket(score: int, *, apply_threshold: int, consider_threshold: int) -> str:
    if score >= apply_threshold:
        return "apply"
    if score >= consider_threshold:
        return "consider"
    return "skipped"


def compare_provider_runs(
    baseline: list[dict[str, Any]],
    candidate: list[dict[str, Any]],
    *,
    apply_threshold: int = 75,
    consider_threshold: int = 55,
    top_n: int = 5,
) -> ProviderComparison:
    """Compare two ordered provider eval runs for the same fixture jobs."""
    total = min(len(baseline), len(candidate))
    if total == 0:
        return ProviderComparison(0, 0, 0, 0.0, 0, 0)

    verdict_matches = 0
    threshold_flips = 0
    score_deltas: list[int] = []

    for base, cand in zip(baseline[:total], candidate[:total]):
        if base.get("verdict") == cand.get("verdict"):
            verdict_matches += 1
        base_score = int(base.get("score", 0) or 0)
        cand_score = int(cand.get("score", 0) or 0)
        score_deltas.append(abs(base_score - cand_score))
        if _bucket(base_score, apply_threshold=apply_threshold, consider_threshold=consider_threshold) != _bucket(
            cand_score,
            apply_threshold=apply_threshold,
            consider_threshold=consider_threshold,
        ):
            threshold_flips += 1

    def ranked_ids(items: list[dict[str, Any]]) -> list[Any]:
        return [
            item.get("fixture_id", idx)
            for idx, item in enumerate(
                sorted(items[:total], key=lambda x: int(x.get("score", 0) or 0), reverse=True)
            )
        ][:top_n]

    base_top = set(ranked_ids(baseline))
    cand_top = set(ranked_ids(candidate))

    return ProviderComparison(
        total=total,
        verdict_matches=verdict_matches,
        threshold_flips=threshold_flips,
        avg_score_delta=round(sum(score_deltas) / total, 2),
        max_score_delta=max(score_deltas),
        top_n_overlap=len(base_top & cand_top),
    )

