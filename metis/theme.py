"""Visual theme for metis CLI.

Single source of truth for colors, questionary style, and print helpers.
Set METIS_THEME=light or METIS_THEME=dark to override detection.
"""
import os
import shutil
import subprocess
import sys
import textwrap

from rich.console import Console
from rich.markup import escape as _escape
from rich.style import Style
from rich.text import Text


# ---------------------------------------------------------------------------
# Light / dark detection
# ---------------------------------------------------------------------------

def _detect_dark() -> bool:
    # Explicit override always wins
    override = os.environ.get("METIS_THEME", "").lower()
    if override == "light":
        return False
    if override == "dark":
        return True

    # COLORFGBG: set by iTerm2 and some other terminals ("fg;bg"), dark bg index < 8
    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return bg < 8
        except ValueError:
            pass

    # macOS: read system-level dark mode preference.
    # The key is only present when Dark Mode is ON; missing = Light Mode.
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=1,
            )
            return result.stdout.strip().lower() == "dark"
        except Exception:
            pass

    # Safe fallback: light. Defaulting to dark caused black panels on
    # light terminals for users whose terminals don't export COLORFGBG.
    return False


IS_DARK = _detect_dark()

# ---------------------------------------------------------------------------
# Color tokens
# ---------------------------------------------------------------------------

if IS_DARK:
    THEME = {
        "accent":       "#5aadff",
        "accent_bg":    "#172033",
        "accent_txt":   "#bfdbfe",
        "accent_muted": "#7a9ac8",
        "bright":       "#e8e8e3",
        "muted":        "#a3a3a3",
        "dim":          "#444444",
        "success":      "#7dd3a8",
        "warning":      "#f0b060",
        "error":        "#f07070",
        "separator":    "#1f1f1f",
    }
else:
    THEME = {
        "accent":       "#2563eb",
        "accent_bg":    "#eff6ff",
        "accent_txt":   "#1e40af",
        "accent_muted": "#60a5fa",
        "bright":       "#171717",
        "muted":        "#6b7280",
        "dim":          "#c4c4c4",
        "success":      "#15803d",
        "warning":      "#b45309",
        "error":        "#dc2626",
        "separator":    "#f0f0f0",
    }

# ---------------------------------------------------------------------------
# Questionary style
# ---------------------------------------------------------------------------

try:
    from questionary import Style as QStyle

    QUESTIONARY_STYLE = QStyle([
        ("qmark",       f"fg:{THEME['accent']} bold"),
        ("question",    f"fg:{THEME['muted']} bold"),
        ("answer",      "bold"),        # no color = terminal default = brightest
        ("pointer",     f"fg:{THEME['accent']} bold"),
        ("highlighted", f"fg:{THEME['muted']} bold"),
        ("selected",    f"fg:{THEME['success']}"),
        ("separator",   f"fg:{THEME['dim']}"),
        ("instruction", f"fg:{THEME['dim']}"),
        ("text",        f"fg:{THEME['muted']}"),
        ("disabled",    f"fg:{THEME['dim']}"),
    ])
except ImportError:
    QUESTIONARY_STYLE = None

# ---------------------------------------------------------------------------
# InquirerPy style
# ---------------------------------------------------------------------------
try:
    from InquirerPy.utils import get_style as _iq_get_style

    INQUIRER_STYLE = _iq_get_style({
        "questionmark":      f"fg:{THEME['accent']} bold",
        "question":          f"fg:{THEME['muted']} bold",
        "input":             f"fg:{THEME['bright']}",
        "answer":            f"fg:{THEME['bright']}",
        "pointer":           f"fg:{THEME['accent']} bold",
        "highlighted":       f"fg:{THEME['muted']} bold",
        "selected":          f"fg:{THEME['success']}",
        "separator":         f"fg:{THEME['dim']}",
        "instruction":       f"fg:{THEME['dim']} italic",
        "long_instruction":  f"fg:{THEME['dim']} italic",
        "validator":         f"fg:{THEME['error']}",
    })
except ImportError:
    INQUIRER_STYLE = None

# ---------------------------------------------------------------------------
# Shared console instance — dynamically bounded to [80, 100] cols.
# Subclassing Console so the width property is re-evaluated on every render,
# not locked at startup — handles terminal resize gracefully.
# ---------------------------------------------------------------------------

class _BoundedConsole(Console):
    @property
    def width(self) -> int:
        return max(80, min(super().width, 100))

console = _BoundedConsole()


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_hint(text: str) -> None:
    """Tip text — label in accent, body in muted; 2-space indent on all wrapped lines."""
    prefix = "  "
    avail  = max(40, console.width - len(prefix))
    lines  = textwrap.wrap(text, width=avail) or [text]
    for i, line in enumerate(lines):
        if i == 0 and " — " in line:
            label, _, rest = line.partition(" — ")
            console.print(
                f"{prefix}[{THEME['accent']}]{_escape(label)} —[/] "
                f"[{THEME['muted']}]{_escape(rest)}[/]"
            )
        else:
            console.print(f"{prefix}[{THEME['muted']}]{_escape(line)}[/]")


def print_section(step: str, label: str, description: str = "") -> None:
    """Section header — single line, clips gracefully on narrow terminals."""
    line = Text()
    line.append(step + "  ", style=Style(color=THEME["accent"], bold=True))
    line.append(label, style=Style(color=THEME["muted"], bold=True))
    if description:
        line.append("  " + description, style=Style(color=THEME["dim"]))
    console.print(line, soft_wrap=True)


def print_confirmed(label: str, value: str, meta: str = "") -> None:
    """Flat single-line success cue:  ✓  Label: value  · meta"""
    t = Text()
    t.append("✓  ", style=Style(color=THEME["success"]))
    t.append(f"{label}: ", style=Style(color=THEME["muted"]))
    t.append(value, style=Style(color=THEME["bright"], bold=True))
    if meta:
        t.append(f"  · {meta}", style=Style(color=THEME["dim"]))
    console.print(t)


def print_separator() -> None:
    """Thin horizontal rule between major wizard stages."""
    console.rule(style=Style(color=THEME["separator"]))


def print_kb_hint() -> None:
    """Keyboard shortcut bar — rendered before select/checkbox prompt groups."""
    console.print(
        "  ↑↓ navigate  ·  space select  ·  enter confirm  ·  ctrl+c save & exit",
        style=Style(color=THEME["dim"]),
    )


def print_section_intro(body: str, ctrl_hint: bool = False) -> None:
    """Section intro paragraph — soft_wrap=True so terminal reflows on resize (no hard newlines)."""
    console.print(body, style=Style(color=THEME["muted"]), soft_wrap=True)
    if ctrl_hint:
        console.print(
            "Press Enter to skip optional questions.  Ctrl+C exits safely.",
            style=Style(color=THEME["dim"], italic=True),
            soft_wrap=True,
        )


def print_eg(text: str) -> None:
    """Examples line — accent_muted italic; visually distinct from print_hint's dim gray."""
    prefix = "  "
    avail  = max(40, console.width - len(prefix))
    for line in textwrap.wrap(f"Examples: {text}", width=avail) or [f"Examples: {text}"]:
        console.print(f"{prefix}[{THEME['accent_muted']} italic]{_escape(line)}[/]")
