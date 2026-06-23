"""Unit tests for feedback_cmd.py.

Covers: ID generation, file I/O, entry parsing (new + legacy format),
feedback_log writing, and Claude processing (mocked).
"""
from __future__ import annotations

import json
import re
import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_data(tmp_path, monkeypatch):
    """Redirect DATA_DIR and all derived paths to a temp directory."""
    import scorerole.state as state_mod
    import scorerole.feedback as fb_mod

    monkeypatch.setattr(state_mod, "DATA_DIR",      tmp_path)
    monkeypatch.setattr(state_mod, "LAST_RUN_FILE", tmp_path / "last_run.json")
    monkeypatch.setattr(state_mod, "FEEDBACK_FILE", tmp_path / "feedback.md")

    monkeypatch.setattr(fb_mod, "DATA_DIR",          tmp_path)
    monkeypatch.setattr(fb_mod, "LAST_RUN_FILE",     tmp_path / "last_run.json")
    monkeypatch.setattr(fb_mod, "FEEDBACK_FILE",     tmp_path / "feedback.md")
    monkeypatch.setattr(fb_mod, "FEEDBACK_LOG_FILE", tmp_path / "feedback_log.jsonl")

    return tmp_path


# ---------------------------------------------------------------------------
# _feedback_id
# ---------------------------------------------------------------------------

def test_feedback_id_format():
    from scorerole.feedback import _feedback_id
    fid = _feedback_id()
    assert re.match(r"^fb_\d{8}_[0-9a-f]{6}$", fid), f"Unexpected format: {fid}"


def test_feedback_id_unique():
    from scorerole.feedback import _feedback_id
    ids = {_feedback_id() for _ in range(50)}
    assert len(ids) == 50


# ---------------------------------------------------------------------------
# append_feedback_entry
# ---------------------------------------------------------------------------

def test_append_creates_file_with_header(tmp_data):
    from scorerole.feedback import append_feedback_entry, FEEDBACK_FILE
    append_feedback_entry("test note", "fb_20260619_abcd", "June_18_2026", {})
    content = FEEDBACK_FILE.read_text()
    assert "# Scoring Feedback" in content
    assert "## [user]" in content
    assert "test note" in content


def test_append_includes_comment_header(tmp_data):
    from scorerole.feedback import append_feedback_entry, FEEDBACK_FILE
    append_feedback_entry(
        "GitLab score too low",
        "fb_20260619_abcd",
        "June_18_2026",
        {"roles": ["gitlab"], "dims": ["culture_values"]},
    )
    content = FEEDBACK_FILE.read_text()
    assert "<!-- id:fb_20260619_abcd" in content
    assert "roles:gitlab" in content
    assert "dims:culture_values" in content


def test_append_accumulates_entries(tmp_data):
    from scorerole.feedback import append_feedback_entry, FEEDBACK_FILE
    append_feedback_entry("first note",  "fb_20260619_0001", None, {})
    append_feedback_entry("second note", "fb_20260619_0002", None, {})
    content = FEEDBACK_FILE.read_text()
    assert content.count("## [user]") == 2
    assert "first note" in content
    assert "second note" in content


def test_append_sets_permissions(tmp_data):
    from scorerole.feedback import append_feedback_entry, FEEDBACK_FILE
    append_feedback_entry("note", "fb_20260619_abcd", None, {})
    mode = FEEDBACK_FILE.stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------------
# load_feedback_text
# ---------------------------------------------------------------------------

def test_load_returns_none_when_missing(tmp_data):
    from scorerole.feedback import load_feedback_text
    assert load_feedback_text() is None


def test_load_returns_none_when_empty(tmp_data):
    from scorerole.feedback import load_feedback_text, FEEDBACK_FILE
    FEEDBACK_FILE.write_text("   \n")
    assert load_feedback_text() is None


def test_load_returns_content(tmp_data):
    from scorerole.feedback import append_feedback_entry, load_feedback_text
    append_feedback_entry("important note", "fb_001", None, {})
    text = load_feedback_text()
    assert text is not None
    assert "important note" in text


