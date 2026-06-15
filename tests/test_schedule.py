"""Tests for scorerole/schedule_cmd.py.

No OS calls are made here — launchctl and crontab are mocked throughout.
Tests cover: plist/crontab generation, schedule.json persistence, status
display, frequency-to-lookback mapping, and regression checks confirming
run_pipeline() is unaffected by schedule state.
"""
import json, os, platform, re, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock

import pytest

# Make scorerole importable from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from scorerole.schedule_cmd import (
    FREQUENCY_OPTIONS,
    LAUNCHD_LABEL,
    LAUNCHD_PLIST,
    SCHEDULE_FILE,
    WEEKDAY_TO_INT,
    _parse_time,
    build_crontab_line,
    build_plist,
    load_schedule,
    remove_schedule,
    show_schedule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_config(freq="daily", time="08:00", **extra) -> dict:
    cfg = {"frequency": freq, "time": time, "scorerole_bin": "/venv/bin/scorerole",
           "working_dir": "/project", "platform": "Darwin", **extra}
    return cfg


# ---------------------------------------------------------------------------
# Frequency metadata
# ---------------------------------------------------------------------------

class TestFrequencyOptions:
    def test_all_frequencies_present(self):
        assert set(FREQUENCY_OPTIONS) == {"daily", "twice_weekly", "weekly"}

    def test_lookback_values(self):
        assert FREQUENCY_OPTIONS["daily"]["lookback"]        == "1d"
        assert FREQUENCY_OPTIONS["twice_weekly"]["lookback"] == "4d"
        assert FREQUENCY_OPTIONS["weekly"]["lookback"]       == "7d"

    def test_twice_weekly_label(self):
        assert "Mon" in FREQUENCY_OPTIONS["twice_weekly"]["label"]
        assert "Thu" in FREQUENCY_OPTIONS["twice_weekly"]["label"]

    def test_weekday_map_monday(self):
        assert WEEKDAY_TO_INT["Monday"] == 1

    def test_weekday_map_thursday(self):
        assert WEEKDAY_TO_INT["Thursday"] == 4


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------

class TestParseTime:
    def test_standard(self):
        assert _parse_time("08:30") == (8, 30)

    def test_midnight(self):
        assert _parse_time("00:00") == (0, 0)

    def test_invalid_raises(self):
        with pytest.raises((ValueError, IndexError)):
            _parse_time("8am")


# ---------------------------------------------------------------------------
# build_plist
# ---------------------------------------------------------------------------

class TestBuildPlist:
    def _plist(self, freq, time="08:00", weekday=None):
        cfg = {"frequency": freq, "time": time}
        if weekday is not None:
            cfg["weekday"] = weekday
        return build_plist(cfg, "/venv/bin/scorerole", "/project")

    def test_label_present(self):
        assert LAUNCHD_LABEL in self._plist("daily")

    def test_binary_path_present(self):
        assert "/venv/bin/scorerole" in self._plist("daily")

    def test_working_dir_present(self):
        assert "/project" in self._plist("daily")

    def test_daily_lookback(self):
        assert "<string>1d</string>" in self._plist("daily")

    def test_daily_hour_minute(self):
        plist = self._plist("daily", time="09:15")
        assert "<integer>9</integer>" in plist
        assert "<integer>15</integer>" in plist

    def test_daily_uses_dict_not_array(self):
        plist = self._plist("daily")
        # StartCalendarInterval value for daily should be a single <dict>
        start = plist.find("<key>StartCalendarInterval</key>")
        snippet = plist[start:start + 200]
        assert "<dict>" in snippet
        assert "<array>" not in snippet

    def test_twice_weekly_lookback(self):
        assert "<string>4d</string>" in self._plist("twice_weekly")

    def test_twice_weekly_uses_array(self):
        plist = self._plist("twice_weekly")
        start = plist.find("<key>StartCalendarInterval</key>")
        snippet = plist[start:start + 400]
        assert "<array>" in snippet

    def test_twice_weekly_weekdays_1_and_4(self):
        plist = self._plist("twice_weekly")
        # Both Weekday 1 (Mon) and 4 (Thu) must appear
        assert plist.count("<key>Weekday</key>") == 2
        # Find both weekday integers
        weekday_ints = re.findall(r"<key>Weekday</key><integer>(\d+)</integer>", plist)
        assert set(weekday_ints) == {"1", "4"}

    def test_weekly_lookback(self):
        assert "<string>7d</string>" in self._plist("weekly")

    def test_weekly_default_weekday_monday(self):
        plist = self._plist("weekly")   # no weekday kwarg → default 1
        assert "<key>Weekday</key><integer>1</integer>" in plist

    def test_weekly_custom_weekday(self):
        plist = self._plist("weekly", weekday=3)  # Wednesday
        assert "<key>Weekday</key><integer>3</integer>" in plist

    def test_run_at_load_is_false(self):
        assert "<key>RunAtLoad</key><false/>" in self._plist("daily")

    def test_valid_xml_structure(self):
        plist = self._plist("daily")
        assert plist.startswith("<?xml")
        assert "<plist version=" in plist
        assert "</plist>" in plist

    def test_log_path_in_plist(self):
        plist = self._plist("daily")
        assert "scheduled.log" in plist


# ---------------------------------------------------------------------------
# build_crontab_line
# ---------------------------------------------------------------------------

