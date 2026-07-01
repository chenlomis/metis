"""metis init — conversational profile setup wizard.

Conversational alternative to `metis init`. Two freeform prompts replace
the structured Step 2/3 form. The configured LLM extracts the profile, then asks at most
2–3 targeted clarifications for genuinely ambiguous fields before review.

Flow:
  0. Existing profile detection (Quick edits / Open in editor / Start fresh / Exit)
  1. Resume + LinkedIn  (reuses init_cmd parsing)
  2. What you're looking for  (single-line, Enter to submit)
  3. What you'd pass on       (single-line, Enter to skip)
  4. Clarifications           (0–2 select prompts, 3 absolute max)
  5. Review + save            (reuses init_cmd _show_profile + edit menu)
  6. Proactive sources + schedule (reuses init_cmd wizards)

Writes to the same ~/.job_pipeline/profile.yaml as `metis init`.
"""
import os, re, sys, shutil, logging
from pathlib import Path

from .llm import complete_text, create_llm_client, normalize_provider, resolve_stage_models
from .theme import THEME, INQUIRER_STYLE, console, print_section, print_section_intro, print_eg, print_hint, print_confirmed, print_separator


# ---------------------------------------------------------------------------
# Terminal-safe line reader
#
# InquirerPy (via prompt_toolkit) puts the terminal in raw/cbreak mode for
# select/confirm prompts and may not fully restore cooked mode afterward.
# Calling input() in raw mode means Enter sends \r (echoed as ^M) instead of
# terminating the line. This helper temporarily re-enables ICANON + ICRNL so
# the user sees normal cooked-mode line input.
# ---------------------------------------------------------------------------

def _read_line() -> str:
    """Read one line of text from stdin in cooked (canonical) mode.

    Saves current termios settings, enables ICANON + ECHO + ICRNL, reads a
    line, then restores the original settings so InquirerPy's next prompt
    finds the terminal in the state it left it.
    """
    try:
        import termios
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)   # returns a 7-element list
        new = list(old)               # shallow copy — integer elements are immutable
        # iflag: translate CR → NL so Enter terminates readline
        new[0] = new[0] | termios.ICRNL
        # lflag: canonical mode + echo
        new[3] = new[3] | termios.ICANON | termios.ECHO | termios.ECHOE | termios.ECHOK
        termios.tcsetattr(fd, termios.TCSADRAIN, new)
        try:
            line = sys.stdin.readline()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        return line.rstrip('\r\n')
    except Exception:
        # Fallback for non-POSIX or environments without termios
        try:
            return input()
        except (EOFError, KeyboardInterrupt):
            return ""

log = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

from .state import DATA_DIR
PROFILE_PATH = Path(os.environ["METIS_PROFILE"]) if "METIS_PROFILE" in os.environ else DATA_DIR / "profile.yaml"

from .prompts import init_extract_system_prompt


# ---------------------------------------------------------------------------
# Clarification question specs — kind → UI options
# ---------------------------------------------------------------------------

_CLARIFICATION_KINDS = {
    "salary_floor_or_target": {
        "label": "Salary type",
        "question_template": "Is this a strict base salary floor or an aspirational target?",
        "choices": [
            ("Hard floor", "floor", "[Filter out roles below {from_text}]"),
            ("Aspirational target", "target", "[Use for ranking, don't exclude]"),
            ("Skip", "skip", ""),
        ],
    },
    "remote_only_or_preferred": {
        "label": "Location flexibility",
        "question_template": "Are you remote-only, or open to hybrid for an exceptional role?",
        "choices": [
            ("Remote only",      "remote",   "[No office requirements accepted]"),
            ("Remote preferred", "flexible", "[Hybrid OK for an exceptional role]"),
            ("Flexible",         "flexible", "[Any arrangement works]"),
            ("Skip",             "skip",     ""),
        ],
    },
    "deal_breakers_absent": {
        "label": "Deal-breakers",
        "question_template": "Anything that's an automatic no regardless of fit?",
        "choices": None,  # freeform — handled separately
    },
    "track_ic_or_management": {
        "label": "Career track",
        "question_template": "Your background has both IC and management signals. Which path should we favor?",
        "choices": [
            ("Senior IC",   "ic",         "[Staff / Principal / Distinguished]"),
            ("Management",  "management", "[Director / VP / Head of]"),
            ("Both",        "flexible",   "[Evaluate IC and management equally]"),
            ("Skip",        "skip",       ""),
        ],
    },
}

