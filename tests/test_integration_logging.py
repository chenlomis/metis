"""Integration tests for trace.py and feedback_cmd.py (logging/feedback modules).

Covers:
  - write_trace: correct record shape, RUNS_PATH import from state, exception swallowing
  - save_last_run / load_last_run: round-trip, scoring verdicts, file permissions
  - append_feedback_entry: comment header format, appends to existing, creates with header
  - write_feedback_log: JSONL record shape, audit fields
  - _parse_entries: new format (with id comment), legacy format (bare ## header), mixed
  - Shared path constants: FEEDBACK_LOG_FILE and RUNS_PATH must live in state.py
"""
from __future__ import annotations
import json
import os
import stat
import datetime
import pytest
from pathlib import Path
from unittest import mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Redirect all state paths to a temp dir without touching ~/.job_pipeline."""
    d = tmp_path / "job_pipeline"
    d.mkdir()

    import metis.state as state_mod
    monkeypatch.setattr(state_mod, "DATA_DIR", d)
    monkeypatch.setattr(state_mod, "LAST_RUN_FILE", d / "last_run.json")
    monkeypatch.setattr(state_mod, "FEEDBACK_FILE", d / "feedback.md")
    monkeypatch.setattr(state_mod, "FEEDBACK_LOG_FILE", d / "feedback_log.jsonl")
    monkeypatch.setattr(state_mod, "RUNS_PATH", d / "runs.jsonl")

    # Patch derived constants in the sub-modules after import
    import metis.trace as trace_mod
    import metis.feedback as fb_mod
    monkeypatch.setattr(trace_mod, "RUNS_PATH", d / "runs.jsonl")
    monkeypatch.setattr(fb_mod, "DATA_DIR", d)
    monkeypatch.setattr(fb_mod, "LAST_RUN_FILE", d / "last_run.json")
    monkeypatch.setattr(fb_mod, "FEEDBACK_FILE", d / "feedback.md")
    monkeypatch.setattr(fb_mod, "FEEDBACK_LOG_FILE", d / "feedback_log.jsonl")

    return d


# ---------------------------------------------------------------------------
# Shared component gaps — path constants must live in state.py
# ---------------------------------------------------------------------------

class TestSharedPathConstants:
    def test_feedback_log_file_exported_from_state(self):
        from metis.state import FEEDBACK_LOG_FILE
        assert "feedback_log.jsonl" in str(FEEDBACK_LOG_FILE)

    def test_runs_path_exported_from_state(self):
        from metis.state import RUNS_PATH
        assert "runs.jsonl" in str(RUNS_PATH)

    def test_trace_imports_runs_path_from_state(self):
        """trace.py must not define RUNS_PATH locally."""
        import metis.trace as trace_mod
        import metis.state as state_mod
        # They must be the same object (imported, not re-defined)
        assert trace_mod.RUNS_PATH is state_mod.RUNS_PATH

    def test_feedback_cmd_imports_feedback_log_from_state(self):
        """feedback_cmd.py must not define FEEDBACK_LOG_FILE locally."""
        import metis.feedback as fb_mod
        import metis.state as state_mod
        assert fb_mod.FEEDBACK_LOG_FILE is state_mod.FEEDBACK_LOG_FILE


# ---------------------------------------------------------------------------
# trace.write_trace
# ---------------------------------------------------------------------------

class TestWriteTrace:
    def _sample_job(self, verdict="apply") -> dict:
        return {
            "title": "Staff PM",
            "company": "Acme",
            "location": "Remote",
            "source": "linkedin",
            "extraction": {"seniority": "staff"},
            "eval": {"verdict": verdict, "score": 82},
        }

    def test_appends_jsonl_record(self, tmp_data_dir):
        from metis.trace import write_trace
        write_trace(self._sample_job())
        lines = (tmp_data_dir / "runs.jsonl").read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["title"] == "Staff PM"
        assert record["company"] == "Acme"

    def test_record_has_required_fields(self, tmp_data_dir):
        from metis.trace import write_trace
        write_trace(self._sample_job())
        record = json.loads((tmp_data_dir / "runs.jsonl").read_text())
        required = {"ts", "role_hash", "title", "company", "location",
                    "source", "extraction", "eval", "prompt_version", "model"}
        assert required.issubset(set(record.keys()))

    def test_multiple_calls_each_appends_a_line(self, tmp_data_dir):
        from metis.trace import write_trace
        write_trace(self._sample_job())
        write_trace(self._sample_job(verdict="consider"))
        lines = (tmp_data_dir / "runs.jsonl").read_text().splitlines()
        assert len(lines) == 2

    def test_role_hash_is_stable(self, tmp_data_dir):
        from metis.trace import write_trace
        write_trace(self._sample_job())
        write_trace(self._sample_job())
        records = [(json.loads(l)["role_hash"]) for l in
                   (tmp_data_dir / "runs.jsonl").read_text().splitlines()]
        assert records[0] == records[1]

    def test_swallows_exception_on_bad_path(self, tmp_data_dir, monkeypatch):
        import metis.trace as trace_mod
        monkeypatch.setattr(trace_mod, "RUNS_PATH", Path("/no/such/directory/runs.jsonl"))
        # Must not raise
        from metis.trace import write_trace
        write_trace(self._sample_job())  # silent warning, no crash

    def test_empty_job_does_not_crash(self, tmp_data_dir):
        from metis.trace import write_trace
        write_trace({})  # all fields optional / have defaults
        record = json.loads((tmp_data_dir / "runs.jsonl").read_text())
        assert "ts" in record


# ---------------------------------------------------------------------------
# feedback_cmd.save_last_run / load_last_run
# ---------------------------------------------------------------------------

class TestLastRunPersistence:
    def _make_jobs(self) -> list[dict]:
        return [
            {"title": "Staff PM", "company": "Stripe", "eval": {"verdict": "apply", "score": 88}},
            {"title": "PM II", "company": "Boring Corp", "eval": {"verdict": "consider", "score": 62}},
            {"title": "Jr PM", "company": "Tiny", "eval": {"verdict": "skipped", "score": 40}},
        ]

    def test_round_trip(self, tmp_data_dir):
        from metis.feedback import save_last_run, load_last_run
        save_last_run(self._make_jobs(), run_date="2026-06-19")
        result = load_last_run()
        assert result is not None
        assert result["run_date"] == "2026-06-19"

    def test_counts_by_verdict(self, tmp_data_dir):
        from metis.feedback import save_last_run, load_last_run
        save_last_run(self._make_jobs(), run_date="2026-06-19", filtered_count=5)
        result = load_last_run()
        assert result["apply_count"] == 1
        assert result["consider_count"] == 1
        assert result["skipped_count"] == 1
        assert result["filtered_count"] == 5

    def test_roles_sorted_by_score_descending(self, tmp_data_dir):
        from metis.feedback import save_last_run, load_last_run
        save_last_run(self._make_jobs(), run_date="2026-06-19")
        result = load_last_run()
        scores = [r["score"] for r in result["roles"]]
        assert scores == sorted(scores, reverse=True)

    def test_skipped_jobs_excluded_from_roles_list(self, tmp_data_dir):
        from metis.feedback import save_last_run, load_last_run
        save_last_run(self._make_jobs(), run_date="2026-06-19")
        result = load_last_run()
        verdicts = [r["verdict"] for r in result["roles"]]
        assert "skipped" not in verdicts

    def test_load_returns_none_when_missing(self, tmp_data_dir):
        from metis.feedback import load_last_run
        assert load_last_run() is None

    def test_load_returns_none_on_corrupt_json(self, tmp_data_dir):
        from metis.feedback import load_last_run
        (tmp_data_dir / "last_run.json").write_text("not json {{{")
        assert load_last_run() is None


# ---------------------------------------------------------------------------
# feedback_cmd.append_feedback_entry
# ---------------------------------------------------------------------------

class TestAppendFeedbackEntry:
    def test_creates_file_with_header_on_first_call(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry("Score FinTech higher", feedback_id="fb_test_01", run_id="run_abc")
        content = (tmp_data_dir / "feedback.md").read_text()
        assert "# Scoring Feedback" in content
        assert "Score FinTech higher" in content

    def test_appends_to_existing_file(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry("First note", feedback_id="fb_test_01", run_id=None)
        append_feedback_entry("Second note", feedback_id="fb_test_02", run_id=None)
        content = (tmp_data_dir / "feedback.md").read_text()
        assert "First note" in content
        assert "Second note" in content

    def test_two_calls_produce_two_entries_not_one(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry("A", feedback_id="fb_test_01", run_id=None)
        append_feedback_entry("A", feedback_id="fb_test_02", run_id=None)
        content = (tmp_data_dir / "feedback.md").read_text()
        # Two separate ## headers
        assert content.count("## [user]") == 2

    def test_comment_header_contains_id(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry("Note", feedback_id="fb_20260619_abc123", run_id="run_xyz")
        content = (tmp_data_dir / "feedback.md").read_text()
        assert "id:fb_20260619_abc123" in content

    def test_comment_header_contains_run_id(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry("Note", feedback_id="fb_001", run_id="June_19_2026")
        content = (tmp_data_dir / "feedback.md").read_text()
        assert "run:June_19_2026" in content

    def test_meta_roles_and_dims_in_comment(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry(
            "Note",
            feedback_id="fb_001",
            run_id=None,
            meta={"roles": ["stripe", "gitlab"], "dims": ["culture_values"]},
        )
        content = (tmp_data_dir / "feedback.md").read_text()
        assert "roles:stripe,gitlab" in content
        assert "dims:culture_values" in content

    def test_file_permissions_are_owner_only(self, tmp_data_dir):
        from metis.feedback import append_feedback_entry
        append_feedback_entry("Note", feedback_id="fb_001", run_id=None)
        mode = (tmp_data_dir / "feedback.md").stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


# ---------------------------------------------------------------------------
# feedback_cmd.write_feedback_log
# ---------------------------------------------------------------------------

class TestWriteFeedbackLog:
    def test_appends_jsonl_record(self, tmp_data_dir):
        from metis.feedback import write_feedback_log
        write_feedback_log(
            feedback_id="fb_001",
            run_id="run_xyz",
            raw_text="I dislike pure sales roles",
            roles=["company_a"],
            dims=["industry_domain"],
        )
        lines = (tmp_data_dir / "feedback_log.jsonl").read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["feedback_id"] == "fb_001"

    def test_record_has_required_fields(self, tmp_data_dir):
        from metis.feedback import write_feedback_log
        write_feedback_log("fb_001", "run_xyz", "text", [], [])
        record = json.loads((tmp_data_dir / "feedback_log.jsonl").read_text())
        required = {"feedback_id", "run_id", "timestamp", "roles", "dims", "text_length"}
        assert required.issubset(set(record.keys()))

    def test_text_length_matches_input(self, tmp_data_dir):
        from metis.feedback import write_feedback_log
        text = "Prefer product-led growth companies"
        write_feedback_log("fb_001", None, text, [], [])
        record = json.loads((tmp_data_dir / "feedback_log.jsonl").read_text())
        assert record["text_length"] == len(text)

    def test_multiple_entries_each_on_own_line(self, tmp_data_dir):
        from metis.feedback import write_feedback_log
        write_feedback_log("fb_001", None, "A", [], [])
        write_feedback_log("fb_002", None, "B", [], [])
        lines = (tmp_data_dir / "feedback_log.jsonl").read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["feedback_id"] == "fb_002"


# ---------------------------------------------------------------------------
# feedback_cmd._parse_entries
# ---------------------------------------------------------------------------

class TestParseEntries:
    def _parse(self, content: str) -> list[dict]:
        from metis.feedback import _parse_entries
        return _parse_entries(content)

    def test_new_format_with_id_comment(self):
        content = (
            "# Scoring Feedback\n\n"
            "<!-- id:fb_001 | run:run_abc | roles:stripe | dims:culture_values -->\n"
            "## [user] 2026-06-19\n\n"
            "Score FinTech companies higher.\n"
        )
        entries = self._parse(content)
        assert len(entries) == 1
        assert entries[0]["id"] == "fb_001"
        assert "2026-06-19" in entries[0]["date"]

    def test_legacy_format_bare_header(self):
        content = (
            "# Scoring Feedback\n\n"
            "## 2026-05-01\n\n"
            "Old style entry with no id comment.\n"
        )
        entries = self._parse(content)
        assert len(entries) == 1
        assert entries[0]["id"] is None

    def test_mixed_new_and_legacy_entries(self):
        content = (
            "## 2026-05-01\n\nOld entry.\n\n"
            "<!-- id:fb_002 | run:r2 | roles: | dims: -->\n"
            "## [user] 2026-06-19\n\nNew entry.\n"
        )
        entries = self._parse(content)
        assert len(entries) == 2
        assert entries[0]["id"] is None
        assert entries[1]["id"] == "fb_002"

    def test_first_line_of_body_extracted(self):
        content = (
            "<!-- id:fb_001 | run:r | roles: | dims: -->\n"
            "## [user] 2026-06-19\n\n"
            "First line here.\nSecond line.\n"
        )
        entries = self._parse(content)
        assert entries[0]["first_line"] == "First line here."

    def test_source_tag_user(self):
        content = "## [user] 2026-06-19\n\nNote.\n"
        entries = self._parse(content)
        assert entries[0]["source"] == "[user]"

    def test_source_tag_auto(self):
        content = "## [auto] 2026-06-19\n\nNote.\n"
        entries = self._parse(content)
        assert entries[0]["source"] == "[auto]"

    def test_empty_content_returns_empty_list(self):
        assert self._parse("") == []
        assert self._parse("# Scoring Feedback\n\n") == []

    def test_comment_not_stolen_by_adjacent_entry(self):
        content = (
            "<!-- id:fb_001 | run:r1 | roles: | dims: -->\n"
            "## [user] 2026-06-18\n\nFirst.\n\n"
            "<!-- id:fb_002 | run:r2 | roles: | dims: -->\n"
            "## [user] 2026-06-19\n\nSecond.\n"
        )
        entries = self._parse(content)
        assert entries[0]["id"] == "fb_001"
        assert entries[1]["id"] == "fb_002"
