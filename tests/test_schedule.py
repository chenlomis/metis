"""Tests for metis/schedule_cmd.py.

No OS calls are made here — launchctl and crontab are mocked throughout.
Tests cover: plist/crontab generation, schedule.json persistence, status
display, frequency-to-lookback mapping, and regression checks confirming
run_pipeline() is unaffected by schedule state.
"""
import json, os, platform, re, subprocess, sys, tempfile
from pathlib import Path
from unittest import mock

import pytest

# Make metis importable from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from metis.schedule_cmd import (
    FREQUENCY_OPTIONS,
    LAUNCHD_LABEL,
    SCHEDULE_FILE,
    WEEKDAY_TO_INT,
    _parse_time,
    _schedule_label,
    build_crontab_line,
    build_plist,
    install_schedule,
    load_schedule,
    pause_schedule,
    remove_schedule,
    resume_schedule,
    show_schedule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_config(freq="daily", time="08:00", **extra) -> dict:
    cfg = {"frequency": freq, "time": time, "metis_bin": "/venv/bin/metis",
           "working_dir": "/project", "platform": "Darwin", **extra}
    return cfg


@pytest.fixture(autouse=True)
def _isolate_legacy_launchd_plist(tmp_path, monkeypatch):
    monkeypatch.setattr("metis.schedule_cmd.LEGACY_LAUNCHD_PLIST", tmp_path / "com.scorerole.digest.plist")


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
        # Base label is generic; actual days are reflected via _schedule_label()
        assert "Twice a week" in FREQUENCY_OPTIONS["twice_weekly"]["label"]

    def test_schedule_label_twice_weekly_default_days(self):
        cfg = {"frequency": "twice_weekly"}
        label = _schedule_label(cfg)
        assert "Monday" in label
        assert "Thursday" in label

    def test_schedule_label_twice_weekly_custom_days(self):
        cfg = {"frequency": "twice_weekly", "weekdays": [2, 5]}   # Tue, Fri
        label = _schedule_label(cfg)
        assert "Tuesday" in label
        assert "Friday" in label

    def test_schedule_label_weekly(self):
        cfg = {"frequency": "weekly", "weekday": 3}   # Wednesday
        assert "Wednesday" in _schedule_label(cfg)

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
        return build_plist(cfg, "/venv/bin/metis", "/project")

    def test_label_present(self):
        assert LAUNCHD_LABEL in self._plist("daily")

    def test_binary_path_present(self):
        assert "/venv/bin/metis" in self._plist("daily")

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

    def test_state_env_pinned_in_plist(self):
        cfg = {
            "frequency": "daily",
            "time": "08:00",
            "data_dir": "/tmp/metis-data",
            "profile_path": "/tmp/metis-data/profile.yaml",
        }
        plist = build_plist(cfg, "/venv/bin/metis", "/project")
        assert "<key>METIS_DATA_DIR</key><string>/tmp/metis-data</string>" in plist
        assert "<key>METIS_PROFILE</key><string>/tmp/metis-data/profile.yaml</string>" in plist


# ---------------------------------------------------------------------------
# build_crontab_line
# ---------------------------------------------------------------------------

class TestBuildCrontabLine:
    def _line(self, freq, time="08:00", weekday=None):
        cfg = {"frequency": freq, "time": time}
        if weekday is not None:
            cfg["weekday"] = weekday
        return build_crontab_line(cfg, "/venv/bin/metis", "/project")

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
        from metis.schedule_cmd import CRONTAB_MARKER
        assert CRONTAB_MARKER in self._line("daily")

    def test_custom_time(self):
        line = self._line("daily", time="14:30")
        parts = line.split()
        assert parts[0] == "30"   # minute
        assert parts[1] == "14"   # hour

    def test_state_env_pinned_in_crontab_command(self):
        cfg = {
            "frequency": "daily",
            "time": "08:00",
            "data_dir": "/tmp/metis-data",
            "profile_path": "/tmp/metis-data/profile.yaml",
        }
        line = build_crontab_line(cfg, "/venv/bin/metis", "/project")
        assert "METIS_DATA_DIR=/tmp/metis-data" in line
        assert "METIS_PROFILE=/tmp/metis-data/profile.yaml" in line
        assert "METIS_PROFILE=/tmp/metis-data/profile.yaml /venv/bin/metis schedule run" in line


# ---------------------------------------------------------------------------
# schedule.json persistence
# ---------------------------------------------------------------------------