_CLARIFICATION_PRIORITY = [
    "salary_floor_or_target",
    "remote_only_or_preferred",
    "deal_breakers_absent",
    "track_ic_or_management",
]

MAX_CLARIFICATIONS_DEFAULT = 2
MAX_CLARIFICATIONS_ABSOLUTE = 3


# ---------------------------------------------------------------------------
# Welcome panel
# ---------------------------------------------------------------------------

def _print_welcome(console, THEME, rich_box):
    from rich.panel import Panel
    from rich.style import Style

    # Width is always relative to current terminal — panel can never overflow the edge.
    # expand=False + explicit width: content reflows inside the box; border never wraps.
    width  = max(50, console.width - 2)
    narrow = console.width < 70
    pad    = (1, 1) if narrow else (1, 2)

    console.print(Panel(
        "[bold]Let's build your metis profile![/bold]\n\n"
        "The more context you provide, the better metis\n"
        "can filter and score roles against your background.\n\n"
        f"  [{THEME['accent']} bold]1.[/]  [bold]Resume + LinkedIn[/bold]  [{THEME['dim']}]— who you are[/]\n"
        f"  [{THEME['accent']} bold]2.[/]  [bold]What you're looking for[/bold]  [{THEME['dim']}]— what you want[/]\n"
        f"  [{THEME['accent']} bold]3.[/]  [bold]What you'd pass on[/bold]  [{THEME['dim']}]— what to exclude[/]\n"
        f"  [{THEME['accent']} bold]4.[/]  [bold]Review + save[/bold]  [{THEME['dim']}]— confirm and calibrate[/]\n\n"
        f"[{THEME['dim']}]~5 mins · Enter to skip · "
        f"`metis init` to update anytime[/]",
        style=Style(bgcolor=THEME["accent_bg"]),
        border_style=Style(color=THEME["accent"]),
        box=rich_box.ROUNDED,
        padding=pad,
        width=width,
        expand=False,
    ))


# ---------------------------------------------------------------------------
# Existing profile detection
# ---------------------------------------------------------------------------

def _handle_existing_profile(console, INQUIRER_STYLE):
    """If a profile exists, ask what to do. Returns 'fresh', 'quick', or 'editor'."""
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice

    if not PROFILE_PATH.exists():
        return "fresh"

    console.print()
    console.print("[bold]Existing profile found. What do you want to do?[/bold]")
    console.print("  [dim italic]Select an option  ·  Ctrl+C to abort safely[/dim italic]")
    console.print()

    choice = inquirer.select(
        message="",
        qmark="",
        choices=[
            Choice("quick",  "Quick edit      [Edit specific sections of your profile]"),
            Choice("editor", "Open in editor  [Edit profile.yaml directly in your editor]"),
            Choice("fresh",  "Start fresh     [Delete profile and restart from scratch]"),
            Choice("exit",   "Exit"),
        ],
        style=INQUIRER_STYLE,
    ).execute()

    if choice == "exit" or choice is None:
        sys.exit(0)
    return choice


# ---------------------------------------------------------------------------
# Step 1 — Resume + LinkedIn
# ---------------------------------------------------------------------------

