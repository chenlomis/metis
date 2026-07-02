"""Tests for deterministic profile normalization."""

from metis.normalization import (
    apply_step_text_backfills,
    classify_role_family,
    normalize_company_stage,
    normalize_company_scale,
    normalize_team_environment,
)


def _sparse_profile():
    return {
        "candidate": {"name": "Test User"},
        "target": {"roles": [], "level": None},
        "aspirations": {"track": None, "direction": None, "company_types": [], "avoid_company_types": []},
        "preferences": {"company_stage": [], "company_scale": None, "team_environment": None},
        "inferred": {"customer_types": []},
        "notes": None,
    }


def test_product_pm_backfill_is_role_family_gated():
    profile = apply_step_text_backfills(
        _sparse_profile(),
        (
            "Staff or Principal PM at an AI infrastructure or developer tools company. "
            "Prefer growth-stage, remote-first, small team. "
            "Excited by agentic AI or LLM infra."
        ),
        "",
    )

    assert profile["target"]["role_family"] == "product"
    assert profile["target"]["roles"] == ["Staff PM", "Principal PM"]
    assert profile["target"]["level"] == "staff"
    assert profile["aspirations"]["track"] == "ic"
    assert profile["aspirations"]["direction"] == "agentic AI or LLM infra"
    assert profile["aspirations"]["company_types"] == ["ai-infrastructure", "developer-tools"]
    assert profile["preferences"]["company_stage"] == ["growth-stage"]
    assert profile["preferences"]["team_environment"] == "small-team"
    assert profile["candidate"]["location_preference"] == "remote"
    assert profile["inferred"]["customer_types"] == ["developer"]


def test_engineering_input_does_not_become_pm():
    profile = apply_step_text_backfills(
        _sparse_profile(),
        (
            "Staff Backend Engineer looking for remote work on cloud infrastructure "
            "at a growth-stage company with a small team."
        ),
        "",
    )

    assert profile["target"]["role_family"] == "engineering"
    assert profile["target"]["roles"] == ["Staff Engineer"]
    assert "PM" not in " ".join(profile["target"]["roles"])
    assert profile["target"]["level"] == "staff"
    assert profile["aspirations"]["track"] == "ic"
    assert profile["aspirations"]["company_types"] == ["cloud-infrastructure"]


def test_design_input_does_not_become_pm():
    profile = apply_step_text_backfills(
        _sparse_profile(),
        "Principal Product Designer seeking remote growth-stage consumer product work on a small team.",
        "",
    )

    assert profile["target"]["role_family"] == "design"
    assert profile["target"]["roles"] == ["Principal Designer"]
    assert "PM" not in " ".join(profile["target"]["roles"])
    assert profile["target"]["level"] == "principal"


def test_data_input_does_not_become_pm():
    profile = apply_step_text_backfills(
        _sparse_profile(),
        "Senior Data Scientist interested in analytics platforms for enterprise customers.",
        "",
    )

    assert profile["target"]["role_family"] == "data"
    assert profile["target"]["roles"] == ["Senior Data Scientist"]
    assert "PM" not in " ".join(profile["target"]["roles"])
    assert profile["target"]["level"] == "senior"
    assert profile["inferred"]["customer_types"] == ["b2b", "enterprise"]


def test_role_family_unknown_vs_other_vs_null_semantics():
    assert classify_role_family("") == "unknown"
    assert classify_role_family("I want something founder-in-residence-ish") == "other"
    assert classify_role_family("Legal Counsel for fintech company") == "legal"


def test_stage_scale_and_team_environment_are_distinct():
    assert normalize_company_stage("pre-seed or seed") == "early-stage"
    assert normalize_company_stage("Series B scaling company") == "growth-stage"
    assert normalize_company_stage("public Fortune 500") == "mature"
    assert normalize_company_scale("selling to enterprise customers") == "enterprise"
    assert normalize_team_environment("small tight-knit crew") == "small-team"


def test_step_text_notes_are_preserved_when_llm_returns_partial_notes():
    profile = _sparse_profile()
    profile["notes"] = "Staff or Principal PM at an AI infrastructure company"

    want = (
        "Staff or Principal PM at an AI infrastructure company. "
        "Prefer growth-stage, remote-first, small team."
    )
    out = apply_step_text_backfills(profile, want, "")

    assert out["notes"] == (
        "Staff or Principal PM at an AI infrastructure company\n\n"
        "Staff or Principal PM at an AI infrastructure company. "
        "Prefer growth-stage, remote-first, small team."
    )
    assert out["preferences"]["company_stage"] == ["growth-stage"]
    assert out["preferences"]["team_environment"] == "small-team"
    assert out["candidate"]["location_preference"] == "remote"
