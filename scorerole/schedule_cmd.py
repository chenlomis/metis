"""scorerole schedule — install, inspect, and remove automated OS-level digests.

On macOS: writes a launchd plist to ~/Library/LaunchAgents/ and loads it via
launchctl so the job runs at the configured time without manual intervention.

On Linux: edits the user crontab (crontab -l / crontab -) to add a single
tagged line that is removed cleanly by `scorerole schedule --remove`.

Config is stored in ~/.job_pipeline/schedule.json alongside profile.yaml and
seen_roles.json so the schedule is inspectable independently of the OS job.
"""
import json, os, platform, re, subprocess, sys
import datetime
from pathlib import Path

from .state import DATA_DIR

SCHEDULE_FILE  = DATA_DIR / "schedule.json"
LAUNCHD_LABEL  = "com.scorerole.digest"
LAUNCHD_PLIST  = Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
CRONTAB_MARKER = "# scorerole-digest"

FREQUENCY_OPTIONS: dict[str, dict] = {
    "daily":        {"label": "Daily",                      "lookback": "1d",  "cron_dow": "*",   "weekdays": [1,2,3,4,5,6,0]},
    "twice_weekly": {"label": "Twice a week (Mon & Thu)",   "lookback": "4d",  "cron_dow": "1,4", "weekdays": [1, 4]},
    "weekly":       {"label": "Weekly",                     "lookback": "7d",  "cron_dow": None,  "weekdays": None},
}

WEEKDAY_NAMES  = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
WEEKDAY_TO_INT = {name: i for i, name in enumerate(WEEKDAY_NAMES)}


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
    # venv/bin/python → venv/ → project/
    candidates = [
        Path(sys.executable).parent.parent.parent,
        Path(__file__).parent.parent,
    ]
    for p in candidates:
        if (p / ".env").exists() or (p / "pyproject.toml").exists():
            return str(p.resolve())
    return str(candidates[-1].resolve())


# ---------------------------------------------------------------------------
# Plist / crontab builders  (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------