def _step_resume(console, THEME, INQUIRER_STYLE, print_section, print_section_intro, print_eg):
    from InquirerPy import inquirer
    from rich.style import Style

    console.print()
    print_section("Step 1 of 4", "Resume + LinkedIn", "— who you are")
    console.print()
    print_section_intro("Add your resume so metis can understand your experience, skills, and background.")
    console.print("  [dim italic]Tip — drag the file into this window to paste its path[/dim italic]", soft_wrap=True)
    print_eg("~/resume_2026.pdf")
    console.print()

    from .init_bak_cmd import _parse_file

    resume_path = None
    while not resume_path:
        raw = inquirer.filepath(
            message="  › ",
            style=INQUIRER_STYLE,
        ).execute() or ""
        raw = raw.strip().strip("\"'").replace("\\ ", " ")
        if not raw:
            sys.exit(0)
        p = Path(raw).expanduser().resolve()
        if not p.exists():
            console.print(f"  [bold]File not found:[/bold] {raw}", style=Style(color=THEME["error"]))
            continue
        if p.is_dir():
            console.print(f"  [bold]That's a folder:[/bold] {raw}", style=Style(color=THEME["error"]))
            continue
        resume_path = p

    with console.status("  [dim]Parsing resume…[/dim]"):
        resume_text = _parse_file(resume_path)

    console.print(f"  [bold]✓[/bold]  Resume loaded", style=Style(color=THEME["success"]))
    console.print()

    # LinkedIn URL (optional)
    print_section_intro("Paste your LinkedIn profile URL to add skills + endorsements.")
    print_eg("https://www.linkedin.com/in/your-name/")
    console.print()

    li_raw = inquirer.text(
        message="  › ",
        style=INQUIRER_STYLE,
    ).execute() or ""
    li_raw = li_raw.strip()

    linkedin_text = ""
    if li_raw and li_raw.startswith("http"):
        with console.status("  [dim]Fetching LinkedIn profile…[/dim]"):
            try:
                from .init_bak_cmd import _scrape_linkedin_url
                linkedin_text = _scrape_linkedin_url(li_raw, console)
                if linkedin_text:
                    console.print("  [bold]✓[/bold]  LinkedIn profile loaded", style=Style(color=THEME["success"]))
                else:
                    console.print("  [dim]LinkedIn fetch returned no content — continuing without it.[/dim]")
            except Exception as e:
                log.debug("LinkedIn fetch failed: %s", e)
                console.print("  [dim]LinkedIn fetch skipped — continuing without it.[/dim]")
    elif li_raw:
        console.print("  [dim]Doesn't look like a URL — skipping LinkedIn.[/dim]")

    return resume_text, linkedin_text


# ---------------------------------------------------------------------------
# Step 2 — What you're looking for (freeform textarea)
# ---------------------------------------------------------------------------

def _step_want(console, THEME, INQUIRER_STYLE, print_section, print_section_intro, print_eg):
    console.print()
    print_section("Step 2 of 4", "What you're looking for", "— what you want")
    console.print()
    print_section_intro("Imagine your ideal recruiter calls tomorrow. What role would you hope they're calling about?")
    print_hint(
        "Tip — describe useful details such as: role title, company type, domain,"
        " work style, location, and comp expectations."
    )
    print_eg('"Staff or Principal PM at an AI infrastructure or developer tools company. Prefer growth-stage, remote-first, small team. Excited by agentic AI or LLM infra."')
    console.print()
    console.print("  [dim italic][Enter] submit  ·  empty [Enter] to skip[/dim italic]")
    console.print()

    console.print(f"  [{THEME['accent']} bold]›[/] ", end="")
    console.file.flush()
    result = _read_line()
    return result.strip()


# ---------------------------------------------------------------------------
# Step 3 — What you'd pass on (freeform textarea, skippable)
# ---------------------------------------------------------------------------

def _step_dontwant(console, THEME, INQUIRER_STYLE, print_section, print_section_intro, print_eg):
    console.print()
    print_section("Step 3 of 4", "What you'd pass on", "— what to exclude")
    console.print()
    print_section_intro("What would make you decline a role, even if the title looks right?")
    print_hint(
        "Tip — think about: work location, company scale, scope,"
        " management expectations, comp, and any other hard negatives."
    )
    print_eg('"Pass on anything requiring relocation or regular onsite travel. Exclude heavy people management from day one."')
    console.print()
    console.print("  [dim italic][Enter] submit  ·  empty [Enter] to skip[/dim italic]")
    console.print()

    console.print(f"  [{THEME['accent']} bold]›[/] ", end="")
    console.file.flush()
    result = _read_line()
    return result.strip()


# ---------------------------------------------------------------------------
# LLM extraction (v2)
# ---------------------------------------------------------------------------

