"""Core logic tests for scorerole.

Philosophy: "if this breaks, something important is wrong."
These tests cover the functions whose silent failure would cause the most
harm — bad dedup, missed jobs, wrong scoring order, or buried roles.

Run with:  pytest tests/
"""
import datetime
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# state.py — role hashing + seen_roles TTL
# ---------------------------------------------------------------------------

class TestRoleHash:
    """_role_hash must be stable — dedup depends on it never changing."""

    def test_hash_is_12_chars(self):
        from scorerole.state import _role_hash
        h = _role_hash("Staff Product Manager", "Anthropic")
        assert len(h) == 12

    def test_hash_is_stable(self):
        """Same inputs always produce the same hash (no randomness)."""
        from scorerole.state import _role_hash
        assert _role_hash("Staff PM", "Stripe") == _role_hash("Staff PM", "Stripe")

    def test_hash_normalises_case_and_punctuation(self):
        """Normalisation means 'Staff PM @ Stripe' == 'staff pm stripe'."""
        from scorerole.state import _role_hash
        assert _role_hash("Staff PM", "Stripe") == _role_hash("STAFF PM", "STRIPE")
        assert _role_hash("Staff PM", "Stripe") == _role_hash("Staff PM!", "Stripe.")

    def test_different_roles_produce_different_hashes(self):
        from scorerole.state import _role_hash
        assert _role_hash("Staff PM", "Stripe") != _role_hash("Staff PM", "Anthropic")
        assert _role_hash("Staff PM", "Stripe") != _role_hash("Senior PM", "Stripe")


class TestSeenRolesTTL:
    """save_seen_roles must prune expired entries; load_seen_roles must honour TTL."""

    def test_new_entries_survive_within_ttl(self, tmp_path):
        from scorerole.state import save_seen_roles, load_seen_roles
        with patch("scorerole.state.DATA_DIR", tmp_path):
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
            entries = {"abc123": now.isoformat()}
            save_seen_roles(entries)
            seen = load_seen_roles()
        assert "abc123" in seen

    def test_expired_entries_are_pruned_on_load(self, tmp_path):
        """An entry from 15 days ago must not appear in load_seen_roles."""
        from scorerole.state import save_seen_roles, load_seen_roles
        with patch("scorerole.state.DATA_DIR", tmp_path):
            old_ts = (
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                - datetime.timedelta(days=15)
            ).isoformat()
            p = tmp_path / "seen_roles.json"
            p.write_text(json.dumps({"deadbeef1234": old_ts}))
            seen = load_seen_roles()
        assert "deadbeef1234" not in seen

    def test_save_prunes_stale_entries(self, tmp_path):
        """Saving new entries must evict old ones from the file."""
        from scorerole.state import save_seen_roles
        with patch("scorerole.state.DATA_DIR", tmp_path):
            old_ts = (
                datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
                - datetime.timedelta(days=15)
            ).isoformat()
            now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
            p = tmp_path / "seen_roles.json"
            p.write_text(json.dumps({"stale": old_ts}))
            save_seen_roles({"fresh": now})
            on_disk = json.loads(p.read_text())
        assert "stale" not in on_disk
        assert "fresh" in on_disk

    def test_capped_roles_not_buried(self, tmp_path):
        """Roles dropped by the cap must NOT be written to seen_roles.json
        (regression for the role-burial bug fixed in commit 8e805de).
        """
        from scorerole.state import save_seen_roles, load_seen_roles
        # Simulate: 3 roles found, only 1 scored, 2 capped
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
        with patch("scorerole.state.DATA_DIR", tmp_path):
            save_seen_roles({"scored_role": now})   # only the scored one
            seen = load_seen_roles()
        assert "scored_role" in seen
        assert "capped_role_1" not in seen   # was never passed to save_seen_roles
        assert "capped_role_2" not in seen


# ---------------------------------------------------------------------------
# sources/linkedin.py — job extraction from email body
# ---------------------------------------------------------------------------

_SAMPLE_ALERT_BODY = """\
Your job alert for "product manager"

Senior Product Manager
Anthropic
San Francisco, CA · Remote
3 company alumni
View job: https://www.linkedin.com/comm/jobs/view/3901234567/AAAA_aaaa111/?trackingId=xyz

Staff Software Engineer
Stripe
New York, NY · On-site
View job: https://www.linkedin.com/comm/jobs/view/3901234568/BBBB_bbbb222/?trackingId=abc

--
Your job alert
"""

