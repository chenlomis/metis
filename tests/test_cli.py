"""CLI smoke tests.

These tests keep command parsing/routing stable while the implementation is
split away from the digest pipeline orchestration.
"""
from __future__ import annotations

import pytest


def test_cli_help_renders_without_logging_side_effects(capsys):
    from metis import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage: metis" in out
    assert "sources" in out
    assert "track" in out
    assert "init_bak" not in out


def test_sources_help_renders(capsys):
    from metis import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["sources", "--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "metis sources add Stripe" in out


def test_default_command_routes_to_pipeline(monkeypatch):
    from metis import cli

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: calls.setdefault("validated", require_gmail))
    monkeypatch.setattr(cli, "_parse_lookback", lambda value: "SINCE")
    monkeypatch.setattr(cli, "run_pipeline", lambda **kwargs: calls.update(kwargs))

    cli.main(["--lookback", "1d", "--dry-run"])

    assert calls["validated"] is True
    assert calls["since_dt"] == "SINCE"
    assert calls["score_all"] is False
    assert calls["dry_run"] is True


def test_feedback_list_routes_without_gmail_validation(monkeypatch):
    from metis import cli
    import metis.feedback as feedback

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: calls.update(require_gmail=require_gmail))
    monkeypatch.setattr(feedback, "run_feedback_list", lambda: calls.update(listed=True))

    cli.main(["feedback", "list"])

    assert calls["require_gmail"] is False
    assert calls["listed"] is True


def test_resume_tailor_routes_with_provider_key_without_gmail_validation(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: calls.update(require_gmail=require_gmail))
    monkeypatch.setattr(cli, "LLM_API_KEY", "provider-key")
    monkeypatch.setattr(
        resume_cmd,
        "run_resume_tailor",
        lambda **kwargs: calls.update(kwargs) or [],
    )

    cli.main(["resume", "tailor"])

    assert calls["require_gmail"] is False
    assert calls["api_key"] == "provider-key"
    assert calls["resume_path"] is None
    assert calls["tailor_all"] is False


def test_profile_evidence_index_routes_without_gmail_validation(monkeypatch):
    from metis import cli
    import metis.profile_evidence as profile_evidence

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: calls.update(require_gmail=require_gmail))
    monkeypatch.setattr(
        profile_evidence,
        "write_evidence_index",
        lambda output=None: calls.update(output=output) or "/tmp/profile.evidence.index.yaml",
    )

    cli.main(["profile", "evidence-index"])

    assert calls["require_gmail"] is False
    assert calls["output"] is None


def test_resume_tailor_resume_path_routes(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(resume_cmd, "run_resume_tailor", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["resume", "tailor", "--resume", "/tmp/resume.docx"])

    assert calls["resume_path"] == "/tmp/resume.docx"


def test_resume_tailor_all_flag_routes(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(resume_cmd, "run_resume_tailor", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["resume", "tailor", "--all"])

    assert calls["tailor_all"] is True


def test_resume_tailor_top_flag_routes(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(resume_cmd, "run_resume_tailor", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["resume", "tailor", "--top", "3"])

    assert calls["top_n"] == 3


def test_summary_sends_real_report_by_default(monkeypatch):
    from metis import cli
    import metis.report_cmd as report_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(report_cmd, "run_report", lambda **kwargs: calls.update(kwargs))

    cli.main(["summary"])

    assert calls["preview"] is False


def test_summary_preview_flag_is_explicit(monkeypatch):
    from metis import cli
    import metis.report_cmd as report_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(report_cmd, "run_report", lambda **kwargs: calls.update(kwargs))

    cli.main(["summary", "--preview"])

    assert calls["preview"] is True
