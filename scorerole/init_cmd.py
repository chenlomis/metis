"""scorerole init — interactive profile setup wizard.

Uses questionary for prompts and Rich for formatted output.
Parses a resume (PDF / DOCX / TXT), optionally a LinkedIn export,
collects preferences interactively, extracts a structured profile
with Claude, lets the user review and edit, then saves to
~/.job_pipeline/profile.yaml.
"""
import os, sys, shutil, subprocess, logging
from pathlib import Path

log = logging.getLogger(__name__)

# Suppress HTTP request logs so they don't bleed into the Rich spinner
# or appear between questionary prompts.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)

DATA_DIR     = Path.home() / ".job_pipeline"
PROFILE_PATH = DATA_DIR / "profile.yaml"

_RELOCATION_CITIES = [
    "San Francisco Bay Area",
    "New York / NYC",
    "Seattle / Pacific NW",
    "Austin, TX",
    "Boston, MA",
    "Los Angeles",
    "Chicago, IL",
    "Denver / Boulder",
    "Remote anywhere",
]


# ---------------------------------------------------------------------------
# File parsing
# ---------------------------------------------------------------------------

def _parse_file(path: Path) -> str:
    """Extract plain text from PDF, DOCX, or TXT/MD."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        try:
            import pdfplumber
        except ImportError:
            sys.exit("❌  pdfplumber not installed. Run: pip install pdfplumber")
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    if suffix in (".docx", ".doc"):
        try:
            import docx
        except ImportError:
            sys.exit("❌  python-docx not installed. Run: pip install python-docx")
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs)
    return path.read_text(errors="replace")


# ---------------------------------------------------------------------------
# Editor helper
# ---------------------------------------------------------------------------

def open_in_editor(path: Path):
    """Open a file in the user's preferred editor."""
    editor = (
        os.environ.get("VISUAL")
        or os.environ.get("EDITOR")
        or next((e for e in ["code", "cursor", "zed", "nano", "vi"] if shutil.which(e)), "nano")
    )
    try:
        subprocess.run([editor, str(path)])
    except FileNotFoundError:
        print(f"Could not open editor '{editor}'. File is at: {path}")


# ---------------------------------------------------------------------------
# Claude extraction
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You are a career profile extractor.

Given resume text (and optionally a LinkedIn export, supplementary notes, and
USER-PROVIDED PREFERENCES), extract the candidate's information and return ONLY
valid YAML matching this schema exactly — no markdown fences, no commentary,
no extra keys.

IMPORTANT: If a "USER-PROVIDED PREFERENCES" section is present, its values
override anything you would otherwise infer from the resume for these fields:
target.roles, deal_breakers, salary_floor_usd, candidate.open_to_remote,
candidate.open_to_relocation, aspirations.track, aspirations.direction,
aspirations.company_types, preferences.*, and notes (merge calibration text).

candidate:
  name: string
  email: string or null
  location: "City, State"
  open_to_remote: bool
  open_to_relocation: []         # list of cities/regions, or empty

target:
  roles: []                      # use user-provided if given; else infer from trajectory
  level: string                  # "ic", "senior", "staff", "director", "vp", "c-suite"
  industries: []                 # inferred from background

aspirations:
  track: string                  # "ic", "management", or "flexible"
  direction: string              # 2-3 sentences on career arc; use user-provided if given
  company_types: []              # company archetypes they're drawn to
  avoid_company_types: []        # soft avoids (hard no's go in deal_breakers)

preferences:
  company_stage: []              # e.g. ["growth", "public"]
  company_size: null             # "startup (<200)", "mid-size (200-2000)", "enterprise (2000+)", or null
  industry_targets: []           # industries to move toward (may differ from past)
  industry_avoid: []             # soft avoids
  base_salary_target_usd: null   # aspirational base (vs salary_floor_usd which is the hard floor)

scoring:
  apply_threshold: 75
  consider_threshold: 55
  level_mismatch_deduction: 10

experience:
  - company: string
    title: string
    dates: string
    highlights: []               # 2-4 bullet points, specific and metric-backed

education:
  - institution: string or null
    degree: string
    year: int or null

strengths: []                    # 6-10 items, each a concrete phrase with evidence
green_flags: []                  # role/company signals that boost score
yellow_flags: []                 # honest gaps or risks
red_flags: []                    # strong concerns
deal_breakers: []                # hard no's — roles matching any of these should score <=30
salary_floor_usd: int or null    # hard floor — use user-provided if given; else infer
notes: string                    # scoring calibration; distinguish hard filters from soft signals;
                                 # include level-mismatch rule if title understates scope

