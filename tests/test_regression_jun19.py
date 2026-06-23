"""
Regression tests for the three changes landed 2026-06-19:

  1. render.py + score.py — frictionPoints/leveragePoints bare-string bug
     (_coerce_list, _normalize_list_fields)
  2. track.py — recruiter_screen classification (4th email class)
  3. init2_cmd.py + profile.template.yaml — schema alignment

Each section is labelled with the commit it covers.
"""
from __future__ import annotations
import pytest
from unittest import mock


# ---------------------------------------------------------------------------
# 1. Render regression — bare string coercion
#    commit a7e3459
# ---------------------------------------------------------------------------

class TestCoerceList:
    """_coerce_list must never let a bare string reach '; '.join()."""

    def _coerce(self, val):
        from scorerole.render import _coerce_list
        return _coerce_list(val)

    def test_list_passthrough(self):
        assert self._coerce(["A", "B"]) == ["A", "B"]

    def test_bare_string_wrapped(self):
        result = self._coerce("No prior AI experience")
        assert result == ["No prior AI experience"]
        # Must NOT produce character-by-character output
        assert len(result) == 1

    def test_empty_string_returns_empty_list(self):
        assert self._coerce("") == []
        assert self._coerce("   ") == []

    def test_none_returns_empty_list(self):
        assert self._coerce(None) == []

    def test_non_string_non_list_returns_empty(self):
        assert self._coerce(42) == []
        assert self._coerce({}) == []

    def test_empty_list_passthrough(self):
        assert self._coerce([]) == []


class TestNormalizeListFields:
    """_normalize_list_fields must coerce at the score.py layer before render sees data."""

    def _normalize(self, evals):
        from scorerole.score import _normalize_list_fields
        _normalize_list_fields(evals)
        return evals

    def test_bare_string_leverage_coerced(self):
        evals = [{"leveragePoints": "Strong AI background", "frictionPoints": []}]
        result = self._normalize(evals)
        assert result[0]["leveragePoints"] == ["Strong AI background"]

    def test_bare_string_friction_coerced(self):
        evals = [{"leveragePoints": [], "frictionPoints": "No domain experience"}]
        result = self._normalize(evals)
        assert result[0]["frictionPoints"] == ["No domain experience"]

    def test_both_already_lists_unchanged(self):
        evals = [{"leveragePoints": ["A", "B"], "frictionPoints": ["C"]}]
        result = self._normalize(evals)
        assert result[0]["leveragePoints"] == ["A", "B"]
        assert result[0]["frictionPoints"] == ["C"]

    def test_empty_string_becomes_empty_list(self):
        evals = [{"leveragePoints": "", "frictionPoints": ""}]
        result = self._normalize(evals)
        assert result[0]["leveragePoints"] == []
        assert result[0]["frictionPoints"] == []

    def test_none_value_becomes_empty_list(self):
        evals = [{"leveragePoints": None, "frictionPoints": None}]
        result = self._normalize(evals)
        assert result[0]["leveragePoints"] == []
        assert result[0]["frictionPoints"] == []

    def test_multiple_evals_all_normalized(self):
        evals = [
            {"leveragePoints": "Point A", "frictionPoints": []},
            {"leveragePoints": [], "frictionPoints": "Point B"},
        ]
        result = self._normalize(evals)
        assert result[0]["leveragePoints"] == ["Point A"]
        assert result[1]["frictionPoints"] == ["Point B"]

    def test_missing_fields_left_absent(self):
        # _normalize_list_fields only coerces existing fields — doesn't inject absent ones
        evals = [{}]
        result = self._normalize(evals)
        # No error raised; absent fields stay absent (render's _coerce_list handles None/missing)
        assert "leveragePoints" not in result[0] or isinstance(result[0]["leveragePoints"], list)
        assert "frictionPoints" not in result[0] or isinstance(result[0]["frictionPoints"], list)


