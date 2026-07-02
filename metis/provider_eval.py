"""Offline comparison helpers for provider bakeoffs.

These functions compare normalized Metis eval objects. They do not call model
APIs; live provider runs should record raw outputs separately, then replay those
outputs through this module for stable regression checks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .contracts import (
    REQUIRED_DIMENSIONS,
    VALID_SENTIMENTS,
    VALID_VERDICTS,
    validate_eval_schema,
)


@dataclass(frozen=True)
class ProviderComparison:
    total: int
    verdict_matches: int
    threshold_flips: int
    avg_score_delta: float
    max_score_delta: int
    top_n_overlap: int


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