def _extract_with_llm_v2(api_key, resume_text, linkedin_text, want_text, dontwant_text, console):
    import yaml

    provider = normalize_provider(os.getenv("METIS_LLM_PROVIDER", os.getenv("LLM_PROVIDER", "anthropic")))
    model = resolve_stage_models(provider)["model"]
    client = create_llm_client(provider=provider, api_key=api_key)

    user_msg = "\n\n".join(filter(None, [
        f"RESUME:\n{resume_text[:12000]}",
        f"LINKEDIN:\n{linkedin_text[:4000]}" if linkedin_text else "LINKEDIN: (not provided)",
        f"WHAT_I_WANT:\n{want_text}" if want_text else "WHAT_I_WANT: (not provided)",
        f"WHAT_I_DONT_WANT:\n{dontwant_text}" if dontwant_text else "WHAT_I_DONT_WANT: (not provided)",
    ]))

    with console.status("  [dim]Extracting profile…[/dim]"):
        response = complete_text(
            client,
            model=model,
            max_tokens=4096,
            system=init_extract_system_prompt(),
            user=user_msg,
        )

    raw = response.text.strip()

    # Strip markdown fences if present
    if raw.startswith("```"):
        lines = raw.splitlines()
        close = next((i for i, l in enumerate(lines) if i > 0 and l.strip() == "```"), len(lines))
        raw = "\n".join(lines[1:close])

    try:
        data = yaml.safe_load(raw)
    except Exception as e:
        log.warning("YAML parse error: %s — attempting recovery", e)
        data = {}

    if not isinstance(data, dict):
        data = {}

    followups = data.pop("_followups", []) or []
    return data, followups


# ---------------------------------------------------------------------------
# Deterministic guardrails (post-extraction)
# ---------------------------------------------------------------------------

def _apply_guardrails(profile, followups, want_text, dontwant_skipped):
    """Add follow-up entries for fields Claude may have missed."""
    existing_kinds = {f.get("kind") for f in followups if isinstance(f, dict)}

    def _add(kind, from_text):
        if kind not in existing_kinds and kind in _CLARIFICATION_KINDS:
            spec = _CLARIFICATION_KINDS[kind]
            followups.append({
                "kind": kind,
                "field": kind,
                "question": spec["question_template"],
                "from_text": from_text,
            })
            existing_kinds.add(kind)

    want_lower = (want_text or "").lower()

    # Salary: amount extracted but no explicit floor signal in the ~80 chars around the mention.
    # Scoped to context window to avoid "ground floor" / "ground-floor startup" false negatives.
    if profile.get("salary_floor_usd"):
        sal_match = re.search(r"\$[\d,k]+\+?", want_text or "", re.IGNORECASE)
        if sal_match:
            ctx_start = max(0, sal_match.start() - 40)
            ctx_end   = min(len(want_text), sal_match.end() + 40)
            ctx       = want_text[ctx_start:ctx_end].lower()
            floor_signals = ["floor", "minimum", "at least", "no less than", "hard floor"]
            if not any(s in ctx for s in floor_signals):
                _add("salary_floor_or_target", sal_match.group(0))
        # If no salary mention in text but profile has a value, Claude inferred it — ask anyway
        elif not sal_match:
            _add("salary_floor_or_target", f"${profile['salary_floor_usd']:,}")

    # Remote ambiguity: word-boundary match to avoid "remote debugging", "remote access", etc.
    # Only fires when "remote" is used in a location/work-style context, not an explicit floor signal.
    if re.search(r'\bremote\b', want_lower):
        is_location_remote = bool(re.search(
            r'\bremote[\s\-]*(only|first|role|position|work|job|friendly)?\b', want_lower
        ))
        is_explicit = bool(re.search(
            r'\bremote\s+only\b|\bfully\s+remote\b|\bexclusively\s+remote\b|\bno[\s\-]+office\b',
            want_lower
        ))
        if is_location_remote and not is_explicit:
            _add("remote_only_or_preferred", "remote")

    # Deal-breakers: Step 3 was not skipped but none extracted
    if not dontwant_skipped and not profile.get("deal_breakers"):
        _add("deal_breakers_absent", "your constraints")

    # Track: mixed IC + management signals in target roles
    roles_lower = " ".join(profile.get("target", {}).get("roles", [])).lower()
    ic_signals  = any(w in roles_lower for w in ["staff", "principal", "distinguished", "senior"])
    mgmt_signals = any(w in roles_lower for w in ["head of", "director", "vp", "vice president", "manager"])
    if ic_signals and mgmt_signals:
        _add("track_ic_or_management", "mixed role signals")

    # Enforce priority order and cap
    ordered = sorted(
        followups,
        key=lambda f: _CLARIFICATION_PRIORITY.index(f.get("kind", ""))
        if f.get("kind") in _CLARIFICATION_PRIORITY else 99,
    )
    return ordered[:MAX_CLARIFICATIONS_ABSOLUTE]


# ---------------------------------------------------------------------------
# Step 4 — Clarifications
# ---------------------------------------------------------------------------

