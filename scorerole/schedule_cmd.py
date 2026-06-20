"""scorerole schedule — install, inspect, pause, resume, and remove automated OS-level digests.

On macOS: writes a launchd plist to ~/Library/LaunchAgents/ and loads it via
launchctl so the job runs at the configured time without manual intervention.

On Linux: edits the user crontab (crontab -l / crontab -) to add a single
tagged line that is removed cleanly by `scorerole schedule remove`.

Config is stored in ~/.job_pipeline/schedule.json alongside profile.yaml and
seen_roles.json so the schedule is inspectable independently of the OS job.

Scheduled entry point: `scorerole schedule run --lookback {X}`
  Runs the digest pipeline then `scorerole track` in sequence.
  Called by launchd / cron — not intended for direct user invocation.
"""
from __future__ import annotations

import json, os, platform, re, subprocess, sys
import datetime
from pathlib import Path

from .state import DATA_DIR

SCHEDULE_FILE  = DATA_DIR / "schedule.json"
LAUNCHD_LABEL  = "com.scorerole.digest"
LAUNCHD_PLIST  = Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
CRONTAB_MARKER = "# scorerole-digest"

FREQUENCY_OPTIONS: dict[str, dict] = {
    "daily":        {"label": "Daily",         "lookback": "1d",  "cron_dow": "*"},
    "twice_weekly": {"label": "Twice a week",  "lookback": "4d",  "cron_dow": None},
    "weekly":       {"label": "Weekly",        "lookback": "7d",  "cron_dow": None},
}

WEEKDAY_NAMES  = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
WEEKDAY_TO_INT = {name: i for i, name in enumerate(WEEKDAY_NAMES)}

_DEFAULT_TWICE_WEEKLY_DAYS = [1, 4]   # Monday, Thursday


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def _scorerole_bin() -> str:
    """Return absolute path to the scorerole script in the current venv."""
    bin_dir   = Path(sys.executable).parent
    candidate = bin_dir / "scorerole"
    if candidate.exists():
        return str(candidate)
    import shutil
    found = shutil.which("scorerole")
    if found:
        return found
    raise RuntimeError(
        "Cannot find the scorerole binary. "
        "Make sure the venv is activated and `pip install -e .` has been run."
    )


def _find_project_root() -> str:
    """Best-effort discovery of the project root (where .env lives).

    Walks up from the venv Python binary. If that fails, falls back to the
    directory two levels above this file (works for both editable installs
    and source tree runs).
    """
    candidates = [
        Path(sys.executable).parent.parent.parent,
        Path(__file__).parent.parent,
    ]
    for p in candidates:
        if (p / ".env").exists() or (p / "pyproject.toml").exists():
            return str(p.resolve())
    return str(candidates[-1].resolve())


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _schedule_label(config: dict) -> str:
    """Human-readable schedule label reflecting actual configured days."""
    freq = config.get("frequency", "?")
    base = FREQUENCY_OPTIONS.get(freq, {}).get("label", freq)

    if freq == "twice_weekly":
        days = config.get("weekdays", _DEFAULT_TWICE_WEEKLY_DAYS)
        day_names = [WEEKDAY_NAMES[d] for d in sorted(days)]
        return f"{base} ({' & '.join(day_names)})"

    if freq == "weekly":
        weekday = config.get("weekday", 1)
        return f"{base} ({WEEKDAY_NAMES[weekday]})"

    return base


# ---------------------------------------------------------------------------
# Plist / crontab builders  (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------