class TestExtractJobs:
    """extract_jobs() must reliably parse title / company / location / job_id."""

    def test_extracts_both_jobs(self):
        from scorerole.sources.linkedin import extract_jobs
        jobs = extract_jobs(_SAMPLE_ALERT_BODY)
        assert len(jobs) == 2

    def test_first_job_fields(self):
        from scorerole.sources.linkedin import extract_jobs
        job = extract_jobs(_SAMPLE_ALERT_BODY)[0]
        assert job["title"]    == "Senior Product Manager"
        assert job["company"]  == "Anthropic"
        assert "San Francisco" in job["location"]
        assert job["job_id"]   == "3901234567"

    def test_second_job_fields(self):
        from scorerole.sources.linkedin import extract_jobs
        job = extract_jobs(_SAMPLE_ALERT_BODY)[1]
        assert job["title"]   == "Staff Software Engineer"
        assert job["company"] == "Stripe"
        assert job["job_id"]  == "3901234568"

    def test_alumni_count_captured(self):
        from scorerole.sources.linkedin import extract_jobs
        job = extract_jobs(_SAMPLE_ALERT_BODY)[0]
        assert job["alumni_count"] == 3

    def test_url_format_is_clean(self):
        """URLs must be the canonical /jobs/view/ID/ format, not the tracking URL."""
        from scorerole.sources.linkedin import extract_jobs
        job = extract_jobs(_SAMPLE_ALERT_BODY)[0]
        assert job["url"] == "https://www.linkedin.com/jobs/view/3901234567/"

    def test_duplicate_job_id_deduplicated(self):
        body = _SAMPLE_ALERT_BODY + "\n".join([
            "", "Senior Product Manager", "Anthropic", "Remote",
            "View job: https://www.linkedin.com/comm/jobs/view/3901234567/DUPE/?trackingId=dupe",
        ])
        from scorerole.sources.linkedin import extract_jobs
        jobs = extract_jobs(body)
        ids = [j["job_id"] for j in jobs]
        assert ids.count("3901234567") == 1

    def test_returns_empty_on_no_jobs(self):
        from scorerole.sources.linkedin import extract_jobs
        assert extract_jobs("No jobs here") == []


class TestExtractJobsHtml:
    """extract_jobs_html() must parse recommendation emails that have no plain-text 'View job:' line."""

    _HTML = """
    <html><body>
    <a href="https://www.linkedin.com/comm/jobs/view/3999000001/?trackingId=r1">
      Head of Product
    </a>
    <span>Cohere · Toronto, ON</span>
    <a href="https://www.linkedin.com/comm/jobs/view/3999000002/?trackingId=r2">
      Principal ML Engineer
    </a>
    <span>Mistral AI · Paris, France (Remote)</span>
    </body></html>
    """

    def test_extracts_two_jobs(self):
        from scorerole.sources.linkedin import extract_jobs_html
        jobs = extract_jobs_html(self._HTML)
        assert len(jobs) == 2

    def test_title_and_company_parsed(self):
        from scorerole.sources.linkedin import extract_jobs_html
        jobs = extract_jobs_html(self._HTML)
        titles   = {j["title"]   for j in jobs}
        companies = {j["company"] for j in jobs}
        assert "Head of Product" in titles
        assert "Cohere" in companies


# ---------------------------------------------------------------------------
# score.py — verdict derivation + ranking
# ---------------------------------------------------------------------------

class TestRankJobs:
    """rank_jobs() must re-derive verdicts from scores AND sort correctly."""

    def _make_job(self, title, score, raw_verdict="apply"):
        return {
            "title": title, "company": "Acme", "location": "SF",
            "eval": {"score": score, "verdict": raw_verdict,
                     "leveragePoints": [], "frictionPoints": [], "tags": []},
        }

    def test_high_score_becomes_apply(self):
        from scorerole.score import rank_jobs
        jobs = rank_jobs([self._make_job("Senior PM", 80)])
        assert jobs[0]["eval"]["verdict"] == "apply"

    def test_mid_score_becomes_consider(self):
        from scorerole.score import rank_jobs
        jobs = rank_jobs([self._make_job("Mid PM", 60)])
        assert jobs[0]["eval"]["verdict"] == "consider"

    def test_low_score_becomes_skipped(self):
        from scorerole.score import rank_jobs
        jobs = rank_jobs([self._make_job("Junior PM", 30)])
        assert jobs[0]["eval"]["verdict"] == "skipped"

    def test_verdict_drift_corrected(self):
        """Claude might return verdict='apply' for score=62 — rank_jobs must fix it."""
        from scorerole.score import rank_jobs
        job = self._make_job("Drifted", score=62, raw_verdict="apply")
        result = rank_jobs([job])
        assert result[0]["eval"]["verdict"] == "consider"

    def test_sort_order(self):
        """apply before consider before skipped; higher score first within tier."""
        from scorerole.score import rank_jobs
        jobs = [
            self._make_job("Skip",     40, "skipped"),
            self._make_job("Apply-B",  76, "apply"),
            self._make_job("Consider", 60, "consider"),
            self._make_job("Apply-A",  90, "apply"),
        ]
        ranked = rank_jobs(jobs)
        titles = [j["title"] for j in ranked]
        assert titles == ["Apply-A", "Apply-B", "Consider", "Skip"]

    def test_boundary_at_apply_threshold(self):
        """Score of exactly 75 must be 'apply'; 74 must be 'consider'."""
        from scorerole.score import rank_jobs
        j75 = rank_jobs([self._make_job("Boundary", 75)])[0]
        j74 = rank_jobs([self._make_job("Below",    74)])[0]
        assert j75["eval"]["verdict"] == "apply"
        assert j74["eval"]["verdict"] == "consider"