def _run_clarifications(followups, profile, console, THEME, INQUIRER_STYLE):
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice
    from rich.style import Style

    active = followups[:MAX_CLARIFICATIONS_ABSOLUTE]
    if not active:
        return

    console.print()
    console.print("[bold]A few clarifications[/bold]")
    console.print()
    from rich.style import Style as _S
    console.print(
        "metis needs to clear up a few ambiguities before calibrating.",
        style=_S(color=THEME["muted"]),
    )
    console.print(
        "  [Enter] pick · [Enter] empty to skip",
        style=_S(color=THEME["dim"], italic=True),
    )

    total = len(active)
    for i, fu in enumerate(active):
        kind      = fu.get("kind", "")
        from_text = fu.get("from_text", "")
        spec      = _CLARIFICATION_KINDS.get(kind)
        if not spec:
            continue

        console.print()
        console.rule(style=_S(color=THEME["separator"]))
        console.print()

        counter = f"[dim][{i+1} of {total}][/dim]"
        label   = f"[bold]{spec['label']}[/bold]"
        src     = f"[dim]— you wrote \"{from_text}\" in Step 2[/dim]" if from_text else ""
        console.print(f"{counter}  {label}  {src}")
        console.print(
            f"  {spec['question_template']}",
            style=_S(color=THEME["muted"], italic=True),
        )
        console.print()

        if spec["choices"] is None:
            # Freeform (deal_breakers_absent)
            answer = inquirer.text(
                message="  › ",
                style=INQUIRER_STYLE,
            ).execute() or ""
            answer = answer.strip()
            if answer:
                existing = profile.get("deal_breakers") or []
                new_dbs  = [d.strip() for d in answer.split(",") if d.strip()]
                profile["deal_breakers"] = existing + new_dbs
        else:
            # Multiple choice
            choices = []
            for display, value, hint in spec["choices"]:
                hint_str = hint.format(from_text=from_text) if from_text else hint
                label_str = f"{display}  {hint_str}".strip()
                choices.append(Choice(value, label_str))

            answer = inquirer.select(
                message="",
                qmark="",
                choices=choices,
                style=INQUIRER_STYLE,
            ).execute()

            if answer == "skip" or answer is None:
                continue

            _apply_clarification_answer(kind, answer, from_text, profile)


def _apply_clarification_answer(kind, answer, from_text, profile):
    if kind == "salary_floor_or_target":
        # salary_floor_usd stays in place regardless — only the discriminator changes.
        # Downstream scoring reads salary_is_hard_floor to decide filter vs. rank signal.
        profile["salary_is_hard_floor"] = (answer == "floor")

    elif kind == "remote_only_or_preferred":
        cand = profile.setdefault("candidate", {})
        cand["location_preference"] = answer
        cand["open_to_remote"] = answer in ("remote", "flexible")

    elif kind == "track_ic_or_management":
        profile.setdefault("aspirations", {})["track"] = answer


# ---------------------------------------------------------------------------
# Step 5 — Review + edit menu (reuses init_cmd _show_profile)
# ---------------------------------------------------------------------------