def test_load_returns_all_entries_no_ttl(tmp_data):
    """All entries are returned regardless of age — no TTL applied."""
    from scorerole.feedback import FEEDBACK_FILE, load_feedback_text, _FEEDBACK_HEADER
    old_entry = (
        "\n<!-- id:fb_20250101_aaaa | run:unknown | roles: | dims: -->\n"
        "## [user] 2025-01-01\n\nOld feedback from a year ago.\n"
    )
    new_entry = (
        "\n<!-- id:fb_20260619_bbbb | run:unknown | roles: | dims: -->\n"
        "## [user] 2026-06-19\n\nNew feedback today.\n"
    )
    FEEDBACK_FILE.write_text(_FEEDBACK_HEADER + old_entry + new_entry)
    text = load_feedback_text()
    assert text is not None
    assert "Old feedback from a year ago" in text
    assert "New feedback today" in text


# ---------------------------------------------------------------------------
# write_feedback_log
# ---------------------------------------------------------------------------

def test_write_feedback_log_creates_jsonl(tmp_data):
    from scorerole.feedback import write_feedback_log, FEEDBACK_LOG_FILE
    write_feedback_log("fb_001", "June_18_2026", "some text", ["gitlab"], ["culture_values"])
    lines = FEEDBACK_LOG_FILE.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["feedback_id"] == "fb_001"
    assert record["run_id"] == "June_18_2026"
    assert record["roles"] == ["gitlab"]
    assert record["dims"] == ["culture_values"]
    assert "timestamp" in record


def test_write_feedback_log_appends(tmp_data):
    from scorerole.feedback import write_feedback_log, FEEDBACK_LOG_FILE
    write_feedback_log("fb_001", None, "text", [], [])
    write_feedback_log("fb_002", None, "more text", [], [])
    lines = FEEDBACK_LOG_FILE.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["feedback_id"] == "fb_001"
    assert json.loads(lines[1])["feedback_id"] == "fb_002"


def test_write_feedback_log_sets_permissions(tmp_data):
    from scorerole.feedback import write_feedback_log, FEEDBACK_LOG_FILE
    write_feedback_log("fb_001", None, "x", [], [])
    mode = FEEDBACK_LOG_FILE.stat().st_mode & 0o777
    assert mode == 0o600


# ---------------------------------------------------------------------------
# _parse_entries
# ---------------------------------------------------------------------------

def test_parse_entries_new_format(tmp_data):
    from scorerole.feedback import _parse_entries, _FEEDBACK_HEADER
    content = (
        _FEEDBACK_HEADER
        + "\n<!-- id:fb_20260619_abcd | run:June_18_2026 | roles:gitlab | dims:culture_values -->\n"
        + "## [user] 2026-06-19\n\nGitLab score too low.\n"
    )
    entries = _parse_entries(content)
    assert len(entries) == 1
    assert entries[0]["id"] == "fb_20260619_abcd"
    assert entries[0]["date"] == "2026-06-19"
    assert entries[0]["source"] == "[user]"
    assert "GitLab" in entries[0]["first_line"]


def test_parse_entries_legacy_format():
    """Legacy entries (no comment header) parse without ID."""
    from scorerole.feedback import _parse_entries
    content = "# Scoring Feedback\n\n## 2026-06-10\n\nOld style feedback.\n"
    entries = _parse_entries(content)
    assert len(entries) == 1
    assert entries[0]["id"] is None
    assert entries[0]["date"] == "2026-06-10"
    assert "Old style" in entries[0]["first_line"]


def test_parse_entries_multiple(tmp_data):
    from scorerole.feedback import _parse_entries, _FEEDBACK_HEADER
    content = (
        _FEEDBACK_HEADER
        + "\n<!-- id:fb_001 | run:r1 | roles:a | dims:d1 -->\n## [user] 2026-06-01\n\nFirst.\n"
        + "\n<!-- id:fb_002 | run:r2 | roles:b | dims:d2 -->\n## [user] 2026-06-15\n\nSecond.\n"
    )
    entries = _parse_entries(content)
    assert len(entries) == 2
    assert entries[0]["id"] == "fb_001"
    assert entries[1]["id"] == "fb_002"


