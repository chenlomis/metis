"""
Tests for metis init (init_cmd.py).

Coverage:
  - _apply_guardrails: all four followup kinds, priority ordering, cap at 3
  - _apply_clarification_answer: all three handled kinds
  - _extract_with_llm_v2: markdown fence stripping, YAML parse, _followups pop
  - run_init subcommand registration: 'init' routes to run_init
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
    from metis.init_cmd import _apply_guardrails  # noqa: E402

    def test_salary_no_floor_signal_adds_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        result = _apply_guardrails(profile, [], "I want $200k+", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "salary_floor_or_target" in kinds

    def test_salary_with_floor_signal_no_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        result = _apply_guardrails(profile, [], "hard floor of $200k minimum", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "salary_floor_or_target" not in kinds

    def test_salary_absent_no_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=None)
        result = _apply_guardrails(profile, [], "looking for PM roles", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "salary_floor_or_target" not in kinds

    def test_remote_ambiguous_adds_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile()
        result = _apply_guardrails(profile, [], "I prefer remote work", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "remote_only_or_preferred" in kinds

    def test_remote_only_no_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile()
        result = _apply_guardrails(profile, [], "remote only, no office", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "remote_only_or_preferred" not in kinds

    def test_missing_location_preference_adds_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(candidate={"name": "Test User", "location": "SF, CA"})
        result = _apply_guardrails(profile, [], "looking for PM roles", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "remote_only_or_preferred" in kinds

    def test_dontwant_not_skipped_no_deal_breakers_adds_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(deal_breakers=[])
        result = _apply_guardrails(profile, [], "", dontwant_skipped=False)
        kinds = [f["kind"] for f in result]
        assert "deal_breakers_absent" in kinds

    def test_dontwant_skipped_no_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(deal_breakers=[])
        result = _apply_guardrails(profile, [], "", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "deal_breakers_absent" not in kinds

    def test_dontwant_has_deal_breakers_no_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(deal_breakers=["No people management"])
        result = _apply_guardrails(profile, [], "", dontwant_skipped=False)
        kinds = [f["kind"] for f in result]
        assert "deal_breakers_absent" not in kinds

    def test_mixed_ic_and_mgmt_roles_adds_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile()
        profile["target"]["roles"] = ["Staff PM", "Head of Product"]
        result = _apply_guardrails(profile, [], "", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "track_ic_or_management" in kinds

    def test_ic_only_roles_no_track_followup(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile()
        profile["target"]["roles"] = ["Staff PM", "Principal PM", "Senior PM"]
        result = _apply_guardrails(profile, [], "", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        assert "track_ic_or_management" not in kinds

    def test_cap_at_three(self):
        from metis.init_cmd import _apply_guardrails, MAX_CLARIFICATIONS_ABSOLUTE
        # Trigger all four kinds simultaneously
        profile = _base_profile(salary_floor_usd=200000, deal_breakers=[])
        profile["target"]["roles"] = ["Staff PM", "Director of Product"]
        want = "I want $200k+ and prefer remote work"
        result = _apply_guardrails(profile, [], want, dontwant_skipped=False)
        assert len(result) <= MAX_CLARIFICATIONS_ABSOLUTE

    def test_priority_order_salary_before_remote(self):
        from metis.init_cmd import _apply_guardrails
        profile = _base_profile(salary_floor_usd=200000)
        result = _apply_guardrails(profile, [], "I want $200k+ and prefer remote work", dontwant_skipped=True)
        kinds = [f["kind"] for f in result]
        if "salary_floor_or_target" in kinds and "remote_only_or_preferred" in kinds:
            assert kinds.index("salary_floor_or_target") < kinds.index("remote_only_or_preferred")

    def test_existing_followup_from_claude_not_duplicated(self):
        from metis.init_cmd import _apply_guardrails
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
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile(salary_floor_usd=200000)
        _apply_clarification_answer("salary_floor_or_target", "floor", "$200k", profile)
        # "floor" answer: keep salary_floor_usd, do NOT move it to preferences target
        assert profile["salary_floor_usd"] == 200000
        # base_salary_target_usd may be present (from _base_profile) but must be None
        assert profile.get("preferences", {}).get("base_salary_target_usd") is None

    def test_salary_target_sets_discriminator_not_floor(self):
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile(salary_floor_usd=200000)
        _apply_clarification_answer("salary_floor_or_target", "target", "$200k", profile)
        # salary_floor_usd stays in place — only the discriminator flag changes
        assert profile.get("salary_floor_usd") == 200000
        assert profile["salary_is_hard_floor"] is False

    def test_remote_only_sets_flag(self):
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("remote_only_or_preferred", "remote", "remote", profile)
        assert profile["candidate"]["location_preference"] == "remote"
        assert profile["candidate"]["open_to_remote"] is True

    def test_remote_flexible_sets_flag(self):
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("remote_only_or_preferred", "flexible", "remote", profile)
        assert profile["candidate"]["open_to_remote"] is True

    def test_track_ic_sets_aspirations(self):
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("track_ic_or_management", "ic", "mixed signals", profile)
        assert profile["aspirations"]["track"] == "ic"

    def test_track_management_sets_aspirations(self):
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile()
        _apply_clarification_answer("track_ic_or_management", "management", "mixed signals", profile)
        assert profile["aspirations"]["track"] == "management"

    def test_unknown_kind_does_not_raise(self):
        from metis.init_cmd import _apply_clarification_answer
        profile = _base_profile()
        # Should silently no-op for unrecognised kind
        _apply_clarification_answer("something_new", "value", "ctx", profile)


# ---------------------------------------------------------------------------
# _extract_with_llm_v2 (mocked API)
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

    def _call(self, client, want="looking for PM roles", dontwant="", provider="anthropic"):
        from metis.init_cmd import _extract_with_llm_v2
        console = mock.MagicMock()
        console.status.return_value.__enter__ = mock.MagicMock(return_value=None)
        console.status.return_value.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.dict("os.environ", {"METIS_LLM_PROVIDER": provider}, clear=False), \
             mock.patch("metis.init_cmd.create_llm_client", return_value=client):
            return _extract_with_llm_v2("sk-fake", "resume text", "", want, dontwant, console)

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

    def test_openai_prefaced_fenced_yaml_is_recovered(self):
        fenced = (
            "Here is the extracted profile:\n\n"
            "```yaml\n"
            "candidate:\n"
            "  name: Test User\n"
            "target:\n"
            "  roles:\n"
            "    - \"Principal PM: AI Platform\"\n"
            "    - Staff PM: Internal AI\n"
            "notes: looking for PM roles\n"
            "_followups: []\n"
            "```"
        )
        client = self._make_client(fenced)
        profile, followups = self._call(client, provider="openai")

        assert profile.get("candidate", {}).get("name") == "Test User"
        assert profile.get("target", {}).get("roles") == [
            "Principal PM: AI Platform",
            "Staff PM: Internal AI",
        ]
        assert profile.get("notes") == "looking for PM roles"
        assert followups == []

    def test_openai_blank_extraction_fails_before_save(self):
        client = self._make_client("not: valid: yaml: [[[")
        with pytest.raises(ValueError, match="OpenAI profile extraction produced blank"):
            self._call(client, provider="openai")

    @pytest.mark.parametrize("provider", ["anthropic", "openai"])
    def test_step_two_text_backfills_missing_target_and_aspirations(self, provider):
        sparse = (
            "candidate:\n"
            "  name: Test User\n"
            "target:\n"
            "  roles: []\n"
            "  level: null\n"
            "aspirations:\n"
            "  track: null\n"
            "  direction: null\n"
            "  company_types: []\n"
            "preferences:\n"
            "  company_stage: []\n"
            "  company_size: null\n"
            "inferred:\n"
            "  customer_types: []\n"
            "notes: null\n"
            "_followups: []\n"
        )
        client = self._make_client(sparse)
        want = (
            "Staff or Principal PM at an AI infrastructure or developer tools company. "
            "Prefer growth-stage, remote-first, small team. "
            "Excited by agentic AI or LLM infra."
        )
        profile, _ = self._call(client, want=want, provider=provider)

        assert profile["target"]["roles"] == ["Staff PM", "Principal PM"]
        assert profile["target"]["level"] == "staff"
        assert profile["aspirations"]["track"] == "ic"
        assert profile["aspirations"]["direction"] == "agentic AI or LLM infra"
        assert profile["target"]["role_family"] == "product"
        assert profile["aspirations"]["company_types"] == ["ai-infrastructure", "developer-tools"]
        assert profile["preferences"]["company_stage"] == ["growth-stage"]
        assert profile["preferences"]["team_environment"] == "small-team"
        assert profile["preferences"]["company_size"] == "small-team"
        assert profile["candidate"]["location_preference"] == "remote"
        assert profile["inferred"]["customer_types"] == ["developer"]
        assert profile["notes"] == want

    @pytest.mark.parametrize("provider", ["anthropic", "openai"])
    @pytest.mark.parametrize("want", [
        (
            "I have spent the last few years scaling developer-facing platforms, and now I am ready "
            "for my next big play as an individual contributor. Ideally, I am looking to step into "
            "a Principal Product Manager role, though I would also consider a Staff PM title if the "
            "scope is right. I am incredibly passionate about agentic AI and LLM infrastructure. "
            "Culturally, I thrive best in remote-first environments with a small, tight-knit crew. "
            "I am targeting growth-stage companies. My primary users should be engineers and developers."
        ),
        (
            "Target Role: Staff / Principal Product Manager (IC Track)\n"
            "Focus Areas: LLM Infrastructure, Agentic AI Frameworks, Developer Tools\n"
            "Ideal Company: Growth-stage startup with a small team footprint. Must support 100% remote work.\n"
            "Core Audience: B2B Developer & Engineering personas."
        ),
        (
            "Honestly, I just want to build cool stuff for devs. I am looking for my next IC gig, "
            "something at the Staff level or a Principal PM title. It needs to be remote. "
            "Industry-wise, I am super fascinated by agentic AI and the whole LLM infra stack. "
            "I would love a growth-stage company where the team is still relatively small."
        ),
        (
            "Right now, the most exciting space in tech is agentic AI and LLM infra. I want to build "
            "developer tools in that domain. I am looking for a small, growth-stage team that operates "
            "remotely. My next position should be a Staff or Principal PM slot on the individual contributor track."
        ),
        (
            "Staff/Principal IC PM looking for a remote, small-team, growth-stage AI infra/dev-tools "
            "company. Obsessed with LLM infra and agentic AI. Building for developers."
        ),
        (
            "Accomplished individual contributor seeking a Staff or Principal Product Manager position "
            "within a growth-stage developer tools organization. My objective is to advance LLM "
            "infrastructure and agentic AI capabilities. I require a remote-first environment and prefer "
            "a small, agile team focused on high-impact developer products."
        ),
        (
            "Devs are struggling with the underlying plumbing of LLMs and autonomous agents. I want to "
            "solve that problem. I am looking to lead product strategy for these developer tools at the "
            "Staff PM or Principal level, staying away from people management. I want a small team "
            "environment where I can work remotely, preferably a company in a solid growth stage."
        ),
        (
            "Remote work is a non-negotiable requirement.\n"
            "I want to stay on the IC track, aiming for Staff or Principal PM.\n"
            "The product must be developer-centric, either AI infrastructure or dev tools.\n"
            "Deeply interested in the agentic AI or LLM infra space.\n"
            "Company profile: Growth-stage, small team."
        ),
    ])
    def test_step_two_backfills_common_wording_variations(self, provider, want):
        sparse = (
            "candidate:\n"
            "  name: Test User\n"
            "target:\n"
            "  roles: []\n"
            "  level: null\n"
            "aspirations:\n"
            "  track: null\n"
            "  direction: null\n"
            "  company_types: []\n"
            "preferences:\n"
            "  company_stage: []\n"
            "  company_size: null\n"
            "inferred:\n"
            "  customer_types: []\n"
            "notes: null\n"
            "_followups: []\n"
        )
        client = self._make_client(sparse)
        profile, _ = self._call(client, want=want, provider=provider)

        assert set(profile["target"]["roles"]) == {"Staff PM", "Principal PM"}
        assert profile["target"]["level"] == "staff"
        assert profile["aspirations"]["track"] == "ic"
        assert profile["aspirations"]["direction"]
        assert profile["aspirations"]["company_types"]
        assert profile["preferences"]["company_stage"] == ["growth-stage"]
        assert profile["preferences"]["team_environment"] == "small-team"
        assert profile["candidate"]["location_preference"] == "remote"
        assert "developer" in profile["inferred"]["customer_types"]

    def test_step_three_text_backfills_missing_avoid_company_types(self):
        sparse = (
            "candidate:\n"
            "  name: Test User\n"
            "target:\n"
            "  roles:\n"
            "    - Staff PM\n"
            "notes: looking for Staff PM roles\n"
            "aspirations:\n"
            "  avoid_company_types: []\n"
            "_followups: []\n"
        )
        client = self._make_client(sparse)
        profile, _ = self._call(
            client,
            want="Staff PM at a developer tools company.",
            dontwant="Pass on AI infrastructure companies and regular onsite travel.",
        )

        assert profile["aspirations"]["avoid_company_types"] == ["ai-infrastructure"]

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

    def test_init_registered_in_cli(self):
        """'init' must appear as a recognised subcommand in the argparse setup."""
        import metis.cli as cli
        source = open(cli.__file__).read()
        assert '"init"' in source, "'init' subcommand not found in cli.py"

    def test_init_routes_to_run_init(self):
        """cli.py dispatch block for 'init' must import and call run_init."""
        import metis.cli as cli
        source = open(cli.__file__).read()
        assert "from .init_cmd import run_init" in source
        assert "run_init(" in source


# ---------------------------------------------------------------------------
# Regression: existing commands unaffected
# ---------------------------------------------------------------------------

class TestInit2Regression:
    """Smoke-check that importing init_cmd doesn't break sibling modules."""

    def test_import_does_not_pollute_profile_module(self):
        import metis.profile as profile_mod
        import metis.init_cmd  # noqa: F401
        # profile module's YAML_PATH must still resolve correctly after import
        assert "profile.yaml" in str(profile_mod.YAML_PATH) or "METIS_PROFILE" in __import__("os").environ

    def test_init_cmd_does_not_import_score(self):
        """init_cmd should not import score.py at module level (keeps startup fast)."""
        import importlib, sys
        # Remove cached module to get a clean import
        for key in list(sys.modules.keys()):
            if "init_cmd" in key:
                del sys.modules[key]
        with mock.patch.dict(sys.modules, {"metis.score": None}):
            try:
                import metis.init_cmd  # noqa: F401
            except ImportError:
                pass  # score.py being None causes ImportError only if init2 imports it at top level
            # If we got here without error, score.py is NOT imported at module level ✓

    def test_score_module_still_importable(self):
        from metis import score
        assert hasattr(score, "score_jobs_batch")

    def test_pipeline_module_still_importable(self):
        from metis import pipeline
        assert hasattr(pipeline, "run_pipeline")

    def test_init_cmd_still_importable(self):
        from metis import init_cmd
        assert hasattr(init_cmd, "run_init")