class TestSchedulePersistence:
    def test_load_returns_none_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")
        assert load_schedule() is None

    def test_load_returns_config_when_present(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps({"frequency": "daily", "time": "09:00"}))
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        result = load_schedule()
        assert result["frequency"] == "daily"
        assert result["time"] == "09:00"

    def test_load_returns_none_on_corrupt_json(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text("not json {{")
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        assert load_schedule() is None

    def test_load_returns_none_when_not_dict(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text("[1, 2, 3]")
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        assert load_schedule() is None

    def test_install_persists_pinned_state_env(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.DATA_DIR", tmp_path)
        monkeypatch.setattr("metis.schedule_cmd._metis_bin", lambda: "/venv/bin/metis")
        monkeypatch.setattr("metis.schedule_cmd._find_project_root", lambda: "/project")
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setenv("METIS_PROFILE", str(tmp_path / "profile.yaml"))

        with mock.patch("metis.schedule_cmd._install_launchd"):
            install_schedule({"frequency": "daily", "time": "08:00"})

        saved = json.loads(sf.read_text())
        assert saved["data_dir"] == str(tmp_path)
        assert saved["profile_path"] == str(tmp_path / "profile.yaml")


# ---------------------------------------------------------------------------
# remove_schedule
# ---------------------------------------------------------------------------

class TestRemoveSchedule:
    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_remove_clears_plist_and_json(self, tmp_path, monkeypatch):
        plist_path   = tmp_path / "com.metis.digest.plist"
        schedule_path = tmp_path / "schedule.json"

        plist_path.write_text("<plist/>")
        schedule_path.write_text("{}")

        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST",  plist_path)
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", schedule_path)

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            removed = remove_schedule()

        assert removed is True
        assert not plist_path.exists()
        assert not schedule_path.exists()

    @pytest.mark.skipif(platform.system() != "Darwin", reason="macOS only")
    def test_remove_clears_legacy_scorerole_plist(self, tmp_path, monkeypatch):
        plist_path = tmp_path / "com.metis.digest.plist"
        legacy_plist_path = tmp_path / "com.scorerole.digest.plist"

        legacy_plist_path.write_text("<plist/>")

        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", plist_path)
        monkeypatch.setattr("metis.schedule_cmd.LEGACY_LAUNCHD_PLIST", legacy_plist_path)
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            removed = remove_schedule()

        assert removed is True
        assert not legacy_plist_path.exists()

    def test_remove_returns_false_when_nothing_to_remove(self, tmp_path, monkeypatch):
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST",  tmp_path / "nonexistent.plist")
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "nonexistent.json")
        with mock.patch("subprocess.run"):
            removed = remove_schedule()
        assert removed is False


# ---------------------------------------------------------------------------
# show_schedule
# ---------------------------------------------------------------------------

class TestShowSchedule:
    def test_no_schedule_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")
        show_schedule()
        out = capsys.readouterr().out
        assert "No schedule configured" in out

    def test_shows_frequency_and_time(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config("twice_weekly", "07:30")))
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        show_schedule()
        out = capsys.readouterr().out
        assert "Twice a week" in out
        assert "07:30" in out

    def test_warns_when_binary_missing(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps({**_fake_config(), "metis_bin": "/nonexistent/metis"}))
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        show_schedule()
        out = capsys.readouterr().out
        assert "Binary not found" in out or "venv" in out.lower()

    def test_shows_active_when_plist_present(self, tmp_path, monkeypatch, capsys):
        plist_path = tmp_path / "plist"
        plist_path.write_text("<plist/>")
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config()))
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", plist_path)
        show_schedule()
        out = capsys.readouterr().out
        assert "active" in out


# ---------------------------------------------------------------------------
# build_plist — scheduled entry point is `metis schedule run`
# ---------------------------------------------------------------------------

class TestBuildPlistScheduledEntryPoint:
    def test_plist_calls_schedule_run(self):
        plist = build_plist({"frequency": "daily", "time": "08:00"}, "/venv/bin/metis", "/project")
        assert "<string>schedule</string>" in plist
        assert "<string>run</string>" in plist

    def test_twice_weekly_custom_days(self):
        cfg = {"frequency": "twice_weekly", "time": "08:00", "weekdays": [2, 5]}  # Tue, Fri
        plist = build_plist(cfg, "/venv/bin/metis", "/project")
        weekday_ints = re.findall(r"<key>Weekday</key><integer>(\d+)</integer>", plist)
        assert set(weekday_ints) == {"2", "5"}

    def test_twice_weekly_defaults_to_mon_thu_when_no_weekdays(self):
        cfg = {"frequency": "twice_weekly", "time": "08:00"}
        plist = build_plist(cfg, "/venv/bin/metis", "/project")
        weekday_ints = re.findall(r"<key>Weekday</key><integer>(\d+)</integer>", plist)
        assert set(weekday_ints) == {"1", "4"}