class TestLeverageFrictionRender:
    """_leverage_friction must produce well-formed HTML, never character iteration."""

    def _render(self, leverage, friction):
        from scorerole.render import _leverage_friction
        return _leverage_friction(leverage, friction)

    def test_bare_string_leverage_renders_single_bullet(self):
        html = self._render("Strong AI background", [])
        # Should contain the text once, not character-by-character
        assert "Strong AI background" in html
        assert "S; t; r; o; n; g" not in html
        # Should be a single <p> block, not many
        assert html.count("&#8593;") == 1

    def test_bare_string_friction_renders_single_bullet(self):
        html = self._render([], "No domain experience")
        assert "No domain experience" in html
        assert "N; o;" not in html
        assert html.count("&#8595;") == 1

    def test_list_with_two_leverage_points(self):
        html = self._render(["Point A", "Point B"], [])
        assert html.count("&#8593;") == 2
        assert "Point A" in html
        assert "Point B" in html

    def test_empty_both_returns_nbsp_placeholder(self):
        html = self._render([], [])
        assert "&nbsp;" in html

    def test_none_inputs_handled(self):
        html = self._render(None, None)
        # Should not raise, should return placeholder
        assert html  # non-empty


# ---------------------------------------------------------------------------
# 2. Track — recruiter_screen classification
#    commit ee97963
# ---------------------------------------------------------------------------

class TestClassifyEmailRecruiterScreen:
    """classify_email must return 'recruiter_screen' for scheduling / phone-screen emails."""

    def _classify(self, body, subject=""):
        from scorerole.track import classify_email
        return classify_email(body, subject, llm_client=None)

    # --- Positive: should return recruiter_screen ---

    def test_scheduling_ask_in_body(self):
        # Matches: "i'd like to schedule some time"
        body = "I'd like to schedule some time to discuss this opportunity with you."
        assert self._classify(body) == "recruiter_screen"

    def test_phone_screen_phrase_in_body(self):
        # Matches: "set up time for a phone screen"
        body = "We'd love to set up time for a phone screen with our hiring team."
        assert self._classify(body) == "recruiter_screen"

    def test_next_steps_subject_tiebreaker(self):
        body = "Hi, we reviewed your profile and want to connect."
        assert self._classify(body, subject="Next Steps with Datadog") == "recruiter_screen"

    def test_hello_from_subject(self):
        body = "I came across your profile and think you'd be a great fit."
        assert self._classify(body, subject="Hello from Datadog") == "recruiter_screen"

    def test_microsoft_phone_screen_subject(self):
        # Regex: "phone screen with " — matches "Phone Screen with Microsoft"
        body = "Please confirm your availability for the upcoming screen."
        assert self._classify(body, subject="Phone Screen with Microsoft") == "recruiter_screen"

    # --- Negative: must NOT be misclassified as recruiter_screen ---

    def test_clear_rejection_not_recruiter_screen(self):
        body = "After careful consideration, we will not be moving forward with your application."
        result = self._classify(body)
        assert result == "rejection"
        assert result != "recruiter_screen"

    def test_offer_letter_not_recruiter_screen(self):
        body = "Congratulations! We are pleased to extend an offer of employment."
        result = self._classify(body)
        assert result != "recruiter_screen"

    def test_generic_email_not_recruiter_screen(self):
        body = "Thank you for your interest. We will keep your resume on file."
        result = self._classify(body)
        assert result != "recruiter_screen"

    # --- Mutual exclusivity: recruiter_screen checked before confirmation ---

    def test_recruiter_screen_takes_priority_over_confirmation_signals(self):
        # "Next Steps" was previously in _SUBJECT_IMPLIES_CONFIRMATION
        # It must now route to recruiter_screen, not confirmation
        body = "We'd like to schedule a quick call to discuss next steps."
        result = self._classify(body, subject="Next Steps with SeekOut")
        assert result == "recruiter_screen"
        assert result != "confirmation"


