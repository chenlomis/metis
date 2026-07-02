from metis.provider_eval import compare_provider_runs, validate_eval_schema


def _eval(score=80, verdict="apply", fixture_id="job-1"):
    return {
        "fixture_id": fixture_id,
        "score": score,
        "verdict": verdict,
        "dimensions": [
            {"name": "seniority_scope", "score": score},
            {"name": "experience_relevance", "score": score},
            {"name": "compensation_fit", "score": score},
            {"name": "culture_values", "score": score},
            {"name": "domain_background", "score": score},
            {"name": "company_stage", "score": score},
        ],
        "leveragePoints": ["Relevant scope match", "Clear domain adjacency"],
        "frictionPoints": ["Compensation is not disclosed"],
        "tags": [{"text": "comp: undisclosed", "sentiment": "amber"}],
    }


def test_validate_eval_schema_accepts_canonical_eval():
    result = validate_eval_schema(_eval())
    assert result.valid is True
    assert result.errors == []


def test_validate_eval_schema_rejects_contract_drift():
    ev = _eval()
    ev["verdict"] = "yes"
    ev["dimensions"] = list(reversed(ev["dimensions"]))
    ev["leveragePoints"] = ["Only one"]

    result = validate_eval_schema(ev)

    assert result.valid is False
    assert any("invalid verdict" in err for err in result.errors)
    assert any("dimensions" in err for err in result.errors)
    assert any("leveragePoints" in err for err in result.errors)


def test_compare_provider_runs_tracks_decision_drift():
    baseline = [
        _eval(82, "apply", "a"),
        _eval(61, "consider", "b"),
        _eval(43, "skipped", "c"),
    ]
    candidate = [
        _eval(78, "apply", "a"),
        _eval(52, "skipped", "b"),
        _eval(45, "skipped", "c"),
    ]

    result = compare_provider_runs(baseline, candidate, top_n=2)

    assert result.total == 3
    assert result.verdict_matches == 2
    assert result.threshold_flips == 1
    assert result.max_score_delta == 9
    assert result.top_n_overlap == 2