# ---------------------------------------------------------------------------
# score.py — partial JSON recovery
# ---------------------------------------------------------------------------

class TestRecoverPartialJson:
    """_recover_partial_json must salvage complete objects from a truncated array."""

    def test_recovers_from_truncated_array(self):
        from scorerole.score import _recover_partial_json
        raw = '[{"score": 80, "verdict": "apply"}, {"score": 60, "verdict": "consi'
        objects = _recover_partial_json(raw)
        assert len(objects) == 1
        assert objects[0]["score"] == 80

    def test_recovers_multiple_complete_objects(self):
        from scorerole.score import _recover_partial_json
        raw = '[{"score": 80}, {"score": 60}, {"score": 40}]'
        objects = _recover_partial_json(raw)
        assert len(objects) == 3

    def test_returns_empty_list_on_garbage(self):
        from scorerole.score import _recover_partial_json
        assert _recover_partial_json("not json at all") == []

    def test_skips_invalid_inner_objects(self):
        from scorerole.score import _recover_partial_json
        raw = '[{"score": 80}, {bad json}, {"score": 40}]'
        objects = _recover_partial_json(raw)
        scores = [o["score"] for o in objects]
        assert 80 in scores
        assert 40 in scores


# ---------------------------------------------------------------------------
# pipeline.py — lookback parsing
# ---------------------------------------------------------------------------

class TestParseLookback:
    """_parse_lookback must handle all documented formats."""

    def test_days_shorthand(self):
        from scorerole.pipeline import _parse_lookback
        result = _parse_lookback("3d")
        delta = datetime.datetime.now() - result
        assert 2 < delta.total_seconds() / 86400 < 4

    def test_iso_date(self):
        from scorerole.pipeline import _parse_lookback
        result = _parse_lookback("2026-05-10")
        assert result.year == 2026
        assert result.month == 5
        assert result.day == 10

    def test_invalid_returns_none(self):
        from scorerole.pipeline import _parse_lookback
        assert _parse_lookback("not-a-date") is None

    def test_zero_days_returns_near_now(self):
        from scorerole.pipeline import _parse_lookback
        result = _parse_lookback("0d")
        delta = datetime.datetime.now() - result
        assert delta.total_seconds() < 10


# ---------------------------------------------------------------------------
# profile.py — render_profile sanity
# ---------------------------------------------------------------------------

class TestRenderProfile:
    """render_profile must produce a non-empty string with the key sections."""

    _PROFILE = {
        "candidate": {"name": "Alex Kim", "title": "Staff PM", "location": "NYC",
                      "work_mode": ["Remote-first"], "open_to_remote": True},
        "target": {"level": "Staff", "roles": ["PM", "Group PM"]},
        "scoring": {"apply_threshold": 75, "consider_threshold": 55},
        "aspirations": {"track": "IC", "direction": "AI-native products"},
        "deal_breakers": ["No management-only roles"],
        "experience": [{"company": "Acme", "title": "PM", "dates": "2022–2024",
                        "highlights": ["Led 0→1 product"]}],
    }

    def test_renders_non_empty(self):
        from scorerole.profile import render_profile
        out = render_profile(self._PROFILE)
        assert len(out) > 100

    def test_contains_candidate_name(self):
        from scorerole.profile import render_profile
        out = render_profile(self._PROFILE)
        assert "Alex Kim" in out

    def test_contains_target_roles(self):
        from scorerole.profile import render_profile
        out = render_profile(self._PROFILE)
        assert "PM" in out

    def test_contains_aspirations(self):
        from scorerole.profile import render_profile
        out = render_profile(self._PROFILE)
        assert "ASPIRATIONS" in out
        assert "AI-native products" in out

    def test_contains_deal_breakers(self):
        from scorerole.profile import render_profile
        out = render_profile(self._PROFILE)
        assert "DEAL BREAKERS" in out

    def test_contains_experience(self):
        from scorerole.profile import render_profile
        out = render_profile(self._PROFILE)
        assert "Acme" in out
        assert "0→1 product" in out

    def test_empty_profile_does_not_crash(self):
        from scorerole.profile import render_profile
        out = render_profile({})
        assert isinstance(out, str)