# ---------------------------------------------------------------------------
# build_crontab_line — custom twice_weekly days
# ---------------------------------------------------------------------------

class TestBuildCrontabCustomDays:
    def test_twice_weekly_custom_days(self):
        cfg = {"frequency": "twice_weekly", "time": "08:00", "weekdays": [2, 5]}  # Tue, Fri
        line = build_crontab_line(cfg, "/venv/bin/metis", "/project")
        parts = line.split()
        assert parts[4] == "2,5"

    def test_crontab_calls_schedule_run(self):
        cfg = {"frequency": "daily", "time": "08:00"}
        line = build_crontab_line(cfg, "/venv/bin/metis", "/project")
        assert "schedule run" in line


# ---------------------------------------------------------------------------
# pause_schedule / resume_schedule
# ---------------------------------------------------------------------------

class TestPauseResume:
    def _write_schedule(self, tmp_path, enabled=True) -> Path:
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps({
            **_fake_config(),
            "enabled": enabled,
        }))
        return sf

    def test_pause_sets_enabled_false(self, tmp_path, monkeypatch):
        sf = self._write_schedule(tmp_path, enabled=True)
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            result = pause_schedule()
        assert result is True
        assert json.loads(sf.read_text())["enabled"] is False

    def test_pause_returns_false_when_already_paused(self, tmp_path, monkeypatch):
        sf = self._write_schedule(tmp_path, enabled=False)
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        result = pause_schedule()
        assert result is False

    def test_pause_returns_false_when_no_schedule(self, tmp_path, monkeypatch):
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "no.json")
        assert pause_schedule() is False

    def test_resume_sets_enabled_true(self, tmp_path, monkeypatch):
        sf = self._write_schedule(tmp_path, enabled=False)
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.MagicMock(returncode=0)
            result = resume_schedule()
        assert result is True
        assert json.loads(sf.read_text())["enabled"] is True

    def test_resume_returns_false_when_already_active(self, tmp_path, monkeypatch):
        sf = self._write_schedule(tmp_path, enabled=True)
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        result = resume_schedule()
        assert result is False

    def test_resume_returns_false_when_no_schedule(self, tmp_path, monkeypatch):
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "no.json")
        assert resume_schedule() is False


# ---------------------------------------------------------------------------
# show_schedule — paused state display
# ---------------------------------------------------------------------------

class TestShowSchedulePaused:
    def test_shows_paused_status(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps({**_fake_config(), "enabled": False}))
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        show_schedule()
        out = capsys.readouterr().out
        assert "Paused" in out
        assert "resume" in out.lower()

    def test_shows_runs_digest_and_track(self, tmp_path, monkeypatch, capsys):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config()))
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", sf)
        monkeypatch.setattr("metis.schedule_cmd.LAUNCHD_PLIST", tmp_path / "plist")
        show_schedule()
        out = capsys.readouterr().out
        assert "track" in out.lower()


# ---------------------------------------------------------------------------
# Regression: run_pipeline unaffected by schedule state
# ---------------------------------------------------------------------------

class TestPipelineScheduleRegression:
    """run_pipeline must behave identically whether or not schedule.json exists."""

    def _run_with_no_emails(self, tmp_path, monkeypatch):
        """Run pipeline in a state where fetch_alerts returns no threads."""
        monkeypatch.setattr("metis.schedule_cmd.SCHEDULE_FILE", tmp_path / "schedule.json")

        from metis import pipeline
        monkeypatch.setattr(pipeline, "fetch_alerts", lambda since_dt, **kwargs: [])
        monkeypatch.setattr(pipeline, "load_seen_roles", lambda: set())

        import datetime
        with mock.patch.object(pipeline, "create_llm_client", return_value=mock.MagicMock()):
            pipeline.run_pipeline(datetime.datetime.now())

    def test_no_schedule_json(self, tmp_path, monkeypatch):
        self._run_with_no_emails(tmp_path, monkeypatch)

    def test_with_schedule_json_present(self, tmp_path, monkeypatch):
        sf = tmp_path / "schedule.json"
        sf.write_text(json.dumps(_fake_config()))
        self._run_with_no_emails(tmp_path, monkeypatch)
