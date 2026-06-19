"""Visual theme for scorerole CLI.

Single source of truth for colors, questionary style, and print helpers.
Set SCOREROLE_THEME=light or SCOREROLE_THEME=dark to override detection.
"""
import os

from rich.console import Console
from rich.style import Style
from rich.text import Text


# ---------------------------------------------------------------------------
# Light / dark detection
# ---------------------------------------------------------------------------

def _detect_dark() -> bool:
    override = os.environ.get("SCOREROLE_THEME", "").lower()
    if override == "light":
        return False
    if override == "dark":
        return True
    # COLORFGBG is set by most terminals: "fg;bg" — dark bg index < 8
    fgbg = os.environ.get("COLORFGBG", "")
    if fgbg:
        try:
            bg = int(fgbg.split(";")[-1])
            return bg < 8
        except ValueError:
            pass
    return True  # safe default


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
        "tip":          "#888885",   # tip text — slightly brighter than dim for WCAG AA (~5:1)
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
        "tip":          "#666663",   # tip text — WCAG AA on white (~4.6:1)
        "cursor":       "#1d5aad",
    }

# ---------------------------------------------------------------------------
# Questionary style
# ---------------------------------------------------------------------------
# Hierarchy (most → least prominent):
#   user typed input  → terminal default fg (no color override = always brightest)
#   question label    → THEME['label'] bold
#   option body text  → THEME['muted']
#   hint text         → THEME['dim'] italic
#   structural chrome → THEME['dim']

try:
    from questionary import Style as QStyle

    QUESTIONARY_STYLE = QStyle([
        ("qmark",       f"fg:{THEME['accent']} bold"),   # ◆ active prompt symbol
        ("question",    f"fg:{THEME['label']} bold"),     # prompt label — clear but below input
        ("answer",      "bold"),                          # completed value — no color, terminal default = brightest
        ("pointer",     f"fg:{THEME['accent']} bold"),    # ❯ selector arrow
        ("highlighted", f"fg:{THEME['label']} bold"),     # focused option in list
        ("selected",    f"fg:{THEME['success']}"),        # confirmed checkbox item
        ("separator",   f"fg:{THEME['dim']}"),            # menu dividers
        ("instruction", f"fg:{THEME['dim']}"),            # inline hint text
        ("text",        f"fg:{THEME['muted']}"),          # option body text
        ("disabled",    f"fg:{THEME['dim']}"),
    ])
except ImportError:
    QUESTIONARY_STYLE = None  # non-interactive env

# ---------------------------------------------------------------------------
# InquirerPy style — separate try block so it survives missing questionary
# ---------------------------------------------------------------------------
try:
    from InquirerPy.utils import get_style as _iq_get_style

    INQUIRER_STYLE = _iq_get_style({
        "questionmark":      f"{THEME['accent']} bold",
        "question":          f"{THEME['label']} bold",
        "input":             THEME['bright'],
        "answer":            THEME['bright'],
        "pointer":           f"{THEME['accent']} bold",
        "highlighted":       f"{THEME['label']} bold",
        "selected":          THEME['success'],
        "separator":         THEME['dim'],
        "instruction":       f"{THEME['dim']} italic",
        "long_instruction":  f"{THEME['dim']} italic",
        "validator":         THEME['error'],
    })
except ImportError:
    INQUIRER_STYLE = None

# ---------------------------------------------------------------------------
# Shared console instance
# ---------------------------------------------------------------------------

console = Console()

# ---------------------------------------------------------------------------
# Print helpers
# ---------------------------------------------------------------------------

def print_hint(text: str) -> None:
    """Dim hint text — printed as part of the same question block (no blank line before prompt)."""
    console.print(text, style=Style(color=THEME["dim"], italic=True))


def print_section(step: str, label: str, description: str = "") -> None:
    """Section header — plain styled text, no trailing rule.

    Trailing rules collapse on terminal resize (past output is static).
    Visual separation comes from the blank line callers inject before this.
    """
    line = Text()
    line.append(step + "  ", style=Style(color=THEME["accent"], bold=True))
    line.append(label, style=Style(color=THEME["label"], bold=True))
    if description:
        line.append("  " + description, style=Style(color=THEME["dim"]))
    console.print(line)


def print_confirmed(label: str, value: str, meta: str = "") -> None:
    """Flat single-line success cue.   ✓  Label: value  · meta"""
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
    """Section intro paragraph — muted prose + optional ctrl-hint line.

    Callers must print a blank line after this before the first question.
    """
    console.print(body, style=Style(color=THEME["muted"]))
    if ctrl_hint:
        console.print(
            "Press Enter to skip optional questions.  Ctrl+C exits safely.",
            style=Style(color=THEME["dim"], italic=True),
        )


def print_eg(text: str) -> None:
    """Examples line — dim teal, 2-space indent, italic. Sits below the instruction line."""
    console.print(f"  Examples: {text}", style=Style(color=THEME["eg"], italic=True))