def build_plist(config: dict, scorerole_bin: str, working_dir: str) -> str:
    """Return the launchd plist XML string for the given schedule config."""
    freq    = config["frequency"]
    hour, minute = _parse_time(config["time"])
    lookback    = FREQUENCY_OPTIONS[freq]["lookback"]
    log_path    = str(DATA_DIR / "logs" / "scheduled.log")
    bin_dir     = str(Path(scorerole_bin).parent)
    home        = str(Path.home())

    if freq == "daily":
        interval_xml = (
            "    <dict>\n"
            f"        <key>Hour</key><integer>{hour}</integer>\n"
            f"        <key>Minute</key><integer>{minute}</integer>\n"
            "    </dict>"
        )
    elif freq == "twice_weekly":
        interval_xml = (
            "    <array>\n"
            "        <dict>\n"
            "            <key>Weekday</key><integer>1</integer>\n"
            f"            <key>Hour</key><integer>{hour}</integer>\n"
            f"            <key>Minute</key><integer>{minute}</integer>\n"
            "        </dict>\n"
            "        <dict>\n"
            "            <key>Weekday</key><integer>4</integer>\n"
            f"            <key>Hour</key><integer>{hour}</integer>\n"
            f"            <key>Minute</key><integer>{minute}</integer>\n"
            "        </dict>\n"
            "    </array>"
        )
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
</dict>
</plist>
"""


def build_crontab_line(config: dict, scorerole_bin: str, working_dir: str) -> str:
    """Return the crontab line for the given schedule config."""
    freq     = config["frequency"]
    hour, minute = _parse_time(config["time"])
    lookback = FREQUENCY_OPTIONS[freq]["lookback"]
    log_path = str(DATA_DIR / "logs" / "scheduled.log")

    if freq in ("daily", "twice_weekly"):
        dow = FREQUENCY_OPTIONS[freq]["cron_dow"]
    else:  # weekly
        dow = str(config.get("weekday", 1))

    cmd = (
        f"cd {working_dir} && {scorerole_bin} --lookback {lookback} "
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
        "scorerole_bin":  scorerole_bin,
        "working_dir":    working_dir,
        "installed_at":   datetime.datetime.now().isoformat(),
        "platform":       platform.system(),
    }

    if platform.system() == "Darwin":
        _install_launchd(full_config, scorerole_bin, working_dir)
    elif platform.system() == "Linux":
        _install_crontab(full_config, scorerole_bin, working_dir)
    else:
        raise SystemExit(
            "❌  Automated scheduling is supported on macOS and Linux only.\n"
            "    On Windows, use Task Scheduler to run `scorerole --lookback 1d` on your preferred cadence."
        )

    _save_schedule(full_config)


def _install_launchd(config: dict, scorerole_bin: str, working_dir: str) -> None:
    plist_content = build_plist(config, scorerole_bin, working_dir)
    uid = os.getuid()

    # Unload existing job gracefully (ignore errors — may not be loaded yet)
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

    # Remove any existing scorerole line, then append the new one
    lines = [l for l in lines if CRONTAB_MARKER not in l]
    lines.append(new_line)

    new_crontab = "\n".join(lines) + "\n"
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"crontab update failed:\n{proc.stderr}")


def remove_schedule() -> bool:
    """Remove the OS-level job and schedule.json. Returns True if anything was removed."""
    removed = False
    sys_platform = platform.system()
    config = load_schedule()

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
# Status display
# ---------------------------------------------------------------------------

def show_schedule() -> None:
    """Print the current schedule status to stdout."""
    config = load_schedule()
    if not config:
        print("  No schedule configured.")
        print("  Run `scorerole schedule --set` to set one up.")
        return

    freq    = config.get("frequency", "?")
    label   = FREQUENCY_OPTIONS.get(freq, {}).get("label", freq)
    time_s  = config.get("time", "?")
    lookback = FREQUENCY_OPTIONS.get(freq, {}).get("lookback", "?")
    installed = config.get("installed_at", "?")[:10]
    bin_path  = config.get("scorerole_bin", "?")

    print(f"  Schedule:   {label} at {time_s}")
    print(f"  Lookback:   --lookback {lookback}  (roles since last run)")
    print(f"  Binary:     {bin_path}")
    print(f"  Installed:  {installed}")

    # Binary health check
    if bin_path != "?" and not Path(bin_path).exists():
        print(f"\n  ⚠  Binary not found at {bin_path}")
        print("     If the venv was recreated, run `scorerole schedule --set` to reinstall.")

    # OS job health check
    sys_platform = config.get("platform", platform.system())
    if sys_platform == "Darwin":
        plist_ok = LAUNCHD_PLIST.exists()
        status   = "✓ launchd plist active" if plist_ok else "⚠  plist missing — run `scorerole schedule --set` to reinstall"
        print(f"  OS job:     {status}")
    elif sys_platform == "Linux":
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        cron_ok = current.returncode == 0 and CRONTAB_MARKER in current.stdout
        status  = "✓ crontab entry active" if cron_ok else "⚠  crontab entry missing — run `scorerole schedule --set` to reinstall"
        print(f"  OS job:     {status}")


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

    Q_STYLE = QStyle([
        ("qmark",       "fg:#57a55a bold"),
        ("question",    "bold"),
        ("answer",      "fg:#57a55a bold"),
        ("pointer",     "fg:#57a55a bold"),
        ("highlighted", "fg:#57a55a bold"),
        ("selected",    "fg:#57a55a"),
        ("instruction", "fg:#6c6c6c"),
        ("separator",   "fg:#6c6c6c"),
    ])

    existing = load_schedule()
    if existing:
        freq  = existing.get("frequency", "?")
        label = FREQUENCY_OPTIONS.get(freq, {}).get("label", freq)
        print(f"\n  Current schedule: {label} at {existing.get('time', '?')}")
        replace = questionary.confirm(
            "  Replace the existing schedule?", default=True, style=Q_STYLE
        ).ask()
        if not replace:
            print("  Schedule unchanged.")
            return

    frequency = questionary.select(
        "  How often should scorerole run?",
        choices=[
            questionary.Choice("Daily",                    value="daily"),
            questionary.Choice("Twice a week (Mon & Thu)", value="twice_weekly"),
            questionary.Choice("Weekly",                   value="weekly"),
        ],
        style=Q_STYLE,
    ).ask()
    if frequency is None:
        return

    weekday = None
    if frequency == "weekly":
        day_name = questionary.select(
            "  Which day of the week?",
            choices=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            style=Q_STYLE,
        ).ask()
        if day_name is None:
            return
        weekday = WEEKDAY_TO_INT[day_name]

    time_str = questionary.text(
        "  At what time? (24 h, e.g. 08:00)",
        default="08:00",
        style=Q_STYLE,
        validate=lambda t: (
            True if re.match(r"^\d{1,2}:\d{2}$", t) else "Use HH:MM format, e.g. 08:00"
        ),
    ).ask()
    if time_str is None:
        return

    config: dict = {"frequency": frequency, "time": time_str}
    if weekday is not None:
        config["weekday"] = weekday

    try:
        install_schedule(config)
    except RuntimeError as e:
        print(f"\n  ❌  Install failed: {e}")
        return

    label = FREQUENCY_OPTIONS[frequency]["label"]
    lookback = FREQUENCY_OPTIONS[frequency]["lookback"]
    print(f"\n  ✓  Schedule installed: {label} at {time_str}")
    print(f"     scorerole will fetch the last {lookback} of alerts and email you the digest.")
    if platform.system() == "Darwin":
        print(f"     OS job: {LAUNCHD_PLIST}")
    print(f"     Config: {SCHEDULE_FILE}")
    print()
    print("  To change the schedule: scorerole schedule --set")
    print("  To remove the schedule: scorerole schedule --remove")
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