class TestBuildCrontabLine:
    def _line(self, freq, time="08:00", weekday=None):
        cfg = {"frequency": freq, "time": time}
        if weekday is not None:
            cfg["weekday"] = weekday
        return build_crontab_line(cfg, "/venv/bin/scorerole", "/project")

    def test_daily_cron_fields(self):
        line = self._line("daily")
        # minute hour dom month dow command
        parts = line.split()
        assert parts[0] == "0"    # minute
        assert parts[1] == "8"    # hour
        assert parts[2] == "*"    # dom
        assert parts[3] == "*"    # month
        assert parts[4] == "*"    # dow=* for daily

    def test_twice_weekly_dow(self):
        line = self._line("twice_weekly")
        parts = line.split()
        assert parts[4] == "1,4"

    def test_weekly_dow_default_monday(self):
        line = self._line("weekly")
        parts = line.split()
        assert parts[4] == "1"

    def test_weekly_custom_weekday(self):
        line = self._line("weekly", weekday=5)  # Friday
        parts = line.split()
        assert parts[4] == "5"

    def test_lookback_daily(self):
        assert "--lookback 1d" in self._line("daily")

    def test_lookback_twice_weekly(self):
        assert "--lookback 4d" in self._line("twice_weekly")

    def test_lookback_weekly(self):
        assert "--lookback 7d" in self._line("weekly")

    def test_marker_present(self):
        from scorerole.schedule_cmd import CRONTAB_MARKER
        assert CRONTAB_MARKER in self._line("daily")

    def test_custom_time(self):
        line = self._line("daily", time="14:30")
        parts = line.split()
        assert parts[0] == "30"   # minute
        assert parts[1] == "14"   # hour


# ---------------------------------------------------------------------------
# schedule.json persistence
# ---------------------------------------------------------------------------

class TestSchedulePersistence:
    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")
        assert load_schedule() is None

    def test_load_returns_config_when_present(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps({"frequency": "daily", "time": "09:00"}))
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", sf)
        result = load_schedule()
        assert result["frequency"] == "daily"
        assert result["time"] == "09:00"

    def test_load_returns_none_on_corrupt_json(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text("not json {{")
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", sf)
        assert load_schedule() is None

    def test_load_returns_none_when_not_dict(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text("[1, 2, 3]")
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", sf)
        assert load_schedule() is None


# ---------------------------------------------------------------------------
# remove_schedule
# ---------------------------------------------------------------------------

class TestRemoveSchedule:
    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_remove_clears_plist_and_json(self, tmp_path, monkeypatch):
        plist_path   = tmp_path / "com.scorerole.digest.plist"
        schedule_path = tmp_path / "schedule.json"

        plist_path.write_text("<plist/>")
        schedule_path.write_text("{}")

        monkeypatch.setattr("scorerole.schedule_cmd.LAUNCHD_PLIST",  plist_path)
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", schedule_path)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            removed = remove_schedule()

        assert removed is True
        assert not plist_path.exists()
        assert not schedule_path.exists()

    def test_remove_returns_false_when_nothing_to_remove(self, tmp_path, monkeypatch):
        monkeypatch.setattr("scorerole.schedule_cmd.LAUNCHD_PLIST",  tmp_path / "nonexistent.plist")
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", tmp_path / "nonexistent.json")
        with mock.patch("subprocess.run"):
            removed = remove_schedule()
        assert removed is False


# ---------------------------------------------------------------------------
# show_schedule
# ---------------------------------------------------------------------------

class TestShowSchedule:
    def test_no_schedule_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")
        show_schedule()
        out = capsys.readouterr().out
        assert "No schedule configured" in out

    def test_shows_frequency_and_time(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config("twice_weekly", "07:30")))
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("scorerole.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        show_schedule()
        out = capsys.readouterr().out
        assert "Twice a week" in out
        assert "07:30" in out

    def test_warns_when_binary_missing(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps({**_fake_config(), "scorerole_bin": "/nonexistent/scorerole"}))
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("scorerole.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        show_schedule()
        out = capsys.readouterr().out
        assert "Binary not found" in out or "venv" in out.lower()

    def test_shows_active_when_plist_present(self, tmp_path, monkeypatch, capsys):
        plist_path = tmp_path / "plist"
        plist_path.write_text("<plist/>")
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config()))
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("scorerole.schedule_cmd.LAUNCHD_PLIST", plist_path)
        show_schedule()
        out = capsys.readouterr().out
        assert "active" in out


# ---------------------------------------------------------------------------
# Regression: run_pipeline unaffected by schedule state
# ---------------------------------------------------------------------------

class TestPipelineScheduleRegression:
    """run_pipeline must behave identically whether or not schedule.json exists."""

    def _run_with_no_emails(self, tmp_path, monkeypatch):
        """Run pipeline in a state where fetch_alerts returns no threads."""
        monkeypatch.setattr("scorerole.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")

        from scorerole import pipeline
        monkeypatch.setattr(pipeline, "fetch_alerts", lambda since_dt: [])
        monkeypatch.setattr(pipeline, "load_seen_roles", lambda: set())
        monkeypatch.setattr(pipeline, "ANTHROPIC_API_KEY", "sk-fake")

        import datetime
        import anthropic
        with mock.patch.object(anthropic, "Anthropic"):
            pipeline.run_pipeline(datetime.datetime.now())

    def test_no_schedule_json(self, tmp_path, monkeypatch):
        self._run_with_no_emails(tmp_path, monkeypatch)

    def test_with_schedule_json_present(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config()))
        self._run_with_no_emails(tmp_path, monkeypatch)