Rules:
- Use null or [] when information is absent; never omit a key.
- Infer target roles and level from title trajectory, not just current title.
- aspirations.direction: write from candidate's perspective, forward-looking, plain string.
- preferences.*: soft signals that nudge scores, not hard filters.
- deal_breakers: hard filters — score <=30 if matched.
- Be honest in yellow_flags — surface real gaps or risks.
- Return ONLY the YAML block. No markdown fences.
"""


def _safe_parse_yaml(raw: str) -> dict:
    """Parse Claude YAML output — strips fences, recovers from truncation."""
    import yaml

    raw = raw.strip()
    # Strip code fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        close = next(
            (i for i, l in enumerate(lines) if i > 0 and l.strip() == "```"),
            len(lines),
        )
        raw = "\n".join(lines[1:close])

    try:
        result = yaml.safe_load(raw)
        if isinstance(result, dict):
            return result
    except yaml.YAMLError:
        pass

    # Output was likely truncated — try progressively shorter slices
    lines = raw.splitlines()
    for cut in range(len(lines) - 1, len(lines) // 2, -1):
        try:
            result = yaml.safe_load("\n".join(lines[:cut]))
            if isinstance(result, dict) and result:
                log.warning("YAML truncated; recovered partial profile (%d/%d lines)", cut, len(lines))
                return result
        except yaml.YAMLError:
            continue

    raise ValueError(
        "Claude returned unparseable YAML. Try re-running extraction.\n"
        f"First 300 chars of raw output:\n{raw[:300]}"
    )


def _extract_with_claude(api_key: str, text: str, user_context: str = "") -> dict:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    model  = os.getenv("MODEL", "claude-sonnet-4-6")

    content = text[:12_000]
    if user_context:
        content += "\n\n" + user_context

    msg = client.messages.create(
        model=model,
        max_tokens=4096,    # 2048 was too small for the full schema; bumped to 4096
        system=_EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    return _safe_parse_yaml(msg.content[0].text)


# ---------------------------------------------------------------------------
# Rich display helpers
# ---------------------------------------------------------------------------

def _show_profile(profile: dict, console):
    from rich.table import Table
    from rich.panel import Panel
    from rich import box as rich_box

    c   = profile.get("candidate", {})
    t   = profile.get("target", {})
    asp = profile.get("aspirations", {})
    pref = profile.get("preferences", {})

    # expand=True makes the table fill available panel width so text wraps
    # instead of overflowing the terminal edge.
    tbl = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    tbl.add_column(style="dim", min_width=16, no_wrap=True, max_width=22)
    tbl.add_column(overflow="fold")

    tbl.add_row("Name",     c.get("name", "—"))
    tbl.add_row("Location", c.get("location", "—"))

    remote_str = "yes" if c.get("open_to_remote") else "no"
    reloc = c.get("open_to_relocation") or []
    if reloc:
        remote_str += f"  [dim]· relocation: {', '.join(reloc)}[/dim]"
    tbl.add_row("Remote", remote_str)

    tbl.add_row("Target roles", "\n".join(t.get("roles", [])) or "—")
    tbl.add_row("Level",        t.get("level", "—"))

    if asp.get("track"):
        tbl.add_row("Track", asp["track"])
    if asp.get("direction"):
        first = asp["direction"].strip().splitlines()[0][:80]
        tbl.add_row("Direction", first)
    if asp.get("company_types"):
        tbl.add_row("Drawn to", "\n".join(asp["company_types"]))

    pref_parts = []
    if pref.get("company_stage"):
        pref_parts.append(f"stage: {', '.join(pref['company_stage'])}")
    if pref_parts:
        tbl.add_row("Preferences", "\n".join(pref_parts))

    dbs = profile.get("deal_breakers", [])
    tbl.add_row("Deal-breakers", "\n".join(dbs) if dbs else "[dim]none set[/dim]")

    gf = profile.get("green_flags", [])
    if gf:
        gf_text = gf[0] + (f"\n[dim]… and {len(gf)-1} more[/dim]" if len(gf) > 1 else "")
        tbl.add_row("Green flags", gf_text)

    strengths = profile.get("strengths", [])
    if strengths:
        s_text = strengths[0] + (f"\n[dim]… and {len(strengths)-1} more[/dim]" if len(strengths) > 1 else "")
        tbl.add_row("Strengths", s_text)

    salary = profile.get("salary_floor_usd")
    if salary:
        tbl.add_row("Salary floor", f"${salary:,}")

    notes = (profile.get("notes") or "").strip()
    if notes:
        first_line = notes.splitlines()[0][:80]
        tbl.add_row("Scoring notes", first_line + ("[dim]…[/dim]" if len(notes) > len(first_line) else ""))

    console.print(Panel(
        tbl,
        title="[bold green]Extracted profile[/bold green]",
        subtitle="[dim]review below — nothing saved yet[/dim]",
        border_style="green",
        box=rich_box.ROUNDED,
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Preferences collection — Step 3
# Three focused sub-functions, merged by _collect_preferences.
#
# Design rule: use print() (not console.print()) for any text printed
# *between* questionary prompts — mixing Rich ANSI and prompt_toolkit
# mid-sequence causes cursor-tracking bugs on terminal resize.
# console.print() is safe only before or after a full prompt sequence.
# Also: no instruction= on questionary.text() — it forces prompt_toolkit
# to recalculate layout on every keypress, causing duplication on resize.
# ---------------------------------------------------------------------------

def _collect_hard_filters(Q_STYLE) -> dict:
    import questionary

    print()
    print("  Hard filters  — these disqualify or cap a role's score")
    print("  ──────────────────────────────────────────────────────")
    print()
    print("  Target roles: be aspirational (e.g. Staff PM, Principal PM, Director of Product)")

    target_roles = questionary.text("  Target roles:", style=Q_STYLE).ask() or ""

    print()
    print("  Work mode: select all that apply  (leave blank = no preference)")
    work_mode = questionary.checkbox(
        "  Work mode:",
        choices=["Remote-first", "Hybrid OK", "On-site OK"],
        style=Q_STYLE,
    ).ask() or []

    print()
    print("  Relocation: leave blank = current location only")
    relocation = questionary.checkbox(
        "  Open to relocating to:",
        choices=_RELOCATION_CITIES,
        style=Q_STYLE,
    ).ask() or []

    print()
    print("  Deal-breakers: hard no's, comma-separated (e.g. no equity, on-site 5d/wk)")
    deal_breakers = questionary.text("  Deal-breakers:", style=Q_STYLE).ask() or ""

    print()
    print("  Min. base salary: numbers only, leave blank to let Claude estimate")
    salary_floor = questionary.text("  Min. base salary (USD):", style=Q_STYLE).ask() or ""

    return {
        "target_roles":      [r.strip() for r in target_roles.split(",") if r.strip()],
        "work_mode":         work_mode,
        "relocation_cities": relocation,
        "deal_breakers":     [d.strip() for d in deal_breakers.split(",") if d.strip()],
        "salary_floor":      salary_floor.replace(",", "").replace("$", "").strip(),
    }


def _collect_aspirations(Q_STYLE) -> dict:
    import questionary

    print()
    print("  Aspirations  — where you're headed, not just where you've been")
    print("  ────────────────────────────────────────────────────────────────")
    print()

    track = questionary.select(
        "  Career track:",
        choices=[
            questionary.Choice("IC-focused  (Staff / Principal / Distinguished)", value="ic"),
            questionary.Choice("Management  (Director / VP / C-suite)",            value="management"),
            questionary.Choice("Flexible — open to both",                          value="flexible"),
        ],
        style=Q_STYLE,
    ).ask() or "flexible"

    print()
    print("  Career direction: 2-3 sentences on where you want to go and why")
    direction = questionary.text("  Career direction:", style=Q_STYLE).ask() or ""

    print()
    print("  Company types: e.g. AI-native startup, enterprise SaaS with applied science team")
    company_types = questionary.text("  Company types drawn to:", style=Q_STYLE).ask() or ""

    return {
        "track":         track,
        "direction":     direction.strip(),
        "company_types": [c.strip() for c in company_types.split(",") if c.strip()],
    }


def _collect_soft_preferences(Q_STYLE) -> dict:
    import questionary

    print()
    print("  Soft preferences  — adjust ranking, not hard filters")
    print("  ─────────────────────────────────────────────────────")
    print()

    company_stage = questionary.checkbox(
        "  Company stage:",
        choices=["Seed / Series A", "Growth (Series B–D)", "Late-stage / pre-IPO", "Public / enterprise"],
        style=Q_STYLE,
    ).ask() or []

    print()
    print("  Industries to move toward: may differ from your past, comma-separated")
    industry_targets = questionary.text("  Industries to move toward:", style=Q_STYLE).ask() or ""

    print()
    print("  Anything else Claude should know? e.g. 'Staff-scope despite Senior title'")
    calibration = questionary.text("  Anything else:", style=Q_STYLE).ask() or ""

    return {
        "company_stage":    company_stage,
        "industry_targets": [i.strip() for i in industry_targets.split(",") if i.strip()],
        "calibration":      calibration.strip(),
    }


def _collect_preferences(console, Q_STYLE) -> dict:
    """Orchestrate the three preference sub-steps and merge results."""
    console.print()
    console.print("[dim]  Step 3 of 4 — Your preferences[/dim]")
    console.print("[dim]  ─────────────────────────────────[/dim]")
    console.print()
    console.print(
        "  Your resume shows [italic]what you've done.[/italic]  "
        "These questions capture [italic]where you're headed[/italic]\n"
        "  and your personal rules — things Claude can't reliably infer.\n"
    )

    hard = _collect_hard_filters(Q_STYLE)
    # ── Summary line after hard filters ─────────────────────────────────────
    _roles_str = ", ".join(hard["target_roles"][:2]) + ("…" if len(hard["target_roles"]) > 2 else "")
    _wm_str    = ", ".join(hard.get("work_mode") or []) or "no pref"
    _sal_str   = f"  ·  ${hard['salary_floor']} floor" if hard.get("salary_floor") else ""
    _db_str    = f"  ·  {len(hard['deal_breakers'])} deal-breaker{'s' if len(hard['deal_breakers']) != 1 else ''}" if hard.get("deal_breakers") else ""
    print()
    print(f"  ✓  Hard filters  ·  {_roles_str or '—'}  ·  {_wm_str}{_sal_str}{_db_str}")

    asp = _collect_aspirations(Q_STYLE)
    # ── Summary line after aspirations ──────────────────────────────────────
    _track_label = {"ic": "IC track", "management": "Management track", "flexible": "Flexible"}.get(asp["track"], asp["track"])
    _co_str = ", ".join(asp["company_types"][:2]) + ("…" if len(asp["company_types"]) > 2 else "") if asp["company_types"] else ""
    print()
    print(f"  ✓  Aspirations  ·  {_track_label}" + (f"  ·  {_co_str}" if _co_str else ""))

    soft = _collect_soft_preferences(Q_STYLE)
    # ── Summary line after soft preferences ─────────────────────────────────
    _stage_str = ", ".join(soft["company_stage"]) if soft["company_stage"] else "any stage"
    _ind_str   = ", ".join(soft["industry_targets"][:2]) + ("…" if len(soft["industry_targets"]) > 2 else "") if soft["industry_targets"] else ""
    print()
    print(f"  ✓  Preferences  ·  {_stage_str}" + (f"  ·  {_ind_str}" if _ind_str else ""))
    print()

    return {**hard, **asp, **soft}


def _format_user_context(prefs: dict) -> str:
    """Format collected preferences into a structured block Claude ingests."""
    lines = ["--- USER-PROVIDED PREFERENCES (override inferred values) ---", ""]

    lines.append("# Hard filters")
    if prefs.get("target_roles"):
        lines.append(f"Target roles: {', '.join(prefs['target_roles'])}")
    _wm = prefs.get("work_mode") or []
    lines.append(f"Work mode: {', '.join(_wm) if _wm else 'no preference'}")
    reloc = prefs.get("relocation_cities") or []
    if reloc:
        lines.append(f"Open to relocation: yes — {', '.join(reloc)}")
    else:
        lines.append("Open to relocation: no")
    if prefs.get("deal_breakers"):
        lines.append(f"Deal-breakers (score <=30 if matched): {', '.join(prefs['deal_breakers'])}")
    if prefs.get("salary_floor"):
        lines.append(f"Minimum base salary (hard floor): ${prefs['salary_floor']}")

    lines += ["", "# Aspirations"]
    if prefs.get("track"):
        lines.append(f"Career track: {prefs['track']}")
    if prefs.get("direction"):
        lines.append(f"Career direction: {prefs['direction']}")
    if prefs.get("company_types"):
        lines.append(f"Drawn to company types: {', '.join(prefs['company_types'])}")

    lines += ["", "# Soft preferences (nudge ranking, don't disqualify)"]
    if prefs.get("company_stage"):
        lines.append(f"Company stage: {', '.join(prefs['company_stage'])}")
    if prefs.get("industry_targets"):
        lines.append(f"Industries to move toward: {', '.join(prefs['industry_targets'])}")
    if prefs.get("calibration"):
        lines += ["", "# Extra context", prefs["calibration"]]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Preferences → profile mapper  (used by "Update prefs" re-run mode)
# ---------------------------------------------------------------------------

def _apply_prefs_to_profile(profile: dict, prefs: dict) -> None:
    """Apply collected preferences to an existing profile dict in-place.

    Preserves Claude-extracted fields (experience, strengths, flags).
    Overwrites goals, filters, and soft preferences from fresh user input.
    """
    # Target roles
    if prefs.get("target_roles"):
        profile.setdefault("target", {})["roles"] = prefs["target_roles"]

    # Work mode → open_to_remote
    work_mode = prefs.get("work_mode") or []
    if work_mode:
        profile.setdefault("candidate", {})["open_to_remote"] = "Remote-first" in work_mode

    # Relocation
    reloc = prefs.get("relocation_cities")
    if reloc is not None:
        profile.setdefault("candidate", {})["open_to_relocation"] = reloc

    # Deal-breakers (replace, not append)
    if prefs.get("deal_breakers") is not None:
        profile["deal_breakers"] = prefs["deal_breakers"]

    # Salary floor
    if prefs.get("salary_floor"):
        try:
            profile["salary_floor_usd"] = int(prefs["salary_floor"])
        except (ValueError, TypeError):
            pass

    # Aspirations
    asp = profile.setdefault("aspirations", {})
    if prefs.get("track"):
        asp["track"] = prefs["track"]
    if prefs.get("direction"):
        asp["direction"] = prefs["direction"]
    if prefs.get("company_types"):
        asp["company_types"] = prefs["company_types"]

    # Soft preferences
    pref = profile.setdefault("preferences", {})
    if prefs.get("company_stage") is not None:
        pref["company_stage"] = prefs["company_stage"]
    if prefs.get("industry_targets") is not None:
        pref["industry_targets"] = prefs["industry_targets"]

    # Extra calibration notes — append to existing
    if prefs.get("calibration"):
        existing = (profile.get("notes") or "").strip()
        profile["notes"] = (existing + "\n\n" + prefs["calibration"]).strip() if existing else prefs["calibration"]


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_init(api_key: str, resume_path_arg: str = "", supplement_path_arg: str = ""):
    try:
        import questionary
        import yaml
        from questionary import Style as QStyle
        from rich.console import Console
        from rich.panel import Panel
        from rich import box as rich_box
    except ImportError as e:
        sys.exit(f"❌  Missing dependency: {e}\n    Run: pip install questionary rich")

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

    console = Console()

    # State — full wizard populates these; quick/update_prefs paths set them directly.
    full_text       = ""
    user_context    = ""
    profile         = None
    skip_extraction = False

    # ── Detect existing profile ───────────────────────────────────────────────
    if PROFILE_PATH.exists() and not resume_path_arg:
        import datetime as _dt
        try:
            existing = yaml.safe_load(PROFILE_PATH.read_text()) or {}
        except Exception:
            existing = {}

        mod_time  = _dt.datetime.fromtimestamp(PROFILE_PATH.stat().st_mtime).strftime("%b %d, %Y")
        cand_name = (existing.get("candidate") or {}).get("name", "")

        console.print()
        console.print(Panel(
            "[bold]Profile found[/bold]  [dim]· last updated " + mod_time + "[/dim]"
            + (f"\n  {cand_name}" if cand_name else ""),
            border_style="green",
            box=rich_box.ROUNDED,
            padding=(1, 3),
        ))
        console.print()

        mode = questionary.select(
            "  What do you want to do?",
            choices=[
                questionary.Choice("Quick edit    — jump to review menu, no re-extraction",        value="quick"),
                questionary.Choice("Update prefs  — re-answer Step 3, apply to existing profile",  value="update_prefs"),
                questionary.Choice("Open in editor — edit profile.yaml directly",                  value="editor"),
                questionary.Choice("Start fresh   — new resume, full re-extraction",               value="full"),
            ],
            style=Q_STYLE,
        ).ask()

        if mode is None:
            sys.exit(0)

        if mode == "editor":
            open_in_editor(PROFILE_PATH)
            sys.exit(0)

        elif mode == "quick":
            profile = existing
            skip_extraction = True
            console.print()
            _show_profile(profile, console)
            console.print()

        elif mode == "update_prefs":
            profile = existing
            skip_extraction = True
            console.print()
            console.print(
                "  [dim]Experience, strengths, and flags from your last extraction are preserved.[/dim]\n"
                "  [dim]Your new answers below update goals, deal-breakers, and preferences.[/dim]"
            )
            console.print()
            _show_profile(profile, console)
            prefs = _collect_preferences(console, Q_STYLE)
            user_context = _format_user_context(prefs)
            _apply_prefs_to_profile(profile, prefs)
            console.print()
            _show_profile(profile, console)
            console.print()

        # mode == "full" falls through to the full wizard below

    if not skip_extraction:
        # ── Welcome ──────────────────────────────────────────────────────────
        console.print()
        console.print(Panel(
            "[bold]Let's build your scorerole profile![/bold]\n\n"
            "The more context you provide, the better we can filter and\n"
            "score roles against your background.\n\n"
            "  [dim]1.[/dim]  Point us to your resume (PDF, DOCX, or TXT)\n"
            "  [dim]2.[/dim]  Optionally add your LinkedIn profile\n"
            "  [dim]3.[/dim]  Tell us about your aspirations, deal-breakers,\n"
            "       and preferences\n"
            "  [dim]4.[/dim]  Review and tweak the final profile before saving\n\n"
            "[dim]It takes about 2 mins.  Run `scorerole init` anytime to update.[/dim]",
            border_style="dim",
            box=rich_box.ROUNDED,
            padding=(1, 3),
        ))

        # ── Step 1: Resume ───────────────────────────────────────────────────
        console.print()
        console.print("[dim]  Step 1 of 4 — Resume[/dim]")
        console.print("[dim]  ─────────────────────[/dim]")
        console.print()
        console.print(
            "  [dim]Accepted: PDF, DOCX, TXT/MD"
            "   ·   Tip: drag the file into this window to paste its path.[/dim]\n"
        )

        resume_path = None
        if resume_path_arg:
            p = Path(resume_path_arg).expanduser().resolve()
            if not p.exists():
                console.print(f"  [red]File not found:[/red] {resume_path_arg}\n")
            elif p.is_dir():
                console.print(f"  [red]That's a folder, not a file:[/red] {resume_path_arg}\n")
            else:
                resume_path = p

        while not resume_path:
            raw = questionary.path("  Path to your resume:", style=Q_STYLE).ask()
            if raw is None:
                sys.exit(0)
            raw = raw.strip().strip("\"'").replace("\\ ", " ")
            p = Path(raw).expanduser().resolve()
            if not p.exists():
                console.print("  [red]File not found — try again.[/red]\n")
            elif p.is_dir():
                console.print(
                    "  [red]That's a folder, not a file.[/red]  "
                    "Drag your resume [italic]file[/italic] into the terminal window.\n"
                )
            else:
                resume_path = p

        resume_text = _parse_file(resume_path)
        console.print(
            f"\n  [green]✓[/green]  {resume_path.name} "
            f"[dim]({len(resume_text):,} characters)[/dim]\n"
        )

        # ── Step 2: LinkedIn (optional) ──────────────────────────────────────
        console.print("[dim]  Step 2 of 4 — LinkedIn (optional)[/dim]")
        console.print("[dim]  ──────────────────────────────────[/dim]")
        console.print()
        console.print(
            "  Your LinkedIn profile often contains skills, endorsements, and role details\n"
            "  that resumes leave out. Adding it improves how well your profile matches roles.\n"
        )

        wants_linkedin = questionary.confirm(
            "  Add your LinkedIn profile?", default=False, style=Q_STYLE
        ).ask()

        supp_text = ""
        if wants_linkedin:
            console.print()
            console.print("  [dim]Export: LinkedIn → Me → Settings & Privacy → Data Privacy[/dim]")
            console.print("  [dim]        → Get a copy of your data → Profile → Request archive[/dim]")
            console.print("  [dim]        LinkedIn emails you a link (usually within minutes).[/dim]\n")

            supp_path = None
            while supp_path is None:
                raw = questionary.path("  Path to LinkedIn PDF (Enter to skip):", style=Q_STYLE).ask()
                if raw is None or raw.strip() == "":
                    break
                raw = raw.strip().strip("\"'").replace("\\ ", " ")
                p = Path(raw).expanduser().resolve()
                if not p.exists():
                    console.print("  [red]File not found — try again, or press Enter to skip.[/red]\n")
                elif p.is_dir():
                    console.print(
                        "  [red]That's a folder, not a file.[/red]  "
                        "Drag the LinkedIn PDF into the terminal, or press Enter to skip.\n"
                    )
                else:
                    supp_path = p

            if supp_path:
                supp_text = _parse_file(supp_path)
                console.print(
                    f"\n  [green]✓[/green]  {supp_path.name} "
                    f"[dim]({len(supp_text):,} characters)[/dim]"
                )

        full_text = resume_text
        if supp_text:
            full_text += "\n\n--- SUPPLEMENTARY PROFILE ---\n\n" + supp_text

        # ── Step 3: Preferences ──────────────────────────────────────────────
        prefs = _collect_preferences(console, Q_STYLE)
        user_context = _format_user_context(prefs)

        # ── Step 4: Extract ──────────────────────────────────────────────────
        console.print()
        console.print("[dim]  Step 4 of 4 — Build your profile[/dim]")
        console.print("[dim]  ──────────────────────────────────[/dim]")
        console.print()

        with console.status("  [dim]Analyzing your resume with Claude…[/dim]"):
            try:
                profile = _extract_with_claude(api_key, full_text, user_context)
            except Exception as e:
                sys.exit(f"\n❌  Extraction failed: {e}")

        console.print("  [green]✓[/green]  Extraction complete\n")
        _show_profile(profile, console)
        console.print()

    # ── Review loop ───────────────────────────────────────────────────────────
    # "Re-run extraction" only offered when we have a resume to work with.
    review_choices = [
        questionary.Choice("Save profile",                                          value="save"),
        questionary.Separator("  ─────────────────────────────────────────────"),
        questionary.Choice("  Target roles & level",                                value="roles"),
        questionary.Choice("  Strengths",                                           value="strengths"),
        questionary.Choice("  Aspirations & career goals",                          value="asp"),
        questionary.Choice("  Deal-breakers",                                       value="dbs"),
        questionary.Choice("  Minimum salary",                                      value="salary"),
        questionary.Choice("  Nice-to-haves  (company stage, industry)",            value="prefs"),
        questionary.Choice("  Boost signals  (green flags Claude watches for)",     value="gf"),
        questionary.Choice("  AI instructions  (custom scoring guidance)",          value="notes"),
    ]
    if full_text:
        review_choices.append(questionary.Separator("  ─────────────────────────────────────────────"))
        review_choices.append(questionary.Choice("  Re-run AI extraction",          value="rerun"))

    while True:
        print()
        print("  Arrow keys to navigate  ·  Enter to select  ·  Ctrl-C to cancel")
        action = questionary.select(
            "  Looks good?",
            choices=review_choices,
            style=Q_STYLE,
        ).ask()

        if action is None or action == "save":
            break

        elif action == "roles":
            t = profile.setdefault("target", {})
            new_roles = questionary.text(
                "  Target roles:", default=", ".join(t.get("roles", [])), style=Q_STYLE
            ).ask()
            if new_roles:
                t["roles"] = [r.strip() for r in new_roles.split(",") if r.strip()]
            valid_levels = ["ic", "senior", "staff", "director", "vp", "c-suite"]
            new_level = questionary.select(
                "  Seniority level:",
                choices=valid_levels,
                default=t.get("level") if t.get("level") in valid_levels else "staff",
                style=Q_STYLE,
            ).ask()
            if new_level:
                t["level"] = new_level

        elif action == "strengths":
            print()
            print("  List your key strengths — each on its own line (semicolons separate them).")
            print("  Be specific: 'ML depth — trained 2k+ models, RLHF at DocuSign' beats 'ML skills'.")
            current_strengths = "; ".join(profile.get("strengths", []))
            new_val = questionary.text(
                "  Strengths:", default=current_strengths, style=Q_STYLE
            ).ask()
            if new_val is not None:
                profile["strengths"] = [s.strip() for s in new_val.split(";") if s.strip()]

        elif action == "asp":
            asp = profile.setdefault("aspirations", {})
            new_track = questionary.select(
                "  Career track:",
                choices=[
                    questionary.Choice("IC-focused",            value="ic"),
                    questionary.Choice("Management",            value="management"),
                    questionary.Choice("Flexible — open to both", value="flexible"),
                ],
                default=asp.get("track", "flexible"),
                style=Q_STYLE,
            ).ask()
            if new_track:
                asp["track"] = new_track
            new_dir = questionary.text(
                "  Career direction:", default=(asp.get("direction") or "").strip(), style=Q_STYLE
            ).ask()
            if new_dir is not None:
                asp["direction"] = new_dir.strip()
            new_co = questionary.text(
                "  Company types drawn to:",
                default=", ".join(asp.get("company_types") or []),
                style=Q_STYLE,
            ).ask()
            if new_co is not None:
                asp["company_types"] = [c.strip() for c in new_co.split(",") if c.strip()]

        elif action == "dbs":
            print()
            print("  Deal-breakers: absolute walk-away rules. Roles matching any of these score ≤30.")
            print("  Comma-separated. E.g. no equity, on-site 5d/wk, no AI/ML product surface")
            new_val = questionary.text(
                "  Deal-breakers:", default=", ".join(profile.get("deal_breakers", [])), style=Q_STYLE
            ).ask()
            if new_val is not None:
                profile["deal_breakers"] = [d.strip() for d in new_val.split(",") if d.strip()]

        elif action == "prefs":
            print()
            print("  Nice-to-haves: soft signals that nudge score up or down — don't disqualify.")
            pref = profile.setdefault("preferences", {})
            new_stage = questionary.checkbox(
                "  Company stage:",
                choices=["Seed / Series A", "Growth (Series B–D)", "Late-stage / pre-IPO", "Public / enterprise"],
                default=pref.get("company_stage") or [],
                style=Q_STYLE,
            ).ask()
            if new_stage is not None:
                pref["company_stage"] = new_stage
            new_ind = questionary.text(
                "  Industries to move toward:",
                default=", ".join(pref.get("industry_targets") or []),
                style=Q_STYLE,
            ).ask()
            if new_ind is not None:
                pref["industry_targets"] = [i.strip() for i in new_ind.split(",") if i.strip()]

        elif action == "gf":
            print()
            print("  Boost signals: role/company signals that strongly increase a job's score.")
            print("  E.g. 'lean team with 0-1 scope', 'AI-native company', 'technical product ownership'")
            new_val = questionary.text(
                "  Boost signals:", default=", ".join(profile.get("green_flags", [])), style=Q_STYLE
            ).ask()
            if new_val is not None:
                profile["green_flags"] = [g.strip() for g in new_val.split(",") if g.strip()]

        elif action == "salary":
            print()
            print("  Minimum salary: hard floor in USD. Roles below this score ≤30 regardless of fit.")
            current = str(profile.get("salary_floor_usd") or "")
            new_val = questionary.text("  Minimum salary (USD):", default=current, style=Q_STYLE).ask()
            if new_val:
                try:
                    profile["salary_floor_usd"] = int(new_val.replace(",", "").replace("$", ""))
                except ValueError:
                    console.print("  [red]Could not parse — keeping current value.[/red]")

        elif action == "notes":
            print()
            print("  AI instructions: free-text guidance for the scoring model.")
            print("  E.g. 'Staff-scope despite Senior title — weight scope over title'")
            print("       'Deduct 15 pts for roles that require people management as primary duty'")
            new_val = questionary.text(
                "  AI instructions:", default=(profile.get("notes") or "").strip(), style=Q_STYLE
            ).ask()
            if new_val is not None:
                profile["notes"] = new_val.strip()

        elif action == "rerun":
            with console.status("  [dim]Re-running extraction…[/dim]"):
                try:
                    profile = _extract_with_claude(api_key, full_text, user_context)
                except Exception as e:
                    console.print(f"  [red]Extraction failed: {e}[/red]")
                    continue

        console.print()
        _show_profile(profile, console)
        console.print()

    # ── Save ──────────────────────────────────────────────────────────────────
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))
    console.print(f"\n  [green]✓[/green]  Saved to [dim]{PROFILE_PATH}[/dim]\n")

    # ── What next ─────────────────────────────────────────────────────────────
    next_action = questionary.select(
        "  What next?",
        choices=[
            questionary.Choice("Open profile in editor", value="profile"),
            questionary.Choice("Open .env in editor",    value="env"),
            questionary.Choice("Done",                   value="exit"),
        ],
        style=Q_STYLE,
    ).ask()

    if next_action == "profile":
        open_in_editor(PROFILE_PATH)
    elif next_action == "env":
        env_example = Path(__file__).parent.parent / ".env.example"
        open_in_editor(env_example if env_example.exists() else PROFILE_PATH.parent)

    console.print(
        "\n  [dim]Run `scorerole init` any time to update your profile.[/dim]\n"
        "  [dim]Run `scorerole config profile` to open it in your editor.[/dim]\n"
    )
