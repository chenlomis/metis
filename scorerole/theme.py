"""Visual theme for scorerole CLI.

Single source of truth for colors, questionary style, and print helpers.
Set SCOREROLE_THEME=light or SCOREROLE_THEME=dark to override detection.
"""
import os
import shutil
import subprocess
import sys

from rich.console import Console
from rich.markup import escape as _escape
from rich.padding import Padding
from rich.style import Style
from rich.text import Text


# ---------------------------------------------------------------------------
# Light / dark detection
# ---------------------------------------------------------------------------

def _detect_dark() -> bool:
    # Explicit override always wins
    override = os.environ.get("SCOREROLE_THEME", "").lower()
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
        "background":   "#0f0f0f",
        "bright":       "#f0f0ee",   # user typed input — highest priority (pure near-white)
        "label":        "#c8c8c5",   # question labels — clear but not competing with input
        "muted":        "#888885",   # secondary text, completed step summaries
        "dim":          "#555552",   # hint/instruction text — genuinely secondary
        "accent":       "#4a9edd",   # structural color (less saturated than before)
        "welcome_bg":   "#13131a",   # welcome panel background — warm dark
        "success":      "#6abf8e",   # ✓ confirmed steps only
        "warning":      "#d4934a",   # → warnings, cost notices
        "error":        "#c96060",   # ✗ errors, validation failures
        "rule":         "#333330",   # horizontal rule lines
        "eg":           "#4a8099",   # examples line — dim teal, distinct from instruction gray
        "cursor":       "#4a9edd",
    }
else:
    THEME = {
        "background":   "#ffffff",
        "bright":       "#0a0a0a",   # user typed input — near black on white bg
        "label":        "#2d2d2d",   # question labels
        "muted":        "#666663",   # secondary text
        "dim":          "#aaaaaa",   # hint/instruction text
        "accent":       "#1d5aad",   # structural color
        "welcome_bg":   "#f0f0f5",   # welcome panel background — cool light
        "success":      "#1a6b3a",
        "warning":      "#8a4a00",
        "error":        "#b02020",
        "rule":         "#d8d8d5",   # horizontal rule lines
        "eg":           "#4a7080",   # examples line — dim teal, distinct from instruction gray
        "cursor":       "#1d5aad",
    }

# ---------------------------------------------------------------------------
# Questionary style
# ---------------------------------------------------------------------------

try:
    from questionary import Style as QStyle

    QUESTIONARY_STYLE = QStyle([
        ("qmark",       f"fg:{THEME['accent']} bold"),
        ("question",    f"fg:{THEME['label']} bold"),
        ("answer",      "bold"),        # no color = terminal default = brightest
        ("pointer",     f"fg:{THEME['accent']} bold"),
        ("highlighted", f"fg:{THEME['label']} bold"),
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
        "question":          f"fg:{THEME['label']} bold",
        "input":             f"fg:{THEME['bright']}",
        "answer":            f"fg:{THEME['bright']}",
        "pointer":           f"fg:{THEME['accent']} bold",
        "highlighted":       f"fg:{THEME['label']} bold",
        "selected":          f"fg:{THEME['success']}",
        "separator":         f"fg:{THEME['dim']}",
        "instruction":       f"fg:{THEME['dim']} italic",
        "long_instruction":  f"fg:{THEME['dim']} italic",
        "validator":         f"fg:{THEME['error']}",
    })
except ImportError:
    INQUIRER_STYLE = None

# ---------------------------------------------------------------------------
# Shared console instance — 80-col cap keeps all output predictable.
# Panel and prose both wrap natively within this budget.
# ---------------------------------------------------------------------------

console = Console()


# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_hint(text: str) -> None:
    """Dim hint text — 2-space indent; markup string ensures dynamic word-wrap on resize."""
    console.print(Padding(f"[{THEME['dim']} italic]{_escape(text)}[/]", (0, 0, 0, 2)))


def print_section(step: str, label: str, description: str = "") -> None:
    """Section header — single line, clips gracefully on narrow terminals."""
    line = Text()
    line.append(step + "  ", style=Style(color=THEME["accent"], bold=True))
    line.append(label, style=Style(color=THEME["label"], bold=True))
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
    console.rule(style=Style(color=THEME["rule"]))


def print_kb_hint() -> None:
    """Keyboard shortcut bar — rendered before select/checkbox prompt groups."""
    console.print(
        "  ↑↓ navigate  ·  space select  ·  enter confirm  ·  ctrl+c save & exit",
        style=Style(color=THEME["dim"]),
    )


def print_section_intro(body: str, ctrl_hint: bool = False) -> None:
    """Section intro paragraph — Rich word-wraps at console.width (80 cols)."""
    console.print(body, style=Style(color=THEME["muted"]))
    if ctrl_hint:
        console.print(
            "Press Enter to skip optional questions.  Ctrl+C exits safely.",
            style=Style(color=THEME["dim"], italic=True),
        )


def print_eg(text: str) -> None:
    """Examples line — dim teal, 2-space indent; markup string ensures dynamic word-wrap on resize."""
    console.print(Padding(f"[{THEME['eg']} italic]Examples: {_escape(text)}[/]", (0, 0, 0, 2)))