def build_plist(config: dict, scorerole_bin: str, working_dir: str) -> str:
    """Return the launchd plist XML string for the given schedule config.

    The scheduled entry point is `scorerole schedule run --lookback {X}` which
    chains the digest pipeline then `scorerole track` in one invocation.
    """
    freq         = config["frequency"]
    hour, minute = _parse_time(config["time"])
    lookback     = FREQUENCY_OPTIONS[freq]["lookback"]
    log_path     = str(DATA_DIR / "logs" / "scheduled.log")
    bin_dir      = str(Path(scorerole_bin).parent)
    home         = str(Path.home())

    if freq == "daily":
        interval_xml = (
            "    <dict>\n"
            f"        <key>Hour</key><integer>{hour}</integer>\n"
            f"        <key>Minute</key><integer>{minute}</integer>\n"
            "    </dict>"
        )
    elif freq == "twice_weekly":
        days = config.get("weekdays", _DEFAULT_TWICE_WEEKLY_DAYS)
        day_dicts = "\n".join(
            "        <dict>\n"
            f"            <key>Weekday</key><integer>{d}</integer>\n"
            f"            <key>Hour</key><integer>{hour}</integer>\n"
            f"            <key>Minute</key><integer>{minute}</integer>\n"
            "        </dict>"
            for d in sorted(days)
        )
        interval_xml = f"    <array>\n{day_dicts}\n    </array>"
    else:  # weekly
        weekday = config.get("weekday", 1)
        interval_xml = (
            "    <dict>\n"
            f"        <key>Weekday</key><integer>{weekday}</integer>\n"
            f"        <key>Hour</key><integer>{hour}</integer>\n"
            f"        <key>Minute</key><integer>{minute}</integer>\n"
            "    </dict>"
        )

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{scorerole_bin}</string>
        <string>schedule</string>
        <string>run</string>
        <string>--lookback</string>
        <string>{lookback}</string>
    </array>
    <key>WorkingDirectory</key><string>{working_dir}</string>
    <key>StartCalendarInterval</key>
{interval_xml}
    <key>RunAtLoad</key><false/>
    <key>StandardOutPath</key><string>{log_path}</string>
    <key>StandardErrorPath</key><string>{log_path}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key><string>{home}</string>
        <key>PATH</key><string>{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key><false/>
    </dict>
    <key>ThrottleInterval</key><integer>900</integer>
</dict>
</plist>
"""


def build_crontab_line(config: dict, scorerole_bin: str, working_dir: str) -> str:
    """Return the crontab line for the given schedule config.

    Calls `scorerole schedule run --lookback {X}` which chains digest + track.
    """
    freq     = config["frequency"]
    hour, minute = _parse_time(config["time"])
    lookback = FREQUENCY_OPTIONS[freq]["lookback"]
    log_path = str(DATA_DIR / "logs" / "scheduled.log")

    if freq == "daily":
        dow = "*"
    elif freq == "twice_weekly":
        days = config.get("weekdays", _DEFAULT_TWICE_WEEKLY_DAYS)
        dow = ",".join(str(d) for d in sorted(days))
    else:  # weekly
        dow = str(config.get("weekday", 1))

    cmd = (
        f"cd {working_dir} && {scorerole_bin} schedule run --lookback {lookback} "
        f">> {log_path} 2>&1"
    )
    return f"{minute} {hour} * * {dow} {cmd}  {CRONTAB_MARKER}"


# ---------------------------------------------------------------------------
# Schedule persistence
# ---------------------------------------------------------------------------

def load_schedule() -> dict | None:
    """Return saved schedule config, or None if no schedule is configured."""
    if not SCHEDULE_FILE.exists():
        return None
    try:
        data = json.loads(SCHEDULE_FILE.read_text())
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _save_schedule(config: dict) -> None:
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    SCHEDULE_FILE.write_text(json.dumps(config, indent=2))
    SCHEDULE_FILE.chmod(0o600)


# ---------------------------------------------------------------------------
# Install / remove
# ---------------------------------------------------------------------------

def install_schedule(config: dict) -> None:
    """Install the OS-level scheduled job and persist config to schedule.json."""
    scorerole_bin = _scorerole_bin()
    working_dir   = _find_project_root()

    full_config = {
        **config,
        "enabled":       True,
        "scorerole_bin": scorerole_bin,
        "working_dir":   working_dir,
        "installed_at":  datetime.datetime.now().isoformat(),
        "platform":      platform.system(),
    }

    if platform.system() == "Darwin":
        _install_launchd(full_config, scorerole_bin, working_dir)
    elif platform.system() == "Linux":
        _install_crontab(full_config, scorerole_bin, working_dir)
    else:
        raise SystemExit(
            "❌  Automated scheduling is supported on macOS and Linux only.\n"
            "    On Windows, use Task Scheduler to run `scorerole schedule run --lookback 1d`."
        )

    _save_schedule(full_config)


def _install_launchd(config: dict, scorerole_bin: str, working_dir: str) -> None:
    plist_content = build_plist(config, scorerole_bin, working_dir)
    uid = os.getuid()

    if LAUNCHD_PLIST.exists():
        subprocess.run(
            ["launchctl", "bootout", f"gui/{uid}", str(LAUNCHD_PLIST)],
            capture_output=True,
        )

    LAUNCHD_PLIST.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHD_PLIST.write_text(plist_content)

    result = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{uid}", str(LAUNCHD_PLIST)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"launchctl bootstrap failed (exit {result.returncode}):\n{result.stderr.strip()}\n\n"
            f"Plist written to: {LAUNCHD_PLIST}\n"
            f"Try: launchctl bootstrap gui/{uid} {LAUNCHD_PLIST}"
        )


def _install_crontab(config: dict, scorerole_bin: str, working_dir: str) -> None:
    new_line = build_crontab_line(config, scorerole_bin, working_dir)

    current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    lines = current.stdout.splitlines() if current.returncode == 0 else []

    lines = [l for l in lines if CRONTAB_MARKER not in l]
    lines.append(new_line)

    new_crontab = "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"crontab update failed:\n{proc.stderr}")


def remove_schedule() -> bool:
    """Remove the OS-level job, plist, and schedule.json. Returns True if anything was removed."""
    removed = False
    sys_platform = platform.system()

    if sys_platform == "Darwin":
        uid = os.getuid()
        if LAUNCHD_PLIST.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(LAUNCHD_PLIST)],
                capture_output=True,
            )
            LAUNCHD_PLIST.unlink()
            removed = True
    elif sys_platform == "Linux":
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if current.returncode == 0 and CRONTAB_MARKER in current.stdout:
            lines = [l for l in current.stdout.splitlines() if CRONTAB_MARKER not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)
            removed = True

    if SCHEDULE_FILE.exists():
        SCHEDULE_FILE.unlink()
        removed = True

    return removed


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------

def pause_schedule() -> bool:
    """Unload the OS job without deleting config or plist. Returns True if paused."""
    config = load_schedule()
    if not config:
        return False
    if not config.get("enabled", True):
        return False   # already paused

    sys_platform = config.get("platform", platform.system())
    if sys_platform == "Darwin":
        uid = os.getuid()
        if LAUNCHD_PLIST.exists():
            subprocess.run(
                ["launchctl", "bootout", f"gui/{uid}", str(LAUNCHD_PLIST)],
                capture_output=True,
            )
    elif sys_platform == "Linux":
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if current.returncode == 0 and CRONTAB_MARKER in current.stdout:
            lines = [l for l in current.stdout.splitlines() if CRONTAB_MARKER not in l]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True)

    config["enabled"] = False
    _save_schedule(config)
    return True


def resume_schedule() -> bool:
    """Re-load the OS job from existing config. Returns True if resumed."""
    config = load_schedule()
    if not config:
        return False
    if config.get("enabled", True):
        return False   # already active

    scorerole_bin = config.get("scorerole_bin") or _scorerole_bin()
    working_dir   = config.get("working_dir") or _find_project_root()

    sys_platform = config.get("platform", platform.system())
    if sys_platform == "Darwin":
        _install_launchd(config, scorerole_bin, working_dir)
    elif sys_platform == "Linux":
        _install_crontab(config, scorerole_bin, working_dir)

    config["enabled"] = True
    _save_schedule(config)
    return True


# ---------------------------------------------------------------------------
# Status display
# ---------------------------------------------------------------------------

def show_schedule() -> None:
    """Print the current schedule status to stdout."""
    config = load_schedule()
    if not config:
        print("  No schedule configured.")
        print("  Run `scorerole schedule set` to set one up.")
        return

    enabled   = config.get("enabled", True)
    status    = "Active" if enabled else "Paused"
    label     = _schedule_label(config)
    time_s    = config.get("time", "?")
    installed = config.get("installed_at", "?")[:10]
    bin_path  = config.get("scorerole_bin", "?")

    print(f"  Status:     {'✓' if enabled else '⏸'}  {status}")
    print(f"  Schedule:   {label} at {time_s}")
    print(f"  Runs:       digest (score + email) → track (xlsx update)")
    print(f"  Binary:     {bin_path}")
    print(f"  Installed:  {installed}")

    if bin_path != "?" and not Path(bin_path).exists():
        print(f"\n  ⚠  Binary not found at {bin_path}")
        print("     If the venv was recreated, run `scorerole schedule set` to reinstall.")

    sys_platform = config.get("platform", platform.system())
    if sys_platform == "Darwin":
        plist_ok = LAUNCHD_PLIST.exists()
        if enabled:
            os_status = "✓ launchd plist active" if plist_ok else "⚠  plist missing — run `scorerole schedule set` to reinstall"
        else:
            os_status = "⏸ paused (plist present)" if plist_ok else "⏸ paused (plist missing — run `scorerole schedule resume`)"
        print(f"  OS job:     {os_status}")
    elif sys_platform == "Linux":
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        cron_ok = current.returncode == 0 and CRONTAB_MARKER in current.stdout
        if enabled:
            os_status = "✓ crontab entry active" if cron_ok else "⚠  crontab entry missing — run `scorerole schedule set` to reinstall"
        else:
            os_status = "⏸ paused"
        print(f"  OS job:     {os_status}")

    if not enabled:
        print("\n  Run `scorerole schedule resume` to re-enable.")


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def run_schedule_wizard() -> None:
    """Interactive prompt to install or update the schedule."""
    try:
        import questionary
        from questionary import Style as QStyle
    except ImportError:
        sys.exit("❌  questionary not installed. Run: pip install questionary")

    from .theme import THEME
    Q_STYLE = QStyle([
        ("qmark",       f"fg:{THEME['accent']} bold"),
        ("question",    f"fg:{THEME['label']} bold"),
        ("answer",      f"fg:{THEME['accent']} bold"),
        ("pointer",     f"fg:{THEME['accent']} bold"),
        ("highlighted", f"fg:{THEME['label']} bold"),
        ("selected",    f"fg:{THEME['success']}"),
        ("instruction", f"fg:{THEME['dim']}"),
        ("separator",   f"fg:{THEME['dim']}"),
        ("text",        f"fg:{THEME['muted']}"),
        ("disabled",    f"fg:{THEME['dim']}"),
    ])

    existing = load_schedule()
    if existing:
        label = _schedule_label(existing)
        print(f"\n  Current schedule: {label} at {existing.get('time', '?')}")
        _raw = questionary.text(
            "  Replace the existing schedule?",
            instruction="(y/yes to replace, n/no to keep)",
            validate=lambda v: True if v.strip().lower() in ("y", "yes", "n", "no") else "Type y or n",
            style=Q_STYLE,
        ).ask()
        if _raw is None or _raw.strip().lower() not in ("y", "yes"):
            print("  Schedule unchanged.")
            return

    frequency = questionary.select(
        "  How often should scorerole run?",
        choices=[
            questionary.Choice("Daily",         value="daily"),
            questionary.Choice("Twice a week",  value="twice_weekly"),
            questionary.Choice("Weekly",        value="weekly"),
        ],
        style=Q_STYLE,
    ).ask()
    if frequency is None:
        return

    weekdays = None
    weekday  = None

    if frequency == "twice_weekly":
        all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        selected = questionary.checkbox(
            "  Which two days?",
            choices=[
                questionary.Choice(name, value=WEEKDAY_TO_INT[name],
                                   checked=(WEEKDAY_TO_INT[name] in _DEFAULT_TWICE_WEEKLY_DAYS))
                for name in all_days
            ],
            validate=lambda s: True if len(s) == 2 else "Please select exactly 2 days",
            style=Q_STYLE,
        ).ask()
        if selected is None:
            return
        weekdays = sorted(selected)

    elif frequency == "weekly":
        day_name = questionary.select(
            "  Which day of the week?",
            choices=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            style=Q_STYLE,
        ).ask()
        if day_name is None:
            return
        weekday = WEEKDAY_TO_INT[day_name]

    time_str = questionary.text(
        "  At what time? (24h, local time, e.g. 08:00)",
        default="08:00",
        style=Q_STYLE,
        validate=lambda t: (
            True if re.match(r"^\d{1,2}:\d{2}$", t) else "Use HH:MM format, e.g. 08:00"
        ),
    ).ask()
    if time_str is None:
        return

    config: dict = {"frequency": frequency, "time": time_str}
    if weekdays is not None:
        config["weekdays"] = weekdays
    if weekday is not None:
        config["weekday"] = weekday

    # Confirm before installing
    label = _schedule_label({**config, **FREQUENCY_OPTIONS.get(frequency, {})})
    print(f"\n  Schedule:  {label} at {time_str}")
    print("  Will run:  digest (score + email)  →  track (xlsx update)")
    _raw = questionary.text(
        "  Install?",
        instruction="(y/yes to confirm, n/no to cancel)",
        validate=lambda v: True if v.strip().lower() in ("y", "yes", "n", "no") else "Type y or n",
        style=Q_STYLE,
    ).ask()
    if _raw is None or _raw.strip().lower() not in ("y", "yes"):
        print("  Cancelled.")
        return

    try:
        install_schedule(config)
    except RuntimeError as e:
        print(f"\n  ❌  Install failed: {e}")
        return

    lookback = FREQUENCY_OPTIONS[frequency]["lookback"]
    print(f"\n  ✓  Schedule installed: {label} at {time_str}")
    print(f"     scorerole will fetch the last {lookback} of alerts, email the digest, and update your tracker.")
    if platform.system() == "Darwin":
        print(f"     OS job: {LAUNCHD_PLIST}")
    print(f"     Config: {SCHEDULE_FILE}")
    print()
    print("  To pause:   scorerole schedule pause")
    print("  To change:  scorerole schedule set")
    print("  To remove:  scorerole schedule remove")
    print()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(time_str: str) -> tuple[int, int]:
    """Parse 'HH:MM' → (hour, minute). Raises ValueError on bad input."""
    parts = time_str.split(":")
    if len(parts) != 2:
        raise ValueError(f"Invalid time format: {time_str!r}. Expected HH:MM.")
    return int(parts[0]), int(parts[1])
