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
    assert "metis sources                    show all active sources" in out


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


def test_configure_logging_falls_back_to_console_when_log_file_unwritable(monkeypatch, capsys, tmp_path):
    from metis import cli

    def raise_permission_error(*_args, **_kwargs):
        raise PermissionError("log denied")

    monkeypatch.setattr(cli, "LOG_DIR", tmp_path)
    monkeypatch.setattr(cli.logging, "FileHandler", raise_permission_error)
    monkeypatch.setattr(cli.logging, "basicConfig", lambda **_kwargs: None)

    cli._configure_logging()

    err = capsys.readouterr().err
    assert "Continuing with console logs only" in err


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


def test_tailor_routes_with_provider_key_without_gmail_validation(monkeypatch):
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

    cli.main(["tailor"])

    assert calls["require_gmail"] is False
    assert calls["api_key"] == "provider-key"
    assert calls["resume_path"] is None
    assert calls["tailor_all"] is False


def test_tailor_resume_path_routes(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(resume_cmd, "run_resume_tailor", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["tailor", "--resume", "/tmp/resume.docx"])

    assert calls["resume_path"] == "/tmp/resume.docx"


def test_tailor_all_flag_routes(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(resume_cmd, "run_resume_tailor", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["tailor", "--all"])

    assert calls["tailor_all"] is True


def test_tailor_top_flag_routes(monkeypatch):
    from metis import cli
    import metis.resume_cmd as resume_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(resume_cmd, "run_resume_tailor", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["tailor", "--top", "3"])

    assert calls["top_n"] == 3


def test_config_autofill_routes(monkeypatch):
    from metis import cli
    import metis.config_apply_cmd as config_apply_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(config_apply_cmd, "run_config_apply", lambda **kwargs: calls.update(kwargs))

    cli.main(["config", "autofill", "--show"])

    assert calls["show"] is True


def test_config_profile_reuses_init_flow(monkeypatch):
    from metis import cli
    import metis.init_cmd as init_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: calls.update(require_gmail=require_gmail))
    monkeypatch.setattr(init_cmd, "run_init", lambda **kwargs: calls.update(kwargs))

    cli.main(["config", "profile"])

    assert calls["require_gmail"] is False
    assert calls["api_key"] == cli.ANTHROPIC_API_KEY


def test_apply_extended_flags_route(monkeypatch):
    from metis import cli
    import metis.apply_cmd as apply_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "GMAIL_ADDRESS", "")
    monkeypatch.setattr(cli, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(apply_cmd, "run_apply", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["apply", "--lookback", "7d", "--latest", "4", "--default-resume"])

    assert calls["lookback"] == "7d"
    assert calls["latest_n"] == 4
    assert calls["force_default_resume"] is True


def test_apply_selection_modes_are_mutually_exclusive():
    from metis import cli

    with pytest.raises(SystemExit) as exc:
        cli.main(["apply", "--top", "3", "--latest", "3"])

    assert exc.value.code == 2


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


def test_summary_rejects_invalid_lookback(monkeypatch, capsys):
    from metis import cli

    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(cli, "_parse_lookback", lambda value: None)

    with pytest.raises(SystemExit) as exc:
        cli.main(["summary", "--lookback", "garbage"])

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Could not parse --lookback 'garbage'" in out


def test_track_keyboard_interrupt_exits_cleanly(monkeypatch, capsys):
    from metis import cli
    import metis.track as track

    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(cli, "_parse_lookback", lambda value: "SINCE")

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(track, "run_track", interrupt)

    with pytest.raises(SystemExit) as exc:
        cli.main(["track", "--lookback", "7d", "--dry-run"])

    assert exc.value.code == 130
    out = capsys.readouterr().out
    assert "Track interrupted before completion" in out


def test_track_routes_provider_key(monkeypatch):
    from metis import cli
    import metis.track as track

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(cli, "_parse_lookback", lambda value: "SINCE")
    monkeypatch.setattr(cli, "LLM_API_KEY", "provider-key")
    monkeypatch.setattr(cli, "ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setattr(track, "run_track", lambda **kwargs: calls.update(kwargs))

    cli.main(["track", "--lookback", "7d", "--dry-run"])

    assert calls["api_key"] == "provider-key"