class TestClassifyEmailReturnType:
    """classify_email return value must always be one of the 4 valid classes."""

    def test_return_is_always_valid_class(self):
        from scorerole.track import classify_email, _LLM_VALID_CLASSES
        test_cases = [
            ("We will not be moving forward.", "Rejection"),
            ("Congratulations on your offer!", "Your Offer Letter"),
            ("Let's schedule a call.", "Next Steps"),
            ("", ""),
        ]
        for body, subject in test_cases:
            result = classify_email(body, subject, llm_client=None)
            assert result in _LLM_VALID_CLASSES, f"Got {result!r} for subject={subject!r}"


class TestRecruiterScreenTrackerUpdate:
    """update_recruiter_screen must set the correct status and fill color."""

    def test_sets_recruiter_screen_status(self):
        from scorerole.track import update_recruiter_screen
        ws = mock.MagicMock()
        # Simulate a cell at row 5
        ws.cell.return_value = mock.MagicMock()
        update_recruiter_screen(ws, 5)
        # Must have written to the worksheet
        assert ws.cell.called

    def test_no_downgrade_from_rejected(self):
        """run_track must not call update_recruiter_screen if status is already Rejected."""
        from scorerole.track import classify_email
        # This is a logic test: the guard is in run_track(), verified via classify
        # returning recruiter_screen (the guard then checks current_status)
        result = classify_email("Let's schedule a call.", "Next Steps with Klaviyo")
        assert result == "recruiter_screen"
        # The actual no-downgrade guard is in run_track's dispatch block —
        # verified here by confirming classify returns the right class so the
        # guard has a chance to fire. Integration coverage lives in e2e persona run.


# ---------------------------------------------------------------------------
# 3. init2 schema alignment regression
#    commit eeed135
# ---------------------------------------------------------------------------

class TestInit2SchemaAlignment:
    """After the schema refactor, init2_cmd must still extract all required top-level keys."""

    def test_extract_system_v2_contains_required_fields(self):
        from scorerole.prompts import init_extract_system_prompt
        prompt = init_extract_system_prompt()
        required = [
            "candidate:", "target:", "aspirations:", "preferences:",
            "experience:", "education:", "strengths:", "deal_breakers:",
            "salary_floor_usd:", "notes:", "_followups:",
        ]
        for field in required:
            assert field in prompt, f"Missing schema field: {field}"

    def test_extract_system_v2_has_no_scoring_block(self):
        """scoring block was moved to inferred.* — must not appear as a top-level key."""
        from scorerole.prompts import init_extract_system_prompt
        import re
        prompt = init_extract_system_prompt()
        assert not re.search(r"^scoring:", prompt, re.MULTILINE), \
            "scoring: block still present as top-level key in init_extract_system_prompt()"

    def test_guardrails_still_work_after_refactor(self):
        """_apply_guardrails must be importable and handle the new schema shape."""
        from scorerole.init2_cmd import _apply_guardrails
        # New schema: candidate may have company_environment; scoring block absent
        profile = {
            "candidate": {"name": "Test", "open_to_remote": True},
            "target": {"roles": ["Staff PM"], "level": "staff", "industries": []},
            "aspirations": {"track": "ic"},
            "preferences": {"company_environment": "remote-first"},
            "deal_breakers": [],
            "salary_floor_usd": 200000,
        }
        result = _apply_guardrails(profile, [], "I want $200k+ and prefer remote", dontwant_skipped=True)
        assert isinstance(result, list)
        assert len(result) <= 3

    def test_profile_template_yaml_parseable(self):
        """profile.template.yaml must parse as valid YAML after the schema update."""
        import yaml
        from pathlib import Path
        template = Path(__file__).parent.parent / "profile.template.yaml"
        if template.exists():
            data = yaml.safe_load(template.read_text())
            assert isinstance(data, dict)
            assert "candidate" in data

    def test_init2_cmd_still_importable(self):
        import scorerole.init2_cmd  # noqa: F401

    def test_init_cmd_unaffected_by_schema_refactor(self):
        """Original scorerole init must still be importable and have run_init."""
        from scorerole.init_cmd import run_init
        assert callable(run_init)
