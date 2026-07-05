"""Tests for prompts.py — identity templates, context synthesis, assemblers.

Philosophy: prompts are contract surfaces. These tests catch regressions
where a refactor silently drops the identity, omits profile sections, or
bakes in a hardcoded candidate name.
"""
import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FULL_PROFILE = {
    "candidate": {
        "name": "Alex Rivera",
        "location": "San Francisco, CA",
        "location_preference": "flexible",
        "strengths": [
            "Built RAG pipeline for 400+ enterprise customers",
            "Led 3-person ML platform team at Series B startup",
            "Production model evaluation at scale",
        ],
    },
    "target": {
        "roles": ["Staff Product Manager", "Principal PM"],
        "level": "Staff/Principal",
    },
    "aspirations": {
        "track": "ic",
        "direction": "AI infrastructure and developer tooling",
        "company_types": ["growth-stage AI-native", "Series B-D"],
        "avoid_company_types": ["pure B2C consumer", "ad-tech"],
    },
    "preferences": {
        "company_stage": ["series_b", "series_c"],
        "company_scale": "enterprise",
        "team_environment": "small-team",
        "industry_targets": ["AI infrastructure", "developer tools"],
        "industry_avoid": ["adtech", "gaming"],
    },
    "deal_breakers": ["management-only roles", "no-AI product"],
}

MINIMAL_PROFILE: dict = {}


# ---------------------------------------------------------------------------
# build_candidate_context
# ---------------------------------------------------------------------------

