"""scorerole init2 — conversational profile setup wizard (beta).

Conversational alternative to `scorerole init`. Two freeform prompts replace
the structured Step 2/3 form. Claude extracts the profile, then asks at most
2–3 targeted clarifications for genuinely ambiguous fields before review.

Flow:
  0. Existing profile detection (Quick edits / Open in editor / Start fresh / Exit)
  1. Resume + LinkedIn  (reuses init_cmd parsing)
  2. What you're looking for  (single-line, Enter to submit)
  3. What you'd pass on       (single-line, Enter to skip)
  4. Clarifications           (0–2 select prompts, 3 absolute max)
  5. Review + save            (reuses init_cmd _show_profile + edit menu)
  6. Proactive sources + schedule (reuses init_cmd wizards)

Writes to the same ~/.job_pipeline/profile.yaml as `scorerole init`.
"""
import os, re, sys, shutil, logging
from pathlib import Path

log = logging.getLogger(__name__)

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

DATA_DIR     = Path.home() / ".job_pipeline"
PROFILE_PATH = DATA_DIR / "profile.yaml"


# ---------------------------------------------------------------------------
# Extraction system prompt (v2 — freeform input variant)
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM_V2 = """\
You are a career profile extractor.

You will receive:
  RESUME: the candidate's full resume text
  LINKEDIN: optional LinkedIn profile text (may be empty)
  WHAT_I_WANT: a freeform paragraph describing the candidate's ideal next role
  WHAT_I_DONT_WANT: a freeform paragraph describing roles/arrangements to avoid (may be empty)

Extract the candidate's information and return a SINGLE YAML document with two
top-level sections: the profile fields, and a `_followups` list.

Return ONLY valid YAML — no markdown fences, no commentary, no extra keys.

Profile schema (all keys required; use null or [] when absent):

candidate:
  name: string
  email: string or null
  location: "City, State"
  open_to_remote: bool
  open_to_relocation: []

target:
  roles: []
  level: string
  industries: []

aspirations:
  track: string                  # "ic", "management", or "flexible"
  direction: string
  company_types: []
  avoid_company_types: []

preferences:
  company_stage: []
  company_size: null
  industry_targets: []
  industry_avoid: []
  base_salary_target_usd: null

scoring:
  apply_threshold: 75
  consider_threshold: 55
  level_mismatch_deduction: 10

experience:
  - company: string
    title: string
    dates: string
    highlights: []

education:
  - institution: string or null
    degree: string
    year: int or null

strengths: []
green_flags: []
yellow_flags: []
red_flags: []
deal_breakers: []
salary_floor_usd: int or null
notes: string

inferred:
  customer_types: []
  degree_level: null

_followups: []

---

Rules for profile extraction:
- WHAT_I_WANT overrides anything inferred from the resume for: target.roles,
  aspirations.track, aspirations.direction, aspirations.company_types,
  preferences.*, salary_floor_usd, candidate.open_to_remote.
- WHAT_I_DONT_WANT populates deal_breakers and aspirations.avoid_company_types.
  Convert described avoidances into crisp deal-breaker strings.
- If salary mentioned: extract the number into salary_floor_usd. Whether it is
  a hard floor vs aspiration is determined by the _followups logic below.
- Return ONLY the YAML block. No markdown fences.

---

Rules for _followups:
After filling the profile, append a `_followups` list. Each entry must have:
  field: string          # the profile key this question clarifies
  kind: string           # controlled vocabulary — see below
  question: string       # one sentence, shown verbatim to the user
  from_text: string      # the exact phrase from input that triggered this, e.g. "$280k+"

Include an entry ONLY when ONE of these conditions is true:
  - kind: salary_floor_or_target
    Trigger: salary amount present in WHAT_I_WANT but no explicit "floor", "minimum",
    "at least", or "no less than" signal.
  - kind: remote_only_or_preferred
    Trigger: "remote" present in WHAT_I_WANT but "only", "exclusively", or "no office"
    are absent.
  - kind: deal_breakers_absent
    Trigger: WHAT_I_DONT_WANT was non-empty but all constraints parsed as soft
    preferences rather than clear hard rejections.
  - kind: track_ic_or_management
    Trigger: target.roles contains a mix of IC-level and management-level titles
    (e.g. "Staff PM" alongside "Head of Product").

Maximum 3 entries in _followups. Omit the list entirely (or set to []) if none apply.
"""


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
    width = min(88, shutil.get_terminal_size().columns)
    console.print(Panel(
        "[bold]Let's build your scorerole profile![/bold]\n\n"
        "The more context you provide, the better scorerole can filter and\n"
        "score roles against your background.\n\n"
        f"  [{THEME['accent']} bold]1.[/]  [bold]Resume + LinkedIn[/bold]  [{THEME['dim']}]— who you are[/]\n"
        f"  [{THEME['accent']} bold]2.[/]  [bold]What you're looking for[/bold]  [{THEME['dim']}]— what you want[/]\n"
        f"  [{THEME['accent']} bold]3.[/]  [bold]What you'd pass on[/bold]  [{THEME['dim']}]— what to exclude[/]\n"
        f"  [{THEME['accent']} bold]4.[/]  [bold]Review + save[/bold]  [{THEME['dim']}]— confirm and calibrate[/]\n\n"
        f"[{THEME['dim']}]Takes about 5 mins. Press Enter to skip optional questions."
        f"  Run `scorerole init2` anytime to update.[/]",
        style=Style(bgcolor=THEME["welcome_bg"]),
        border_style=Style(color=THEME["accent"]),
        box=rich_box.ROUNDED,
        padding=(1, 3),
        width=width,
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
            Choice("quick",  "Quick edits    [Jump straight to the profile review menu]"),
            Choice("editor", "Open in editor [Modify profile.yaml directly in your terminal]"),
            Choice("fresh",  "Start fresh    [Upload a new resume and trigger a full re-extraction]"),
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
    print_section_intro("Add your resume so scorerole can understand your experience, skills, and background.")
    console.print("  [dim italic]Tip — drag the file into this window to paste its path[/dim italic]")
    print_eg("~/resume_2026.pdf")
    console.print()

    from .init_cmd import _parse_file

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
                from .init_cmd import _scrape_linkedin_url
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
    from InquirerPy import inquirer
    from rich.style import Style

    console.print()
    print_section("Step 2 of 4", "What you're looking for", "— what you want")
    console.print()
    print_section_intro("Imagine your ideal recruiter calls tomorrow. What role would you hope they're calling about?")
    console.print(
        "  [dim italic]Tip — describe useful details such as: role title, company type, domain,"
        " work style, location, and comp expectations.[/dim italic]"
    )
    print_eg('"Staff or Principal PM at an AI infrastructure or developer tools company. Prefer growth-stage, remote-first, small team, $280k+ base. Excited by agentic AI or LLM infra."')
    console.print()
    console.print("  [dim italic][Enter] submit  ·  empty [Enter] to skip[/dim italic]")
    console.print()

    result = inquirer.text(
        message="  › ",
        style=INQUIRER_STYLE,
    ).execute() or ""
    return result.strip()


# ---------------------------------------------------------------------------
# Step 3 — What you'd pass on (freeform textarea, skippable)
# ---------------------------------------------------------------------------

def _step_dontwant(console, THEME, INQUIRER_STYLE, print_section, print_section_intro, print_eg):
    from InquirerPy import inquirer

    console.print()
    print_section("Step 3 of 4", "What you'd pass on", "— what to exclude")
    console.print()
    print_section_intro("What would make you decline a role, even if the title looks right?")
    console.print(
        "  [dim italic]Tip — think about: work location, company scale, scope,"
        " management expectations, comp, and any other hard negatives.[/dim italic]"
    )
    print_eg('"Pass on anything requiring relocation or regular onsite travel. Exclude heavy people management from day one."')
    console.print()
    console.print("  [dim italic][Enter] submit  ·  empty [Enter] to skip[/dim italic]")
    console.print()

    result = inquirer.text(
        message="  › ",
        style=INQUIRER_STYLE,
    ).execute() or ""
    return result.strip()


# ---------------------------------------------------------------------------
# Claude extraction (v2)
# ---------------------------------------------------------------------------

def _extract_with_claude_v2(api_key, resume_text, linkedin_text, want_text, dontwant_text, console):
    import anthropic
    import yaml

    client = anthropic.Anthropic(api_key=api_key)

    user_msg = "\n\n".join(filter(None, [
        f"RESUME:\n{resume_text[:12000]}",
        f"LINKEDIN:\n{linkedin_text[:4000]}" if linkedin_text else "LINKEDIN: (not provided)",
        f"WHAT_I_WANT:\n{want_text}" if want_text else "WHAT_I_WANT: (not provided)",
        f"WHAT_I_DONT_WANT:\n{dontwant_text}" if dontwant_text else "WHAT_I_DONT_WANT: (not provided)",
    ]))

    with console.status("  [dim]Extracting profile…[/dim]"):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_EXTRACT_SYSTEM_V2,
            messages=[{"role": "user", "content": user_msg}],
        )

    raw = response.content[0].text.strip()

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

    # Salary: amount extracted but no explicit floor signal in the text
    if profile.get("salary_floor_usd"):
        floor_signals = ["floor", "minimum", "at least", "no less than", "hard floor"]
        if not any(s in want_lower for s in floor_signals):
            # Find what the user wrote for from_text
            sal_match = re.search(r"\$[\d,k]+\+?", want_text or "", re.IGNORECASE)
            from_text = sal_match.group(0) if sal_match else f"${profile['salary_floor_usd']:,}"
            _add("salary_floor_or_target", from_text)

    # Remote ambiguity: "remote" present but "only" / "exclusively" absent
    if "remote" in want_lower and not any(s in want_lower for s in ["only", "exclusively", "no office", "fully remote"]):
        loc_pref = profile.get("candidate", {}).get("location_preference", "")
        if loc_pref in ("remote", "flexible", ""):
            rm_match = re.search(r"remote[\w\-]*", want_text or "", re.IGNORECASE)
            _add("remote_only_or_preferred", rm_match.group(0) if rm_match else "remote")

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
        "scorerole needs to clear up a few ambiguities before calibrating.",
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
        console.rule(style=_S(color=THEME["rule"]))
        console.print()

        counter = f"[dim][{i+1} of {total}][/dim]"
        label   = f"[bold]{spec['label']}[/bold]"
        src     = f"[dim][from: \"{from_text}\"][/dim]" if from_text else ""
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
        if answer == "floor":
            pass  # salary_floor_usd already set; keep as-is
        elif answer == "target":
            # Move to aspirational — clear hard floor, set soft target
            floor = profile.pop("salary_floor_usd", None)
            if floor:
                profile.setdefault("preferences", {})["base_salary_target_usd"] = floor

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
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice
    from rich.style import Style as _RS
    from .init_cmd import _show_profile, open_in_editor
    import yaml as _yaml

    console.print()
    console.print(
        "[bold]Step 4 of 4[/bold]  [bold]Review and save[/bold]",
        style=_RS(color=THEME["accent"], bold=True),
    )
    console.print()

    while True:
        console.print(
            f"  [{THEME['success']}]✓[/]  Profile extracted  "
            f"[{THEME['dim']}]·  edit anything before saving[/]"
        )
        console.print()
        _show_profile(profile)
        console.print()

        action = inquirer.select(
            message="What would you like to update?",
            qmark="",
            choices=[
                Choice("want",    "Step 2 — What you're looking for"),
                Choice("dontwant","Step 3 — What you'd pass on"),
                Choice("editor",  "Open full profile in editor"),
                Choice("rerun",   "Re-run extraction  [re-enter Steps 2 + 3]"),
                Choice("save",    "Save and continue"),
            ],
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
                console.print(f"  [yellow]⚠[/yellow]  Could not re-read profile after edit: {e}")

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
                new_profile, new_followups = _extract_with_claude_v2(
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

def run_init2(api_key):
    import yaml
    from rich import box as rich_box

    from .theme import THEME, INQUIRER_STYLE, console
    from .theme import print_section, print_section_intro, print_eg, print_confirmed, print_separator

    try:
        from InquirerPy import inquirer
    except ImportError:
        sys.exit("❌  InquirerPy not installed. Run: pip install InquirerPy")

    # ── Existing profile check ────────────────────────────────────────────────
    mode = _handle_existing_profile(console, INQUIRER_STYLE)

    if mode == "editor":
        from .init_cmd import open_in_editor
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
        console.print(f"\n  [green]✓[/green]  Saved to [dim]{PROFILE_PATH}[/dim]\n")
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
    print_confirmed("What you're looking for", want_text[:60] + ("…" if len(want_text) > 60 else ""))
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
        print_confirmed("What you'd pass on", dontwant_text[:60] + ("…" if len(dontwant_text) > 60 else ""))
    print_separator()

    # Extract
    console.print()
    profile, followups = _extract_with_claude_v2(
        api_key, resume_text, linkedin_text, want_text, dontwant_text, console,
    )
    console.print(f"  [green]✓[/green]  Extraction complete")

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
    console.print(f"\n  [green]✓[/green]  Saved to [dim]{PROFILE_PATH}[/dim]\n")

    # ── Proactive sources ─────────────────────────────────────────────────────
    from .init_cmd import _run_proactive_sources_wizard
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
            message="  › Set up automated digests? scorerole can email you on a schedule.",
            default=True,
            style=INQUIRER_STYLE,
        ).execute()
        if setup:
            run_schedule_wizard()
        else:
            console.print("  [dim]You can set this up later with: scorerole schedule set[/dim]")

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
        from .init_cmd import open_in_editor
        open_in_editor(PROFILE_PATH)

    console.print(
        "\n  [dim]Run `scorerole init2` any time to update your profile.[/dim]\n"
    )
