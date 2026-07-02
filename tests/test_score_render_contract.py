"""Contract tests for eval objects emitted by score.py and consumed by render.py."""
from __future__ import annotations

from typing import get_args, get_type_hints

from metis.contracts import (
    REQUIRED_DIMENSIONS,
    VALID_SENTIMENTS,
    VALID_VERDICTS,
    validate_eval_schema,
)
from metis.types import Tag


def _canonical_eval():
    return {
        "score": 82,
        "verdict": "apply",
        "dimensions": [
            {"name": name, "score": 82, "rationale": "Relevant evidence"}
            for name in REQUIRED_DIMENSIONS
        ],
        "leveragePoints": ["Relevant scope match", "Clear domain adjacency"],
        "frictionPoints": ["Compensation is not disclosed"],
        "tags": [{"text": "comp: undisclosed", "sentiment": "amber"}],
    }


def test_eval_contract_accepts_canonical_score_render_shape():
    result = validate_eval_schema(_canonical_eval())

    assert result.valid is True
    assert result.errors == []


def test_eval_contract_allows_filtered_shape_split_before_render():
    result = validate_eval_schema({
        "score": 0,
        "verdict": "filtered",
        "dimensions": [],
        "leveragePoints": [],
        "frictionPoints": [],
        "tags": [{"text": "deal breaker: industry", "sentiment": "red"}],
    })

    assert result.valid is True
    assert result.errors == []


def test_eval_contract_allows_no_friction_for_apply_or_consider():
    ev = _canonical_eval()
    ev["frictionPoints"] = []

    result = validate_eval_schema(ev)

    assert result.valid is True
    assert result.errors == []


def test_eval_contract_requires_skip_reason_for_skipped_roles():
    ev = _canonical_eval()
    ev["verdict"] = "skipped"
    ev["frictionPoints"] = []

    result = validate_eval_schema(ev)

    assert result.valid is False
    assert any("skip reason" in err for err in result.errors)


def test_eval_contract_rejects_legacy_yellow_sentiment():
    ev = _canonical_eval()
    ev["tags"] = [{"text": "legacy color", "sentiment": "yellow"}]

    result = validate_eval_schema(ev)

    assert result.valid is False
    assert any("invalid tag sentiment" in err for err in result.errors)


def test_typed_tag_sentiments_match_runtime_contract():
    sentiment_type = get_type_hints(Tag)["sentiment"]

    assert set(get_args(sentiment_type)) == VALID_SENTIMENTS


def test_verdict_contract_stays_score_render_only():
    assert VALID_VERDICTS == {"apply", "consider", "skipped", "filtered"}
