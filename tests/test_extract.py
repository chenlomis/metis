"""Tests for scorerole/extract.py — Layer 1 JD extraction.

Philosophy: extraction failures must never block scoring. Every test validates
either the schema contract, gate logic, or graceful degradation.
"""
from __future__ import annotations
import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_job(title="Staff PM", company="Acme", jd="We are looking for a Staff PM."):
    return {"title": title, "company": company, "location": "SF", "jd": jd}


def _make_client(structs: list[dict] | None = None, raise_exc: Exception | None = None):
    """Return a mock Anthropic client that returns a fixed extraction or raises."""
    client = MagicMock()
    if raise_exc:
        client.messages.create.side_effect = raise_exc
    else:
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps(structs or []))]
        response.usage = MagicMock(input_tokens=100, output_tokens=50)
        client.messages.create.return_value = response
    return client


def _minimal_struct(**overrides) -> dict:
    base = {
        "jd_quality": "high",
        "unknown_fields": [],
        "role_function_match": True,
        "inferred_structural_level": "staff",
        "management_type": "ic",
        "manages_pm_team": False,
        "reports_to_level": "vp",
        "work_model": "hybrid",
        "hybrid_days_required": 2,
        "salary_min": 180000,
        "salary_max": 220000,
        "salary_disclosed": True,
        "equity_type": "rsu",
        "company_stage": "growth",
        "company_tier": "large_private",
        "customer_type": "b2b",
        "customer_segment": "enterprise",
        "product_surface": ["web_app", "api"],
        "technical_depth_required": "technical",
        "org_maturity": "scaling",
        "autonomy_level": "high",
        "degree_hard_requirement": False,
        "degree_level": None,
        "visa_sponsorship": None,
        "government_export_control": False,
        "years_exp_min": 5,
        "primary_execution_stack": ["roadmap", "technical_specs"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Schema contract
# ---------------------------------------------------------------------------

class TestBlankStruct:
    """_BLANK_STRUCT must satisfy schema requirements."""

    def test_blank_struct_has_required_keys(self):
        from scorerole.extract import _BLANK_STRUCT, _REQUIRED_KEYS
        assert _REQUIRED_KEYS.issubset(_BLANK_STRUCT.keys())

    def test_blank_struct_jd_quality_is_blank(self):
        from scorerole.extract import _BLANK_STRUCT
        assert _BLANK_STRUCT["jd_quality"] == "blank"

    def test_blank_struct_salary_disclosed_false(self):
        from scorerole.extract import _BLANK_STRUCT
        assert _BLANK_STRUCT["salary_disclosed"] is False

    def test_blank_struct_is_not_mutated_by_copy(self):
        """dict(_BLANK_STRUCT) must produce independent dicts — not shared state."""
        from scorerole.extract import _BLANK_STRUCT
        a = dict(_BLANK_STRUCT)
        b = dict(_BLANK_STRUCT)
        a["jd_quality"] = "high"
        assert b["jd_quality"] == "blank"


class TestIsValidStruct:
    def test_valid_struct_passes(self):
        from scorerole.extract import _is_valid_struct
        assert _is_valid_struct(_minimal_struct())

    def test_missing_required_key_fails(self):
        from scorerole.extract import _is_valid_struct
        s = _minimal_struct()
        del s["salary_disclosed"]
        assert not _is_valid_struct(s)

    def test_non_dict_fails(self):
        from scorerole.extract import _is_valid_struct
        assert not _is_valid_struct([])
        assert not _is_valid_struct(None)
        assert not _is_valid_struct("string")


# ---------------------------------------------------------------------------
# Gate checker
# ---------------------------------------------------------------------------

class TestCheckHardGates:
    """check_hard_gates must correctly enforce salary floor and blank JD gate."""

    def test_blank_jd_gated(self):
        from scorerole.extract import check_hard_gates
        passes, gate = check_hard_gates({"jd_quality": "blank"}, {})
        assert not passes
        assert gate == "jd_blank"

    def test_low_quality_jd_not_gated(self):
        """Low quality ≠ blank — role should still proceed to Sonnet."""
        from scorerole.extract import check_hard_gates
        passes, _ = check_hard_gates(_minimal_struct(jd_quality="low"), {})
        assert passes

    def test_salary_below_floor_gated(self):
        from scorerole.extract import check_hard_gates
        struct = _minimal_struct(salary_max=100000, salary_disclosed=True)
        passes, gate = check_hard_gates(struct, {"salary_floor_usd": 150000})
        assert not passes
        assert gate == "salary_floor"

    def test_salary_at_threshold_passes(self):
        """salary_max >= floor * 0.9 must not be gated."""
        from scorerole.extract import check_hard_gates
        struct = _minimal_struct(salary_max=136000, salary_disclosed=True)  # 136k ≥ 150k * 0.9
        passes, _ = check_hard_gates(struct, {"salary_floor_usd": 150000})
        assert passes

    def test_salary_just_below_threshold_gated(self):
        """salary_max < floor * 0.9 should gate."""
        from scorerole.extract import check_hard_gates
        struct = _minimal_struct(salary_max=134000, salary_disclosed=True)  # 134k < 150k * 0.9
        passes, gate = check_hard_gates(struct, {"salary_floor_usd": 150000})
        assert not passes
        assert gate == "salary_floor"

    def test_undisclosed_salary_not_gated(self):
        """If salary is not disclosed, we can't apply the floor gate."""
        from scorerole.extract import check_hard_gates
        struct = _minimal_struct(salary_max=None, salary_disclosed=False)
        passes, _ = check_hard_gates(struct, {"salary_floor_usd": 150000})
        assert passes

    def test_no_floor_in_profile_not_gated(self):
        from scorerole.extract import check_hard_gates
        struct = _minimal_struct(salary_max=80000, salary_disclosed=True)
        passes, _ = check_hard_gates(struct, {})
        assert passes

    def test_normal_role_passes_all_gates(self):
        from scorerole.extract import check_hard_gates
        passes, gate = check_hard_gates(_minimal_struct(), {"salary_floor_usd": 150000})
        assert passes
        assert gate == ""

    def test_missing_jd_quality_does_not_crash(self):
        from scorerole.extract import check_hard_gates
        passes, _ = check_hard_gates({}, {})
        assert passes   # empty struct → no gate fires


# ---------------------------------------------------------------------------
# Extraction context formatting
# ---------------------------------------------------------------------------

class TestFormatExtractionForScoring:
    """format_extraction_for_scoring must produce readable text without crashing."""

    def test_blank_struct_returns_empty(self):
        from scorerole.extract import format_extraction_for_scoring, _BLANK_STRUCT
        assert format_extraction_for_scoring(_BLANK_STRUCT) == ""

    def test_empty_dict_returns_empty(self):
        from scorerole.extract import format_extraction_for_scoring
        assert format_extraction_for_scoring({}) == ""

    def test_full_struct_contains_key_fields(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct()
        out = format_extraction_for_scoring(s)
        assert "[EXTRACTED CONTEXT]" in out
        assert "staff" in out
        assert "$180,000" in out   # salary_min formatted
        assert "b2b" in out
        assert "growth" in out     # company_stage

    def test_undisclosed_salary_shows_not_disclosed(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct(salary_disclosed=False, salary_min=None, salary_max=None)
        out = format_extraction_for_scoring(s)
        assert "not disclosed" in out

    def test_degree_requirement_flagged(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct(degree_hard_requirement=True, degree_level="ms_phd")
        out = format_extraction_for_scoring(s)
        assert "degree required" in out
        assert "ms_phd" in out

    def test_visa_false_flagged(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct(visa_sponsorship=False)
        out = format_extraction_for_scoring(s)
        assert "no visa sponsorship" in out

    def test_itar_flagged(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct(government_export_control=True)
        out = format_extraction_for_scoring(s)
        assert "export control" in out

    def test_unknown_fields_listed(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct(unknown_fields=["org_maturity", "autonomy_level"])
        out = format_extraction_for_scoring(s)
        assert "unknowns" in out
        assert "org_maturity" in out

    def test_hybrid_days_shown(self):
        from scorerole.extract import format_extraction_for_scoring
        s = _minimal_struct(work_model="hybrid", hybrid_days_required=3)
        out = format_extraction_for_scoring(s)
        assert "3d/wk" in out

    def test_none_struct_returns_empty(self):
        from scorerole.extract import format_extraction_for_scoring
        assert format_extraction_for_scoring(None) == ""


# ---------------------------------------------------------------------------
# extract_jd_structs — batch extraction with mocked API
# ---------------------------------------------------------------------------

class TestExtractJdStructs:
    """extract_jd_structs must chunk correctly and return one dict per job."""

    def _good_response(self, n: int):
        structs = [_minimal_struct() for _ in range(n)]
        r = MagicMock()
        r.content = [MagicMock(text=json.dumps(structs))]
        r.usage   = MagicMock(input_tokens=100, output_tokens=50)
        return r

    def test_empty_input_returns_empty(self):
        from scorerole.extract import extract_jd_structs
        client = MagicMock()
        assert extract_jd_structs(client, []) == []
        client.messages.create.assert_not_called()

    def test_single_job_returns_one_struct(self):
        from scorerole.extract import extract_jd_structs
        client = MagicMock()
        client.messages.create.return_value = self._good_response(1)
        result = extract_jd_structs(client, [_make_job()])
        assert len(result) == 1
        assert result[0]["jd_quality"] == "high"

    def test_result_count_matches_job_count(self):
        from scorerole.extract import extract_jd_structs
        n = 7
        client = MagicMock()
        client.messages.create.return_value = self._good_response(n)
        result = extract_jd_structs(client, [_make_job() for _ in range(n)])
        assert len(result) == n

    def test_large_batch_chunks_correctly(self):
        """More than _EXTRACT_CHUNK_SIZE jobs → multiple API calls."""
        from scorerole.extract import extract_jd_structs, _EXTRACT_CHUNK_SIZE
        n = _EXTRACT_CHUNK_SIZE + 3

        def side_effect(*args, **kwargs):
            content = kwargs.get("messages", [{}])[0].get("content", "")
            chunk_n = content.count("JOB ")
            return self._good_response(chunk_n)

        client = MagicMock()
        client.messages.create.side_effect = side_effect
        result = extract_jd_structs(client, [_make_job() for _ in range(n)])
        assert len(result) == n
        assert client.messages.create.call_count == 2

    def test_short_response_padded_with_blank_structs(self):
        """If API returns fewer structs than jobs, remainder must be blank — not IndexError."""
        from scorerole.extract import extract_jd_structs, _BLANK_STRUCT
        client = MagicMock()
        client.messages.create.return_value = self._good_response(1)  # only 1 of 3
        result = extract_jd_structs(client, [_make_job() for _ in range(3)])
        assert len(result) == 3
        assert result[0]["jd_quality"] == "high"   # real
        assert result[1]["jd_quality"] == "blank"  # padded
        assert result[2]["jd_quality"] == "blank"

    def test_api_failure_returns_blank_structs(self):
        """If API raises, extraction falls back to blank structs — scoring unblocked."""
        import anthropic
        from scorerole.extract import extract_jd_structs
        client = MagicMock()
        client.messages.create.side_effect = anthropic.APIConnectionError(request=MagicMock())
        with patch("time.sleep"):
            result = extract_jd_structs(client, [_make_job(), _make_job()])
        assert len(result) == 2
        assert all(r["jd_quality"] == "blank" for r in result)

    def test_broken_json_returns_blank_structs(self):
        from scorerole.extract import extract_jd_structs
        r = MagicMock()
        r.content = [MagicMock(text="not json at all")]
        r.usage   = MagicMock(input_tokens=10, output_tokens=5)
        client = MagicMock()
        client.messages.create.return_value = r
        result = extract_jd_structs(client, [_make_job()])
        assert len(result) == 1
        assert result[0]["jd_quality"] == "blank"

    def test_invalid_struct_replaced_with_low_quality(self):
        """A struct missing required keys must be replaced — not passed downstream."""
        from scorerole.extract import extract_jd_structs
        bad = {"jd_quality": "high"}  # missing required keys
        r = MagicMock()
        r.content = [MagicMock(text=json.dumps([bad]))]
        r.usage   = MagicMock(input_tokens=10, output_tokens=5)
        client = MagicMock()
        client.messages.create.return_value = r
        result = extract_jd_structs(client, [_make_job()])
        assert len(result) == 1
        # _is_valid_struct failed → fallback used
        assert result[0].get("jd_quality") in ("blank", "low")

    def test_no_jd_still_extracts(self):
        """Jobs without JD text should still be sent for extraction (title+company)."""
        from scorerole.extract import extract_jd_structs
        job_no_jd = {"title": "Staff PM", "company": "Acme", "location": "SF", "jd": None}
        client = MagicMock()
        client.messages.create.return_value = self._good_response(1)
        result = extract_jd_structs(client, [job_no_jd])
        assert len(result) == 1
        # API should have been called
        client.messages.create.assert_called_once()


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------

class TestExtractChunkRetry:
    """_extract_chunk must retry on transient errors, same as score.py."""

    def _good_response(self):
        r = MagicMock()
        r.content = [MagicMock(text=json.dumps([_minimal_struct()]))]
        r.usage   = MagicMock(input_tokens=50, output_tokens=30)
        return r

    def test_retries_on_500_and_succeeds(self):
        import anthropic
        from scorerole.extract import _extract_chunk
        client = MagicMock()
        client.messages.create.side_effect = [
            anthropic.InternalServerError(
                message="server error",
                response=MagicMock(status_code=500, headers={}),
                body={},
            ),
            self._good_response(),
        ]
        with patch("time.sleep"):
            result = _extract_chunk(client, [_make_job()])
        assert client.messages.create.call_count == 2
        assert result[0]["jd_quality"] == "high"

    def test_raises_after_max_retries(self):
        import anthropic
        from scorerole.extract import _extract_chunk, _MAX_ATTEMPTS
        client = MagicMock()
        client.messages.create.side_effect = anthropic.InternalServerError(
            message="server error",
            response=MagicMock(status_code=500, headers={}),
            body={},
        )
        with patch("time.sleep"):
            with pytest.raises(anthropic.InternalServerError):
                _extract_chunk(client, [_make_job()])
        assert client.messages.create.call_count == _MAX_ATTEMPTS

    def test_non_retryable_propagates_immediately(self):
        import anthropic
        from scorerole.extract import _extract_chunk
        client = MagicMock()
        client.messages.create.side_effect = anthropic.AuthenticationError(
            message="bad key",
            response=MagicMock(status_code=401, headers={}),
            body={},
        )
        with pytest.raises(anthropic.AuthenticationError):
            _extract_chunk(client, [_make_job()])
        assert client.messages.create.call_count == 1


# ---------------------------------------------------------------------------
# Pipeline integration regression
# ---------------------------------------------------------------------------

class TestPipelineExtractionIntegration:
    """Gate-filtered jobs must be excluded from scoring but included in rank output."""

    def _make_scored_job(self, title, score=75):
        return {
            "title": title, "company": "Acme", "location": "SF",
            "jd": "Great job.", "eval": {},
        }

    # patch targets:
    #   enrich_jobs      — imported locally inside _stage_enrich_and_score
    #   extract_jd_structs, check_hard_gates — imported locally from .extract
    #   load_profile_yaml — imported locally from .profile
    #   score_jobs_batch, rank_jobs — imported at pipeline module level

    def test_gate_filtered_job_gets_filtered_verdict(self):
        """A job whose extraction returns jd_quality='blank' must get verdict='filtered'."""
        from scorerole.pipeline import _stage_enrich_and_score
        from scorerole.extract import _BLANK_STRUCT

        with patch("scorerole.sources.linkedin.enrich_jobs", return_value=[_make_job()]), \
             patch("scorerole.extract.extract_jd_structs", return_value=[dict(_BLANK_STRUCT)]), \
             patch("scorerole.profile.load_profile_yaml", return_value={}), \
             patch("scorerole.pipeline.score_jobs_batch") as mock_score, \
             patch("scorerole.pipeline.rank_jobs", side_effect=lambda jobs: jobs):
            result = _stage_enrich_and_score([_make_job()], MagicMock())

        mock_score.assert_not_called()
        assert result[0]["eval"]["verdict"] == "filtered"
        assert result[0]["eval"]["tags"][0]["text"] == "gate: jd_blank"

    def test_salary_gated_job_skips_sonnet(self):
        """A job with disclosed salary below floor must be filtered without Sonnet call."""
        from scorerole.pipeline import _stage_enrich_and_score
        below_floor = _minimal_struct(salary_max=50000, salary_disclosed=True)

        with patch("scorerole.sources.linkedin.enrich_jobs", return_value=[_make_job()]), \
             patch("scorerole.extract.extract_jd_structs", return_value=[below_floor]), \
             patch("scorerole.profile.load_profile_yaml", return_value={"salary_floor_usd": 150000}), \
             patch("scorerole.pipeline.score_jobs_batch") as mock_score, \
             patch("scorerole.pipeline.rank_jobs", side_effect=lambda jobs: jobs):
            result = _stage_enrich_and_score([_make_job()], MagicMock())

        mock_score.assert_not_called()
        assert result[0]["eval"]["verdict"] == "filtered"
        assert "salary_floor" in result[0]["eval"]["tags"][0]["text"]

    def test_passing_job_reaches_sonnet(self):
        """A job that passes all gates must be sent to score_jobs_batch."""
        from scorerole.pipeline import _stage_enrich_and_score

        def fake_score(client, jobs):
            for j in jobs:
                j["eval"] = {"score": 80, "verdict": "apply",
                             "leveragePoints": [], "frictionPoints": [], "tags": []}

        with patch("scorerole.sources.linkedin.enrich_jobs", return_value=[_make_job()]), \
             patch("scorerole.extract.extract_jd_structs", return_value=[_minimal_struct()]), \
             patch("scorerole.profile.load_profile_yaml", return_value={}), \
             patch("scorerole.pipeline.score_jobs_batch", side_effect=fake_score), \
             patch("scorerole.pipeline.rank_jobs", side_effect=lambda jobs: jobs):
            result = _stage_enrich_and_score([_make_job()], MagicMock())

        assert result[0]["eval"]["verdict"] == "apply"

    def test_extraction_failure_falls_back_gracefully(self):
        """If extract_jd_structs raises, pipeline should still score (no gate fires)."""
        from scorerole.pipeline import _stage_enrich_and_score

        def fake_score(client, jobs):
            for j in jobs:
                j["eval"] = {"score": 70, "verdict": "consider",
                             "leveragePoints": [], "frictionPoints": [], "tags": []}

        with patch("scorerole.sources.linkedin.enrich_jobs", return_value=[_make_job()]), \
             patch("scorerole.extract.extract_jd_structs", side_effect=RuntimeError("API down")), \
             patch("scorerole.profile.load_profile_yaml", return_value={}), \
             patch("scorerole.pipeline.score_jobs_batch", side_effect=fake_score), \
             patch("scorerole.pipeline.rank_jobs", side_effect=lambda jobs: jobs):
            result = _stage_enrich_and_score([_make_job()], MagicMock())

        assert result[0]["eval"]["verdict"] == "consider"

    def test_mixed_gate_and_passing_jobs_separated_correctly(self):
        """Two jobs: one blank (filtered), one passing — Sonnet called for one only."""
        from scorerole.pipeline import _stage_enrich_and_score
        from scorerole.extract import _BLANK_STRUCT

        scored_calls = []

        def fake_score(client, jobs):
            scored_calls.extend(j["title"] for j in jobs)
            for j in jobs:
                j["eval"] = {"score": 75, "verdict": "apply",
                             "leveragePoints": [], "frictionPoints": [], "tags": []}

        jobs = [_make_job("Blank Job"), _make_job("Real Job")]
        structs = [dict(_BLANK_STRUCT), _minimal_struct()]

        with patch("scorerole.sources.linkedin.enrich_jobs", return_value=jobs), \
             patch("scorerole.extract.extract_jd_structs", return_value=structs), \
             patch("scorerole.profile.load_profile_yaml", return_value={}), \
             patch("scorerole.pipeline.score_jobs_batch", side_effect=fake_score), \
             patch("scorerole.pipeline.rank_jobs", side_effect=lambda jobs: jobs):
            result = _stage_enrich_and_score(jobs, MagicMock())

        assert len(result) == 2
        assert "Real Job" in scored_calls
        assert "Blank Job" not in scored_calls


# ---------------------------------------------------------------------------
# profile.py — location_preference rendering
# ---------------------------------------------------------------------------

class TestRenderProfileLocationPreference:
    """render_profile must handle location_preference, legacy work_mode, and bool fallback."""

    def _base_profile(self, **candidate_overrides):
        p = {
            "candidate": {"name": "Alex", "location": "NYC"},
            "target": {"level": "staff", "roles": ["Staff PM"]},
            "scoring": {"apply_threshold": 75, "consider_threshold": 55},
        }
        p["candidate"].update(candidate_overrides)
        return p

    def test_location_preference_remote(self):
        from scorerole.profile import render_profile
        out = render_profile(self._base_profile(location_preference="remote"))
        assert "remote only" in out

    def test_location_preference_local(self):
        from scorerole.profile import render_profile
        out = render_profile(self._base_profile(location_preference="local"))
        assert "local" in out

    def test_location_preference_flexible(self):
        from scorerole.profile import render_profile
        out = render_profile(self._base_profile(location_preference="flexible"))
        assert "flexible" in out

    def test_legacy_work_mode_list_still_works(self):
        """Existing profiles with work_mode list must render without crashing."""
        from scorerole.profile import render_profile
        out = render_profile(self._base_profile(work_mode=["Remote-first", "Hybrid OK"]))
        assert "Remote-first" in out

    def test_legacy_open_to_remote_bool_still_works(self):
        from scorerole.profile import render_profile
        out = render_profile(self._base_profile(open_to_remote=True))
        assert "remote-friendly" in out

    def test_location_preference_takes_priority_over_work_mode(self):
        from scorerole.profile import render_profile
        out = render_profile(self._base_profile(
            location_preference="remote",
            work_mode=["On-site OK"],   # legacy field conflicts — new field wins
        ))
        assert "remote only" in out
        assert "On-site OK" not in out


class TestRenderProfileInferred:
    """render_profile must include inferred background when present."""

    def _profile_with_inferred(self, **inferred):
        return {
            "candidate": {"name": "Alex", "location": "NYC"},
            "target": {"level": "staff", "roles": ["Staff PM"]},
            "scoring": {"apply_threshold": 75, "consider_threshold": 55},
            "inferred": inferred,
        }

    def test_inferred_customer_types_rendered(self):
        from scorerole.profile import render_profile
        out = render_profile(self._profile_with_inferred(customer_types=["b2b", "b2b2c"]))
        assert "INFERRED BACKGROUND" in out
        assert "b2b" in out

    def test_inferred_degree_level_rendered(self):
        from scorerole.profile import render_profile
        out = render_profile(self._profile_with_inferred(degree_level="ms_phd"))
        assert "ms_phd" in out

    def test_empty_inferred_block_renders_cleanly(self):
        from scorerole.profile import render_profile
        out = render_profile(self._profile_with_inferred())
        assert isinstance(out, str)

    def test_no_inferred_block_no_section(self):
        """A profile without inferred must not show the INFERRED BACKGROUND header."""
        from scorerole.profile import render_profile
        profile = {
            "candidate": {"name": "Alex"},
            "target": {"roles": ["PM"]},
        }
        out = render_profile(profile)
        assert "INFERRED BACKGROUND" not in out


# ---------------------------------------------------------------------------
# init_cmd.py — _apply_prefs_to_profile location_preference
# ---------------------------------------------------------------------------

class TestApplyPrefsLocationPreference:
    def _base_profile(self):
        return {"candidate": {"name": "Alex"}}

    def test_remote_sets_location_preference_and_open_to_remote_true(self):
        from scorerole.init_cmd import _apply_prefs_to_profile
        p = self._base_profile()
        _apply_prefs_to_profile(p, {"location_preference": "remote"})
        assert p["candidate"]["location_preference"] == "remote"
        assert p["candidate"]["open_to_remote"] is True

    def test_local_sets_open_to_remote_false(self):
        from scorerole.init_cmd import _apply_prefs_to_profile
        p = self._base_profile()
        _apply_prefs_to_profile(p, {"location_preference": "local"})
        assert p["candidate"]["location_preference"] == "local"
        assert p["candidate"]["open_to_remote"] is False

    def test_flexible_sets_open_to_remote_true(self):
        from scorerole.init_cmd import _apply_prefs_to_profile
        p = self._base_profile()
        _apply_prefs_to_profile(p, {"location_preference": "flexible"})
        assert p["candidate"]["open_to_remote"] is True

    def test_no_location_pref_key_does_not_crash(self):
        from scorerole.init_cmd import _apply_prefs_to_profile
        p = self._base_profile()
        _apply_prefs_to_profile(p, {})   # no location_preference key at all
        assert p["candidate"].get("location_preference") is None

    def test_legacy_work_mode_still_handled(self):
        """Old prefs dict with work_mode list must still update the profile."""
        from scorerole.init_cmd import _apply_prefs_to_profile
        p = self._base_profile()
        _apply_prefs_to_profile(p, {"work_mode": ["Remote-first", "Hybrid OK"]})
        assert p["candidate"]["open_to_remote"] is True


class TestFeedbackCmd:
    """save_feedback_entry / load_feedback_text / save_last_run / load_last_run."""

    def _make_job(self, verdict: str, score: int) -> dict:
        return {
            "title": "Staff PM", "company": "Acme", "location": "Remote",
            "eval": {"verdict": verdict, "score": score},
        }

    def test_save_and_load_feedback_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.feedback_cmd.FEEDBACK_FILE", tmp_path / "feedback.md")
        monkeypatch.setattr("scorerole.feedback_cmd.DATA_DIR", tmp_path)
        from scorerole.feedback_cmd import save_feedback_entry, load_feedback_text
        assert load_feedback_text() is None
        save_feedback_entry("Score AI/ML native roles higher even when title is Lead.")
        loaded = load_feedback_text()
        assert loaded is not None
        assert "AI/ML native" in loaded

    def test_multiple_entries_accumulate(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.feedback_cmd.FEEDBACK_FILE", tmp_path / "feedback.md")
        monkeypatch.setattr("scorerole.feedback_cmd.DATA_DIR", tmp_path)
        from scorerole.feedback_cmd import save_feedback_entry, load_feedback_text
        save_feedback_entry("First note.")
        save_feedback_entry("Second note.")
        loaded = load_feedback_text()
        assert "First note" in loaded
        assert "Second note" in loaded

    def test_save_last_run_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.feedback_cmd.LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr("scorerole.feedback_cmd.DATA_DIR", tmp_path)
        from scorerole.feedback_cmd import save_last_run, load_last_run
        jobs = [
            self._make_job("apply",    78),
            self._make_job("consider", 65),
            self._make_job("skipped",  40),
        ]
        save_last_run(jobs, "June 16, 2026", filtered_count=2)
        run = load_last_run()
        assert run is not None
        assert run["apply_count"]    == 1
        assert run["consider_count"] == 1
        assert run["skipped_count"]  == 1
        assert run["filtered_count"] == 2
        assert run["total_evaluated"] == 3
        assert run["run_date"] == "June 16, 2026"

    def test_last_run_roles_sorted_by_score(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.feedback_cmd.LAST_RUN_FILE", tmp_path / "last_run.json")
        monkeypatch.setattr("scorerole.feedback_cmd.DATA_DIR", tmp_path)
        from scorerole.feedback_cmd import save_last_run, load_last_run
        jobs = [
            self._make_job("consider", 60),
            self._make_job("apply",    80),
            self._make_job("consider", 70),
        ]
        save_last_run(jobs, "June 16, 2026")
        run = load_last_run()
        scores = [r["score"] for r in run["roles"]]
        assert scores == sorted(scores, reverse=True)

    def test_load_last_run_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.feedback_cmd.LAST_RUN_FILE", tmp_path / "no_file.json")
        from scorerole.feedback_cmd import load_last_run
        assert load_last_run() is None

    def test_feedback_injected_into_profile_text(self, tmp_path, monkeypatch):
        """load_profile_text must append feedback.md content when present."""
        feedback_path = tmp_path / "feedback.md"
        feedback_path.write_text("## 2026-06-16\n\nGlean AI infra scope is deeper than extracted.\n")
        import scorerole.profile as profile_mod
        monkeypatch.setattr(profile_mod, "PROFILE_DIR", tmp_path)
        # Provide a minimal profile.yaml so load_profile_yaml returns data
        import yaml
        profile_yaml = tmp_path / "profile.yaml"
        profile_yaml.write_text(yaml.dump({
            "candidate": {"name": "Test User", "location": "Remote"},
            "target": {"roles": ["Staff PM"], "level": "staff"},
            "scoring": {},
        }))
        monkeypatch.setattr(profile_mod, "YAML_PATH", profile_yaml)
        text = profile_mod.load_profile_text()
        assert text is not None
        assert "CANDIDATE CALIBRATION FEEDBACK" in text
        assert "Glean AI infra scope" in text

    def test_no_feedback_file_leaves_profile_clean(self, tmp_path, monkeypatch):
        """load_profile_text must not mention feedback when feedback.md is absent."""
        import yaml
        import scorerole.profile as profile_mod
        monkeypatch.setattr(profile_mod, "PROFILE_DIR", tmp_path)
        profile_yaml = tmp_path / "profile.yaml"
        profile_yaml.write_text(yaml.dump({
            "candidate": {"name": "Test User", "location": "Remote"},
            "target": {"roles": ["Staff PM"], "level": "staff"},
            "scoring": {},
        }))
        monkeypatch.setattr(profile_mod, "YAML_PATH", profile_yaml)
        text = profile_mod.load_profile_text()
        assert text is not None
        assert "CALIBRATION FEEDBACK" not in text
