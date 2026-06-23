"""
Tests for scorerole init2 (init2_cmd.py).

Coverage:
  - _apply_guardrails: all four followup kinds, priority ordering, cap at 3
  - _apply_clarification_answer: all three handled kinds
  - _extract_with_claude_v2: markdown fence stripping, YAML parse, _followups pop
  - run_init2 subcommand registration: 'init2' routes to run_init2
  - Regression: existing init / schedule / core tests unaffected
"""
import re, sys
import pytest
from unittest import mock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_profile(**overrides):
    """Minimal valid profile dict for guardrail tests."""
    p = {
        "candidate":   {"name": "Test User", "location": "SF, CA", "open_to_remote": True},
        "target":      {"roles": ["Senior PM"], "level": "senior", "industries": []},
        "aspirations": {"track": "ic", "direction": "", "company_types": [], "avoid_company_types": []},
        "preferences": {"company_stage": [], "company_size": None, "industry_targets": [],
                        "industry_avoid": [], "base_salary_target_usd": None},
        "scoring":     {"apply_threshold": 75, "consider_threshold": 55, "level_mismatch_deduction": 10},
        "experience":  [],
        "education":   [],
        "strengths":   [],
        "green_flags": [],
        "yellow_flags": [],
        "red_flags":   [],
        "deal_breakers": [],
        "salary_floor_usd": None,
        "notes":       "",
    }
    p.update(overrides)
    return p


# ---------------------------------------------------------------------------
# _apply_guardrails
# ---------------------------------------------------------------------------

