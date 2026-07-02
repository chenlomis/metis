"""Shared contracts for objects that cross Metis module boundaries."""
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


def validate_eval_schema(eval_obj: dict[str, Any]) -> EvalSchemaResult:
    """Validate the score.py eval shape consumed by render.py."""
    errors: list[str] = []

    verdict = eval_obj.get("verdict")
    if verdict not in VALID_VERDICTS:
        errors.append(f"invalid verdict: {verdict!r}")

    score = eval_obj.get("score")
    if not isinstance(score, int) or not 0 <= score <= 100:
        errors.append("score must be an integer from 0 to 100")

    dims = eval_obj.get("dimensions", [])
    leverage = eval_obj.get("leveragePoints", [])
    friction = eval_obj.get("frictionPoints", [])

    if verdict == "filtered":
        if score != 0:
            errors.append("filtered evals must have score 0")
        if dims != []:
            errors.append("filtered evals must not contain dimensions")
        if leverage != []:
            errors.append("filtered evals must not contain leveragePoints")
        if friction != []:
            errors.append("filtered evals must not contain frictionPoints")
    else:
        dim_names = [d.get("name") for d in dims if isinstance(d, dict)]
        if dim_names != REQUIRED_DIMENSIONS:
            errors.append("dimensions must match the canonical order")

        if not isinstance(leverage, list) or len(leverage) != 2:
            errors.append("leveragePoints must contain exactly 2 items")

        if not isinstance(friction, list) or len(friction) > 1:
            errors.append("frictionPoints must contain 0 or 1 items")
        elif verdict == "skipped" and not friction:
            errors.append("skipped evals must contain a frictionPoints skip reason")

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
