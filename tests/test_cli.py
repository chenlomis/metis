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


def test_sources_add_email_alias_routes_to_email_add(monkeypatch):
    from metis import cli
    import metis.sources_cmd as sources_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(
        sources_cmd,
        "run_sources",
        lambda *args, **kwargs: calls.update(
            action=args[0],
            name=args[1],
            add_all=kwargs.get("add_all"),
            email_action=kwargs.get("email_action"),
            email_sender=kwargs.get("email_sender"),
        ),
    )

    cli.main(["sources", "add", "email", "team@hi.wellfound.com"])

    assert calls == {
        "action": "email",
        "name": None,
        "add_all": False,
        "email_action": "add",
        "email_sender": "team@hi.wellfound.com",
    }


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