def _run_review(profile, console, THEME, INQUIRER_STYLE, api_key=None,
                resume_text="", linkedin_text=""):
    """Review loop — menu mirrors the three wizard entry points."""
    import os, shutil
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice
    from rich.style import Style as _RS
    from .init_bak_cmd import _show_profile, open_in_editor
    import yaml as _yaml

    # Determine editor label: prefer $VISUAL/$EDITOR name, then check installed GUI editors
    _editor_env = os.environ.get("VISUAL") or os.environ.get("EDITOR") or ""
    if _editor_env:
        _editor_label = os.path.basename(_editor_env)
    elif shutil.which("code"):
        _editor_label = "VS Code"
    elif shutil.which("cursor"):
        _editor_label = "Cursor"
    elif shutil.which("zed"):
        _editor_label = "Zed"
    else:
        _editor_label = "your default editor"

    can_rerun = bool(api_key and resume_text)

    while True:
        console.clear()
        console.print()
        console.print(
            "[bold]Step 4 of 4[/bold]  [bold]Review and save[/bold]",
            style=_RS(color=THEME["accent"], bold=True),
        )
        console.print(
            f"  [{THEME['success']}]✓[/]  Profile extracted  "
            f"[{THEME['dim']}]·  edit anything before saving, then choose Save and continue[/]"
        )
        console.print()
        _show_profile(profile)
        console.print()

        choices = [
            Choice("want",    "Step 2 — What you're looking for"),
            Choice("dontwant","Step 3 — What you'd pass on"),
            Choice("editor",  f"Open profile in {_editor_label}"),
        ]
        if can_rerun:
            choices.append(Choice("rerun", "Re-run extraction  [re-enter Steps 2 + 3]"))
        choices.append(Choice("save", "Save profile  ✓"))

        action = inquirer.select(
            message="Anything to adjust before saving?",
            qmark="",
            choices=choices,
            default="save",
            style=INQUIRER_STYLE,
        ).execute()

        if action is None or action == "save":
            break

        console.print()

        if action == "want":
            new_want = inquirer.text(
                message="  What you're looking for: ",
                default=profile.get("notes", ""),
                style=INQUIRER_STYLE,
            ).execute() or ""
            new_want = new_want.strip()
            if new_want:
                # Update deal_breakers / salary from quick text edit
                profile["notes"] = new_want

        elif action == "dontwant":
            current_dbs = ", ".join(profile.get("deal_breakers") or [])
            new_dbs = inquirer.text(
                message="  What you'd pass on (comma-separated): ",
                default=current_dbs,
                style=INQUIRER_STYLE,
            ).execute() or ""
            profile["deal_breakers"] = [d.strip() for d in new_dbs.split(",") if d.strip()]

        elif action == "editor":
            console.print(
                "  [dim]Opening profile.yaml — save and close to continue.[/dim]"
            )
            DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            PROFILE_PATH.write_text(_yaml.dump(profile, allow_unicode=True, sort_keys=False))
            PROFILE_PATH.chmod(0o600)
            open_in_editor(PROFILE_PATH)
            try:
                updated = _yaml.safe_load(PROFILE_PATH.read_text())
                if isinstance(updated, dict):
                    profile.clear()
                    profile.update(updated)
            except Exception as e:
                console.print(f"  [{THEME['warning']}]⚠[/]  Could not re-read profile after edit: {e}")

        elif action == "rerun":
            if not api_key:
                console.print("  [dim]API key not available — cannot re-run extraction.[/dim]")
            else:
                new_want = _step_want(
                    console, THEME, INQUIRER_STYLE,
                    lambda s, l, d="": console.print(f"[bold]{l}[/bold]  [{THEME['dim']}]{d}[/]"),
                    lambda body, **_: console.print(body, style=_RS(color=THEME["muted"])),
                    lambda text: console.print(f"  [dim italic]{text}[/dim italic]"),
                )
                new_dontwant = _step_dontwant(
                    console, THEME, INQUIRER_STYLE,
                    lambda s, l, d="": console.print(f"[bold]{l}[/bold]  [{THEME['dim']}]{d}[/]"),
                    lambda body, **_: console.print(body, style=_RS(color=THEME["muted"])),
                    lambda text: console.print(f"  [dim italic]{text}[/dim italic]"),
                )
                console.print()
                new_profile, new_followups = _extract_with_llm_v2(
                    api_key, resume_text, linkedin_text, new_want, new_dontwant, console,
                )
                console.print(f"  [{THEME['success']}]✓[/]  Re-extraction complete")
                profile.clear()
                profile.update(new_profile)

        console.print()

    return profile


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_init(api_key):
    import yaml
    from rich import box as rich_box

    # Fail fast in non-interactive environments (CI, pipes, scripts)
    if not sys.stdin.isatty():
        console.print(
            "[bold]metis init[/bold] requires an interactive terminal.\n"
            f"  [dim]For non-interactive setup, edit [{THEME['accent']}]{PROFILE_PATH}[/] directly.[/dim]"
        )
        sys.exit(1)

    try:
        from InquirerPy import inquirer
    except ImportError:
        sys.exit("❌  InquirerPy not installed. Run: pip install InquirerPy")

    # ── Existing profile check ────────────────────────────────────────────────
    mode = _handle_existing_profile(console, INQUIRER_STYLE)

    if mode == "editor":
        from .init_bak_cmd import open_in_editor
        open_in_editor(PROFILE_PATH)
        return

    if mode == "quick":
        import yaml as _y
        try:
            profile = _y.safe_load(PROFILE_PATH.read_text()) or {}
        except Exception:
            profile = {}
        profile = _run_review(profile, console, THEME, INQUIRER_STYLE)
        DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))
        PROFILE_PATH.chmod(0o600)
        console.print(f"\n  [{THEME['success']}]✓[/]  Saved to [dim]{PROFILE_PATH}[/dim]\n")
        return

    # ── Full fresh wizard ─────────────────────────────────────────────────────
    console.print()
    _print_welcome(console, THEME, rich_box)

    # Step 1
    resume_text, linkedin_text = _step_resume(
        console, THEME, INQUIRER_STYLE,
        print_section, print_section_intro, print_eg,
    )
    print_separator()

    # Step 2
    want_text = _step_want(
        console, THEME, INQUIRER_STYLE,
        print_section, print_section_intro, print_eg,
    )
    _w = len(want_text.split()) if want_text else 0
    print_confirmed("What you're looking for", "captured" if want_text else "skipped", f"{_w} words")
    print_separator()

    # Step 3
    dontwant_text = _step_dontwant(
        console, THEME, INQUIRER_STYLE,
        print_section, print_section_intro, print_eg,
    )
    dontwant_skipped = not bool(dontwant_text)
    if dontwant_skipped:
        console.print("  [dim]Skipped — you can add constraints during review.[/dim]")
    else:
        _dw = len(dontwant_text.split())
        print_confirmed("What you'd pass on", "captured", f"{_dw} words")
    print_separator()

    # Extract
    console.print()
    profile, followups = _extract_with_llm_v2(
        api_key, resume_text, linkedin_text, want_text, dontwant_text, console,
    )
    console.print(f"  [{THEME['success']}]✓[/]  Extraction complete")

    # Guardrails — add any deterministic follow-ups Claude missed
    followups = _apply_guardrails(profile, followups, want_text, dontwant_skipped)

    # Step 4 — Clarifications
    _run_clarifications(followups, profile, console, THEME, INQUIRER_STYLE)

    # Step 5 — Review
    profile = _run_review(
        profile, console, THEME, INQUIRER_STYLE,
        api_key=api_key, resume_text=resume_text, linkedin_text=linkedin_text,
    )

    # ── Save ──────────────────────────────────────────────────────────────────
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))
    PROFILE_PATH.chmod(0o600)
    console.print(f"\n  [{THEME['success']}]✓[/]  Saved to [dim]{PROFILE_PATH}[/dim]\n")

    # ── Proactive sources ─────────────────────────────────────────────────────
    from .init_bak_cmd import _run_proactive_sources_wizard
    _run_proactive_sources_wizard(profile, Q_STYLE=INQUIRER_STYLE)

    # Re-save after proactive sources may have updated profile
    PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))
    PROFILE_PATH.chmod(0o600)

    # ── Scheduling ────────────────────────────────────────────────────────────
    from .schedule_cmd import load_schedule, run_schedule_wizard
    existing_schedule = load_schedule()
    if existing_schedule:
        from .schedule_cmd import FREQUENCY_OPTIONS
        freq  = existing_schedule.get("frequency", "?")
        label = FREQUENCY_OPTIONS.get(freq, {}).get("label", freq)
        console.print(
            f"\n  [dim]Automated schedule already active: {label} at {existing_schedule.get('time', '?')}[/dim]"
        )
        from InquirerPy import inquirer as _iq
        change = _iq.confirm(
            message="  › Update the schedule?",
            default=False,
            style=INQUIRER_STYLE,
        ).execute()
        if change:
            run_schedule_wizard()
    else:
        console.print()
        from InquirerPy import inquirer as _iq
        setup = _iq.confirm(
            message="  › Set up automated digests? metis can email you on a schedule.",
            default=True,
            style=INQUIRER_STYLE,
        ).execute()
        if setup:
            run_schedule_wizard()
        else:
            console.print("  [dim]You can set this up later with: metis schedule set[/dim]")

    # ── What next ─────────────────────────────────────────────────────────────
    from InquirerPy import inquirer as _iq
    from InquirerPy.base.control import Choice
    console.print()
    next_action = _iq.select(
        message="What next?",
        qmark="",
        choices=[
            Choice("profile", "Open profile in editor"),
            Choice("exit",    "Done"),
        ],
        style=INQUIRER_STYLE,
    ).execute()

    if next_action == "profile":
        from .init_bak_cmd import open_in_editor
        open_in_editor(PROFILE_PATH)

    console.print(
        "\n  [dim]Run `metis init` any time to update your profile.[/dim]\n"
    )