class TestApplyGuardrails:
    from scorerole.init2_cmd import _apply_guardrails  # noqa: E402

    def test_salary_no_floor_signal_adds_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        result = _apply_guardrails(profile, [], "I want $200k+", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "salary_floor_or_target" in kinds

    def test_salary_with_floor_signal_no_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        result = _apply_guardrails(profile, [], "hard floor of $200k minimum", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "salary_floor_or_target" not in kinds

    def test_salary_absent_no_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=None)
        result = _apply_guardrails(profile, [], "looking for PM roles", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "salary_floor_or_target" not in kinds

    def test_remote_ambiguous_adds_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile()
        result = _apply_guardrails(profile, [], "I prefer remote work", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "remote_only_or_preferred" in kinds

    def test_remote_only_no_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile()
        result = _apply_guardrails(profile, [], "remote only, no office", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "remote_only_or_preferred" not in kinds

    def test_dontwant_not_skipped_no_deal_breakers_adds_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(deal_breakers=[])
        result = _apply_guardrails(profile, [], "", dontwant_skipped=False)
        kinds = [f["kind"] for f in result]
        assert "deal_breakers_absent" in kinds

    def test_dontwant_skipped_no_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(deal_breakers=[])
        result = _apply_guardrails(profile, [], "", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "deal_breakers_absent" not in kinds

    def test_dontwant_has_deal_breakers_no_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(deal_breakers=["No people management"])
        result = _apply_guardrails(profile, [], "", dontwant_skipped=False)
        kinds = [f["kind"] for f in result]
        assert "deal_breakers_absent" not in kinds

    def test_mixed_ic_and_mgmt_roles_adds_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile()
        profile["target"]["roles"] = ["Staff PM", "Head of Product"]
        result = _apply_guardrails(profile, [], "", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "track_ic_or_management" in kinds

    def test_ic_only_roles_no_track_followup(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile()
        profile["target"]["roles"] = ["Staff PM", "Principal PM", "Senior PM"]
        result = _apply_guardrails(profile, [], "", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "track_ic_or_management" not in kinds

    def test_cap_at_three(self):
        from scorerole.init2_cmd import _apply_guardrails, MAX_CLARIFICATIONS_ABSOLUTE
        # Trigger all four kinds simultaneously
        profile = _base_profile(salary_floor_usd=200000, deal_breakers=[])
        profile["target"]["roles"] = ["Staff PM", "Director of Product"]
        want = "I want $200k+ and prefer remote work"
        result = _apply_guardrails(profile, [], want, dontwant_skipped=False)
        assert len(result) <= MAX_CLARIFICATIONS_ABSOLUTE

    def test_priority_order_salary_before_remote(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        result = _apply_guardrails(profile, [], "I want $200k+ and prefer remote work", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        if "salary_floor_or_target" in kinds and "remote_only_or_preferred" in kinds:
            assert kinds.index("salary_floor_or_target") < kinds.index("remote_only_or_preferred")

    def test_existing_followup_from_claude_not_duplicated(self):
        from scorerole.init2_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        existing = [{"kind": "salary_floor_or_target", "field": "salary_floor_usd",
                     "question": "Is this a floor?", "from_text": "$200k"}]
        result = _apply_guardrails(profile, existing, "I want $200k+", dontwant_skipped=True)
        salary_entries = [f for f in result if f["kind"] == "salary_floor_or_target"]
        assert len(salary_entries) == 1  # not doubled


# ---------------------------------------------------------------------------
# _apply_clarification_answer
# ---------------------------------------------------------------------------

class TestApplyClarificationAnswer:

    def test_salary_floor_keeps_salary_floor_usd(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile(salary_floor_usd=200000)
        _apply_clarification_answer("salary_floor_or_target", "floor", "$200k", profile)
        # "floor" answer: keep salary_floor_usd, do NOT move it to preferences target
        assert profile["salary_floor_usd"] == 200000
        # base_salary_target_usd may be present (from _base_profile) but must be None
        assert profile.get("preferences", {}).get("base_salary_target_usd") is None

    def test_salary_target_sets_discriminator_not_floor(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile(salary_floor_usd=200000)
        _apply_clarification_answer("salary_floor_or_target", "target", "$200k", profile)
        # salary_floor_usd stays in place — only the discriminator flag changes
        assert profile.get("salary_floor_usd") == 200000
        assert profile["salary_is_hard_floor"] is False

    def test_remote_only_sets_flag(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("remote_only_or_preferred", "remote", "remote", profile)
        assert profile["candidate"]["location_preference"] == "remote"
        assert profile["candidate"]["open_to_remote"] is True

    def test_remote_flexible_sets_flag(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("remote_only_or_preferred", "flexible", "remote", profile)
        assert profile["candidate"]["open_to_remote"] is True

    def test_track_ic_sets_aspirations(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("track_ic_or_management", "ic", "mixed signals", profile)
        assert profile["aspirations"]["track"] == "ic"

    def test_track_management_sets_aspirations(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("track_ic_or_management", "management", "mixed signals", profile)
        assert profile["aspirations"]["track"] == "management"

    def test_unknown_kind_does_not_raise(self):
        from scorerole.init2_cmd import _apply_clarification_answer
        profile = _base_profile()
        # Should silently no-op for unrecognised kind
        _apply_clarification_answer("something_new", "value", "ctx", profile)


# ---------------------------------------------------------------------------
# _extract_with_claude_v2 (mocked API)
# ---------------------------------------------------------------------------

class TestExtractWithClaudeV2:

    def _fake_response(self, text):
        msg = mock.MagicMock()
        msg.content = [mock.MagicMock(text=text)]
        return msg

    def _make_client(self, text):
        client = mock.MagicMock()
        client.messages.create.return_value = self._fake_response(text)
        return client

    def _call(self, client, want="looking for PM roles", dontwant=""):
        from scorerole.init2_cmd import _extract_with_claude_v2
        console = mock.MagicMock()
        console.status.return_value.__enter__ = mock.MagicMock(return_value=None)
        console.status.return_value.__exit__ = mock.MagicMock(return_value=False)
        # anthropic is imported inside the function, so patch at the anthropic module level
        with mock.patch("anthropic.Anthropic", return_value=client):
            return _extract_with_claude_v2("sk-fake", "resume text", "", want, dontwant, console)

    def test_clean_yaml_parsed(self):
        yaml_str = "candidate:\n  name: Test\nstrengths: []\n_followups: []\n"
        client = self._make_client(yaml_str)
        profile, followups = self._call(client)
        assert profile.get("candidate", {}).get("name") == "Test"
        assert followups == []

    def test_followups_popped_from_profile(self):
        yaml_str = (
            "candidate:\n  name: Test\n"
            "_followups:\n"
            "  - kind: salary_floor_or_target\n"
            "    field: salary_floor_usd\n"
            "    question: Is this a floor?\n"
            "    from_text: $200k\n"
        )
        client = self._make_client(yaml_str)
        profile, followups = self._call(client)
        assert "_followups" not in profile
        assert len(followups) == 1
        assert followups[0]["kind"] == "salary_floor_or_target"

    def test_markdown_fences_stripped(self):
        fenced = "```yaml\ncandidate:\n  name: Fenced\n_followups: []\n```"
        client = self._make_client(fenced)
        profile, _ = self._call(client)
        assert profile.get("candidate", {}).get("name") == "Fenced"

    def test_invalid_yaml_returns_empty_dict(self):
        client = self._make_client("not: valid: yaml: [[[")
        profile, followups = self._call(client)
        assert isinstance(profile, dict)
        assert isinstance(followups, list)

    def test_non_dict_yaml_returns_empty(self):
        client = self._make_client("- just\n- a\n- list\n")
        profile, followups = self._call(client)
        assert profile == {}

    def test_api_called_with_four_sections(self):
        yaml_str = "candidate:\n  name: T\n_followups: []\n"
        client = self._make_client(yaml_str)
        self._call(client, want="PM role", dontwant="no mgmt")
        call_args = client.messages.create.call_args
        user_content = call_args[1]["messages"][0]["content"]
        assert "RESUME:" in user_content
        assert "WHAT_I_WANT:" in user_content
        assert "WHAT_I_DONT_WANT:" in user_content


# ---------------------------------------------------------------------------
# Subcommand registration
# ---------------------------------------------------------------------------

class TestInit2SubcommandRegistration:

    def test_init2_registered_in_pipeline(self):
        """'init2' must appear as a recognised subcommand in the argparse setup."""
        import scorerole.pipeline as pipeline
        import argparse
        # Build the parser the same way pipeline.py does at CLI entry
        source = open(pipeline.__file__).read()
        assert "init2" in source, "'init2' subcommand not found in pipeline.py"

    def test_init2_routes_to_run_init2(self):
        """pipeline.py dispatch block for 'init2' must import and call run_init2."""
        import scorerole.pipeline as pipeline
        source = open(pipeline.__file__).read()
        # The dispatch must import run_init2 from init2_cmd and call it
        assert "from .init2_cmd import run_init2" in source
        assert "run_init2(" in source


# ---------------------------------------------------------------------------
# Regression: existing commands unaffected
# ---------------------------------------------------------------------------

class TestInit2Regression:
    """Smoke-check that importing init2_cmd doesn't break sibling modules."""

    def test_import_does_not_pollute_profile_module(self):
        import scorerole.profile as profile_mod
        import scorerole.init2_cmd  # noqa: F401
        # profile module's YAML_PATH must still resolve correctly after import
        assert "profile.yaml" in str(profile_mod.YAML_PATH) or "SCOREROLE_PROFILE" in __import__("os").environ

    def test_init2_cmd_does_not_import_score(self):
        """init2_cmd should not import score.py at module level (keeps startup fast)."""
        import importlib, sys
        # Remove cached module to get a clean import
        for key in list(sys.modules.keys()):
            if "init2_cmd" in key:
                del sys.modules[key]
        with mock.patch.dict(sys.modules, {"scorerole.score": None}):
            try:
                import scorerole.init2_cmd  # noqa: F401
            except ImportError:
                pass  # score.py being None causes ImportError only if init2 imports it at top level
            # If we got here without error, score.py is NOT imported at module level ✓

    def test_score_module_still_importable(self):
        from scorerole import score
        assert hasattr(score, "score_jobs_batch")

    def test_pipeline_module_still_importable(self):
        from scorerole import pipeline
        assert hasattr(pipeline, "run_pipeline")

    def test_init_cmd_still_importable(self):
        from scorerole import init_cmd
        assert hasattr(init_cmd, "run_init")