def test_parse_entries_empty_file():
    from scorerole.feedback import _parse_entries
    assert _parse_entries("# Scoring Feedback\n") == []


def test_parse_entries_auto_tag():
    from scorerole.feedback import _parse_entries
    content = "## [auto] 2026-06-19\n\nApplied to Qventus.\n"
    entries = _parse_entries(content)
    assert entries[0]["source"] == "[auto]"


# ---------------------------------------------------------------------------
# _claude_process (mocked)
# ---------------------------------------------------------------------------

def _make_mock_response(text: str) -> MagicMock:
    msg  = MagicMock()
    blk  = MagicMock()
    blk.text = text
    msg.content = [blk]
    return msg


def test_claude_process_returns_structured(tmp_data):
    from scorerole.feedback import _claude_process

    valid_json = json.dumps({
        "roles":         [{"company": "GitLab", "title": "PM", "score": 86,
                           "direction": "right", "dim": "culture_values", "note": "caution wrong"}],
        "general_notes": ["Weight AI-native culture more"],
        "conflicts":     [],
        "profile_items": [],
        "dims":          ["culture_values"],
    })

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(valid_json)

    fb_anthropic = MagicMock()
    fb_anthropic.Anthropic.return_value = mock_client
    with patch.dict("sys.modules", {"anthropic": fb_anthropic}):
        result = _claude_process("GitLab score is right but caution wrong", None, "fake-key")

    assert result is not None
    assert result["roles"][0]["company"] == "GitLab"
    assert result["dims"] == ["culture_values"]


def test_claude_process_returns_none_on_bad_json(tmp_data):
    from scorerole.feedback import _claude_process

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response("not valid json {{{")

    fb_anthropic = MagicMock()
    fb_anthropic.Anthropic.return_value = mock_client
    with patch.dict("sys.modules", {"anthropic": fb_anthropic}):
        result = _claude_process("some feedback", None, "fake-key")

    assert result is None


def test_claude_process_returns_none_on_api_error(tmp_data):
    from scorerole.feedback import _claude_process

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("API error")

    fb_anthropic = MagicMock()
    fb_anthropic.Anthropic.return_value = mock_client
    with patch.dict("sys.modules", {"anthropic": fb_anthropic}):
        result = _claude_process("some feedback", None, "fake-key")

    assert result is None


def test_claude_process_fills_missing_keys(tmp_data):
    """Claude response missing some keys — defaults filled in."""
    from scorerole.feedback import _claude_process

    partial_json = json.dumps({"roles": [], "general_notes": ["good note"]})

    mock_client = MagicMock()
    mock_client.messages.create.return_value = _make_mock_response(partial_json)

    fb_anthropic = MagicMock()
    fb_anthropic.Anthropic.return_value = mock_client
    with patch.dict("sys.modules", {"anthropic": fb_anthropic}):
        result = _claude_process("note", None, "fake-key")

    assert result is not None
    assert result.get("conflicts") == []
    assert result.get("profile_items") == []
    assert result.get("dims") == []


# ---------------------------------------------------------------------------
# save_last_run / load_last_run
# ---------------------------------------------------------------------------

def _make_job(title: str, company: str, score: int, verdict: str) -> dict:
    return {
        "title":   title,
        "company": company,
        "eval":    {"score": score, "verdict": verdict},
    }


def test_save_and_load_last_run(tmp_data):
    from scorerole.feedback import save_last_run, load_last_run
    jobs = [
        _make_job("PM, AI", "GitLab",  86, "apply"),
        _make_job("PM",     "Headway", 78, "apply"),
        _make_job("PM",     "Google",  42, "skipped"),
    ]
    save_last_run(jobs, "June 19, 2026")
    run = load_last_run()
    assert run is not None
    assert run["apply_count"] == 2
    assert run["skipped_count"] == 1
    assert run["total_evaluated"] == 3
    assert run["run_date"] == "June 19, 2026"
    # Roles list contains only apply+consider, sorted by score descending
    assert run["roles"][0]["company"] == "GitLab"
    assert run["roles"][1]["company"] == "Headway"


def test_load_last_run_returns_none_when_missing(tmp_data):
    from scorerole.feedback import load_last_run
    assert load_last_run() is None