class TestBuildCandidateContext:

    def test_includes_candidate_name(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "Alex Rivera" in result

    def test_includes_target_level_and_roles(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "Staff/Principal" in result
        assert "Staff Product Manager" in result

    def test_includes_company_preferences(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "growth-stage AI-native" in result
        assert "pure B2C consumer" in result

    def test_includes_deal_breakers(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "management-only roles" in result

    def test_includes_strengths(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "RAG pipeline" in result

    def test_strengths_all_included(self):
        from metis.prompts import build_candidate_context
        profile = {**FULL_PROFILE, "candidate": {
            **FULL_PROFILE["candidate"],
            "strengths": [f"strength {i}" for i in range(10)],
        }}
        result = build_candidate_context(profile)
        # All strengths are included — no positional cap (scoring selects contextually)
        key_line = next(l for l in result.splitlines() if "Key strengths" in l)
        assert "strength 0" in key_line
        assert "strength 9" in key_line

    def test_minimal_profile_does_not_crash(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(MINIMAL_PROFILE)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_minimal_profile_uses_fallback_name(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(MINIMAL_PROFILE)
        assert "the candidate" in result

    def test_no_hardcoded_candidate_names(self):
        """Template must not contain hardcoded user names — OSS contract."""
        from metis.prompts import build_candidate_context
        result = build_candidate_context(MINIMAL_PROFILE)
        for forbidden in ("Lomis", "Lomis Chen", "chenlomis"):
            assert forbidden not in result

    def test_industry_targets_and_avoids(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "AI infrastructure" in result
        assert "adtech" in result

    def test_company_scale_and_team_environment(self):
        from metis.prompts import build_candidate_context
        result = build_candidate_context(FULL_PROFILE)
        assert "Company/customer scale: enterprise" in result
        assert "Team environment: small-team" in result


# ---------------------------------------------------------------------------
# SCORING_IDENTITY
# ---------------------------------------------------------------------------

class TestScoringIdentity:

    def test_has_candidate_name_placeholder(self):
        from metis.prompts import SCORING_IDENTITY
        assert "{candidate_name}" in SCORING_IDENTITY

    def test_no_hardcoded_names(self):
        from metis.prompts import SCORING_IDENTITY
        for name in ("Lomis", "Lomis Chen", "Alex", "Rivera"):
            assert name not in SCORING_IDENTITY

    def test_formats_with_name(self):
        from metis.prompts import SCORING_IDENTITY
        result = SCORING_IDENTITY.format(candidate_name="Sam Park")
        assert "Sam Park" in result
        assert "{candidate_name}" not in result

    def test_contains_key_standards(self):
        from metis.prompts import SCORING_IDENTITY
        result = SCORING_IDENTITY.format(candidate_name="Sam Park")
        assert "traceable" in result.lower() or "traceab" in result.lower()
        assert "generic" in result.lower()
        assert "cheerleading" in result.lower() or "cheerlead" in result.lower()


# ---------------------------------------------------------------------------
# FEEDBACK_IDENTITY
# ---------------------------------------------------------------------------

class TestFeedbackIdentity:

    def test_has_candidate_name_placeholder(self):
        from metis.prompts import FEEDBACK_IDENTITY
        assert "{candidate_name}" in FEEDBACK_IDENTITY

    def test_no_hardcoded_names(self):
        from metis.prompts import FEEDBACK_IDENTITY
        for name in ("Lomis", "Lomis Chen"):
            assert name not in FEEDBACK_IDENTITY

    def test_formats_with_name(self):
        from metis.prompts import FEEDBACK_IDENTITY
        result = FEEDBACK_IDENTITY.format(candidate_name="Sam Park")
        assert "Sam Park" in result
        assert "{candidate_name}" not in result

    def test_contains_zero_inference_rule(self):
        from metis.prompts import FEEDBACK_IDENTITY
        result = FEEDBACK_IDENTITY.format(candidate_name="Sam Park")
        assert "infer" in result.lower()

    def test_contains_empty_conflicts_rule(self):
        from metis.prompts import FEEDBACK_IDENTITY
        result = FEEDBACK_IDENTITY.format(candidate_name="Sam Park")
        assert "no prior statement" in result.lower() or "empty" in result.lower()


# ---------------------------------------------------------------------------
# scoring_system_prompt
# ---------------------------------------------------------------------------

class TestScoringSystemPrompt:

    def _make_prompt(self, feedback=None):
        from metis.prompts import scoring_system_prompt
        return scoring_system_prompt(
            profile=FULL_PROFILE,
            rendered_profile="RENDERED PROFILE BLOCK",
            bullet_guide="BULLET GUIDE BLOCK",
            score_suffix="SCORE SUFFIX BLOCK",
            feedback_text=feedback,
        )

    def test_domain_taxonomy_included_when_provided(self):
        from metis.prompts import scoring_system_prompt

        result = scoring_system_prompt(
            profile=FULL_PROFILE,
            rendered_profile="RENDERED PROFILE BLOCK",
            bullet_guide="BULLET GUIDE BLOCK",
            score_suffix="SCORE SUFFIX BLOCK",
            domain_taxonomy_text="cloud infra hard barriers include Kubernetes",
        )

        assert "DOMAIN TRANSFERABILITY REFERENCE" in result
        assert "Kubernetes" in result

    def test_contains_identity(self):
        result = self._make_prompt()
        assert "ruthlessly" in result or "headhunter" in result

    def test_contains_candidate_name_in_identity(self):
        result = self._make_prompt()
        assert "Alex Rivera" in result

    def test_contains_candidate_brief(self):
        result = self._make_prompt()
        assert "CLIENT BRIEF" in result
        assert "Staff/Principal" in result

    def test_contains_rendered_profile(self):
        result = self._make_prompt()
        assert "RENDERED PROFILE BLOCK" in result

    def test_contains_bullet_guide(self):
        result = self._make_prompt()
        assert "BULLET GUIDE BLOCK" in result

    def test_contains_score_suffix(self):
        result = self._make_prompt()
        assert "SCORE SUFFIX BLOCK" in result

    def test_feedback_included_when_provided(self):
        result = self._make_prompt(feedback="prefer growth-stage healthcare roles")
        assert "prefer growth-stage healthcare roles" in result
        assert "CALIBRATION FEEDBACK" in result

    def test_feedback_absent_when_none(self):
        result = self._make_prompt(feedback=None)
        assert "CALIBRATION FEEDBACK" not in result

    def test_identity_precedes_profile(self):
        """Identity block must come before the profile detail."""
        result = self._make_prompt()
        identity_pos = result.find("headhunter")
        profile_pos  = result.find("RENDERED PROFILE BLOCK")
        assert identity_pos < profile_pos

    def test_brief_precedes_profile(self):
        """Client brief must come before the full rendered profile."""
        result = self._make_prompt()
        brief_pos   = result.find("CLIENT BRIEF")
        profile_pos = result.find("RENDERED PROFILE BLOCK")
        assert brief_pos < profile_pos


# ---------------------------------------------------------------------------
# feedback_system_prompt
# ---------------------------------------------------------------------------

class TestFeedbackSystemPrompt:

    def test_returns_string(self):
        from metis.prompts import feedback_system_prompt
        result = feedback_system_prompt("Sam Park")
        assert isinstance(result, str)

    def test_contains_candidate_name(self):
        from metis.prompts import feedback_system_prompt
        result = feedback_system_prompt("Sam Park")
        assert "Sam Park" in result

    def test_no_placeholder_remaining(self):
        from metis.prompts import feedback_system_prompt
        result = feedback_system_prompt("Sam Park")
        assert "{candidate_name}" not in result

    def test_contains_grounding_rules(self):
        from metis.prompts import feedback_system_prompt
        result = feedback_system_prompt("Sam Park")
        assert "infer" in result.lower()
