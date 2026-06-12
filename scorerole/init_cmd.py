"""scorerole init — interactive profile setup wizard.

Uses questionary for prompts and Rich for formatted output.
Parses a resume (PDF / DOCX / TXT), optionally a LinkedIn export,
extracts a structured profile with Claude, lets the user review and
edit inline, then saves to ~/.job_pipeline/profile.yaml.
"""
import os, sys, shutil, subprocess, logging
from pathlib import Path

log = logging.getLogger(__name__)

DATA_DIR     = Path.home() / ".job_pipeline"
PROFILE_PATH = DATA_DIR / "profile.yaml"


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
  direction: |                   # use user-provided; 2-3 sentences on career arc and desired next chapter
  company_types: []              # company archetypes they're drawn to (e.g. "AI-native startup")
  avoid_company_types: []        # company types to avoid — soft, not hard (those go in deal_breakers)

preferences:
  company_stage: []              # ordered preference: "seed", "series-a", "growth", "public"
  company_size: null             # "startup (<200)", "mid-size (200-2000)", "enterprise (2000+)", or null
  industry_targets: []           # industries to move toward (may differ from past industries)
  industry_avoid: []             # industries to steer away from — soft signal, affects ranking
  comp_target_usd: null          # aspirational total comp (vs salary_floor which is a hard floor)
  work_style: null               # "deep IC / individual focus", "cross-functional leadership",
                                 # "player-coach", or null

scoring:
  apply_threshold: 75
  consider_threshold: 55
  level_mismatch_deduction: 10

experience:
  - company: string
    title: string
    dates: string
    highlights: []               # 2-4 bullet points per role, specific and metric-backed

education:
  - institution: string or null
    degree: string
    year: int or null

strengths: []                    # 6-10 items, each a concrete phrase with evidence
green_flags: []                  # role / company signals that boost score
yellow_flags: []                 # honest gaps or risks
red_flags: []                    # strong concerns
deal_breakers: []                # hard no's — use user-provided if given; roles matching these
                                 # should be capped at a low score regardless of other signals
salary_floor_usd: int or null    # hard floor — use user-provided if given; else infer
notes: |
  Scoring calibration. Include a level-mismatch rule if title understates scope.
  Distinguish hard filters (deal_breakers, salary_floor) from soft signals
  (preferences.*) that adjust ranking rather than disqualify.
  Incorporate any user-provided calibration text verbatim.

Rules:
- Use null or [] when information is absent; never omit a key.
- Infer target roles and level from title trajectory, not just current title.
- aspirations.direction: write from the candidate's perspective, forward-looking.
- preferences.*: soft signals — they should nudge scores, not hard-filter.
- deal_breakers: hard filters — a role matching any deal-breaker should score ≤30.
- Be honest in yellow_flags — surface real gaps or risks.
- Return ONLY the YAML block.
"""


def _extract_with_claude(api_key: str, text: str, user_context: str = "") -> dict:
    import anthropic, yaml

    client = anthropic.Anthropic(api_key=api_key)
    model  = os.getenv("MODEL", "claude-sonnet-4-6")

    content = text[:12_000]
    if user_context:
        content += "\n\n" + user_context

    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": content}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return yaml.safe_load(raw)


# ---------------------------------------------------------------------------
# Rich display helpers
# ---------------------------------------------------------------------------

def _show_profile(profile: dict, console):
    from rich.table import Table
    from rich.panel import Panel
    from rich import box as rich_box

    c = profile.get("candidate", {})
    t = profile.get("target", {})

    tbl = Table(show_header=False, box=None, padding=(0, 2))
    tbl.add_column(style="dim", min_width=16)
    tbl.add_column()

    tbl.add_row("Name",         c.get("name", "—"))
    tbl.add_row("Location",     c.get("location", "—"))
    remote_str = "yes" if c.get("open_to_remote") else "no"
    reloc = c.get("open_to_relocation") or []
    if reloc:
        remote_str += f"  [dim]· relocation: {', '.join(reloc)}[/dim]"
    tbl.add_row("Remote",       remote_str)
    tbl.add_row("Target roles", "\n".join(t.get("roles", [])) or "—")
    tbl.add_row("Level",        t.get("level", "—"))

    asp = profile.get("aspirations", {})
    if asp.get("track"):
        tbl.add_row("Track", asp["track"])
    if asp.get("direction"):
        direction_preview = asp["direction"].strip().splitlines()[0][:80]
        tbl.add_row("Direction", direction_preview)
    if asp.get("company_types"):
        tbl.add_row("Drawn to", "\n".join(asp["company_types"]))

    pref = profile.get("preferences", {})
    pref_parts = []
    if pref.get("company_stage"):
        pref_parts.append(f"stage: {', '.join(pref['company_stage'])}")
    if pref.get("work_style"):
        pref_parts.append(f"style: {pref['work_style']}")
    if pref_parts:
        tbl.add_row("Preferences", "\n".join(pref_parts))

    dbs = profile.get("deal_breakers", [])
    tbl.add_row("Deal-breakers", ("\n".join(dbs)) if dbs else "[dim]none set[/dim]")

    gf = profile.get("green_flags", [])
    if gf:
        gf_preview = gf[0]
        if len(gf) > 1:
            gf_preview += f"\n[dim]… and {len(gf) - 1} more[/dim]"
        tbl.add_row("Green flags", gf_preview)

    strengths = profile.get("strengths", [])
    if strengths:
        preview = strengths[0]
        if len(strengths) > 1:
            preview += f"\n[dim]… and {len(strengths) - 1} more[/dim]"
        tbl.add_row("Strengths", preview)

    salary = profile.get("salary_floor_usd")
    if salary:
        tbl.add_row("Salary floor", f"${salary:,}")

    notes = (profile.get("notes") or "").strip()
    if notes:
        first_line = notes.splitlines()[0][:80]
        if len(notes) > len(first_line):
            first_line += "[dim]…[/dim]"
        tbl.add_row("Scoring notes", first_line)

    console.print(Panel(tbl, title="[bold green]Extracted profile[/bold green]",
                        subtitle="[dim]review below — nothing saved yet[/dim]",
                        border_style="green", box=rich_box.ROUNDED, padding=(1, 2)))


# ---------------------------------------------------------------------------
# Preferences collection (Step 3)
# ---------------------------------------------------------------------------

def _collect_preferences(console, Q_STYLE) -> dict:
    """Collect the intent and constraints that resumes can't capture."""
    import questionary

    console.print()
    console.print("[dim]  Step 3 of 4 — Your preferences[/dim]")
    console.print("[dim]  ─────────────────────────────[/dim]")
    console.print()
    console.print(
        "  Your resume shows [italic]what you've done.[/italic]\n"
        "  These questions capture [italic]where you're headed[/italic] and your\n"
        "  personal rules — things Claude can't reliably infer.\n"
    )

    # ── Hard filters ─────────────────────────────────────────────────────────
    console.print("  [bold]Hard filters[/bold]  [dim]— these disqualify or cap a role's score[/dim]\n")

    target_roles = questionary.text(
        "  Target roles:",
        instruction="(be aspirational — what you're going for, not just where you've been)",
        style=Q_STYLE,
    ).ask() or ""

    work_mode = questionary.select(
        "  Work mode:",
        choices=["Remote-first", "Hybrid OK", "On-site OK", "No preference"],
        style=Q_STYLE,
    ).ask() or "No preference"

    wants_relocation = questionary.confirm(
        "  Open to relocation?", default=False, style=Q_STYLE
    ).ask()
    relocation_cities = ""
    if wants_relocation:
        relocation_cities = questionary.text(
            "  Preferred cities / regions:",
            instruction="(comma-separated)",
            style=Q_STYLE,
        ).ask() or ""

    deal_breakers = questionary.text(
        "  Deal-breakers:",
        instruction="(hard no's — roles matching any of these score ≤30 regardless of fit)",
        style=Q_STYLE,
    ).ask() or ""

    salary_floor = questionary.text(
        "  Minimum salary (USD, numbers only):",
        instruction="(hard floor — leave blank to let Claude estimate from seniority)",
        style=Q_STYLE,
    ).ask() or ""

    # ── Aspirations ───────────────────────────────────────────────────────────
    console.print()
    console.print("  [bold]Aspirations[/bold]  [dim]— where you're headed, not just where you've been[/dim]\n")

    track = questionary.select(
        "  Career track:",
        choices=[
            questionary.Choice("IC-focused  (Staff / Principal / Distinguished)", value="ic"),
            questionary.Choice("Management  (Director / VP / C-suite)", value="management"),
            questionary.Choice("Flexible — open to both", value="flexible"),
        ],
        style=Q_STYLE,
    ).ask() or "flexible"

    direction = questionary.text(
        "  Career direction (optional):",
        instruction="(2-3 sentences — where you want to go and why)",
        style=Q_STYLE,
    ).ask() or ""

    company_types = questionary.text(
        "  Company types you're drawn to (optional):",
        instruction="(e.g. AI-native startup, enterprise SaaS with applied science team)",
        style=Q_STYLE,
    ).ask() or ""

    # ── Soft preferences ──────────────────────────────────────────────────────
    console.print()
    console.print("  [bold]Soft preferences[/bold]  [dim]— these adjust ranking, not hard-filter[/dim]\n")

    company_stage = questionary.checkbox(
        "  Company stage (select all that appeal):",
        choices=["Seed / Series A", "Growth (Series B–D)", "Late-stage / pre-IPO", "Public / enterprise"],
        style=Q_STYLE,
    ).ask() or []

    industry_targets = questionary.text(
        "  Industries you want to move toward (optional):",
        instruction="(may differ from your past — comma-separated)",
        style=Q_STYLE,
    ).ask() or ""

    work_style = questionary.select(
        "  Work style preference:",
        choices=[
            "Deep IC / individual focus",
            "Cross-functional leadership",
            "Player-coach (both)",
            "No preference",
        ],
        style=Q_STYLE,
    ).ask() or "No preference"

    comp_target = questionary.text(
        "  Target total comp (USD, optional):",
        instruction="(aspirational — separate from your hard floor)",
        style=Q_STYLE,
    ).ask() or ""

    calibration = questionary.text(
        "  Scoring calibration (optional):",
        instruction="(e.g. 'Staff scope despite Senior title — weight impact over title')",
        style=Q_STYLE,
    ).ask() or ""

    return {
        # hard filters
        "target_roles":      [r.strip() for r in target_roles.split(",")      if r.strip()],
        "work_mode":         work_mode,
        "relocation_cities": [c.strip() for c in relocation_cities.split(",") if c.strip()],
        "deal_breakers":     [d.strip() for d in deal_breakers.split(",")     if d.strip()],
        "salary_floor":      salary_floor.replace(",", "").replace("$", "").strip(),
        # aspirations
        "track":             track,
        "direction":         direction.strip(),
        "company_types":     [t.strip() for t in company_types.split(",")     if t.strip()],
        # soft preferences
        "company_stage":     company_stage,
        "industry_targets":  [i.strip() for i in industry_targets.split(",") if i.strip()],
        "work_style":        work_style,
        "comp_target":       comp_target.replace(",", "").replace("$", "").strip(),
        "calibration":       calibration.strip(),
    }


def _format_user_context(prefs: dict) -> str:
    """Format collected preferences into a structured block Claude ingests during extraction."""
    lines = ["--- USER-PROVIDED PREFERENCES (override inferred values) ---", ""]

    lines.append("# Hard filters")
    if prefs.get("target_roles"):
        lines.append(f"Target roles: {', '.join(prefs['target_roles'])}")
    lines.append(f"Work mode: {prefs.get('work_mode', 'No preference')}")
    if prefs.get("relocation_cities"):
        lines.append(f"Open to relocation: yes — {', '.join(prefs['relocation_cities'])}")
    else:
        lines.append("Open to relocation: no")
    if prefs.get("deal_breakers"):
        lines.append(f"Deal-breakers (cap score ≤30 if matched): {', '.join(prefs['deal_breakers'])}")
    if prefs.get("salary_floor"):
        lines.append(f"Salary floor (hard): ${prefs['salary_floor']}")

    lines.append("")
    lines.append("# Aspirations")
    if prefs.get("track"):
        lines.append(f"Career track: {prefs['track']}")
    if prefs.get("direction"):
        lines.append(f"Career direction: {prefs['direction']}")
    if prefs.get("company_types"):
        lines.append(f"Drawn to company types: {', '.join(prefs['company_types'])}")

    lines.append("")
    lines.append("# Soft preferences (adjust ranking, not hard filters)")
    if prefs.get("company_stage"):
        lines.append(f"Company stage preference: {', '.join(prefs['company_stage'])}")
    if prefs.get("industry_targets"):
        lines.append(f"Industries to move toward: {', '.join(prefs['industry_targets'])}")
    if prefs.get("work_style"):
        lines.append(f"Work style: {prefs['work_style']}")
    if prefs.get("comp_target"):
        lines.append(f"Total comp target: ${prefs['comp_target']}")

    if prefs.get("calibration"):
        lines.append("")
        lines.append(f"# Scoring calibration")
        lines.append(prefs["calibration"])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_init(api_key: str, resume_path_arg: str = "", supplement_path_arg: str = ""):
    try:
        import questionary
        from questionary import Style as QStyle
        from rich.console import Console
        from rich.panel import Panel
        from rich import box as rich_box
    except ImportError as e:
        sys.exit(
            f"❌  Missing dependency: {e}\n"
            f"    Run: pip install questionary rich"
        )

    # Consistent green/gray palette matching scorerole's visual identity
    Q_STYLE = QStyle([
        ("qmark",       "fg:#57a55a bold"),   # the leading ?
        ("question",    "bold"),
        ("answer",      "fg:#57a55a bold"),   # typed / confirmed answer
        ("pointer",     "fg:#57a55a bold"),   # ❯ in select menus
        ("highlighted", "fg:#57a55a bold"),   # hovered option
        ("selected",    "fg:#57a55a"),        # checked option
        ("instruction", "fg:#6c6c6c"),        # (Use arrow keys) hint
        ("separator",   "fg:#6c6c6c"),
    ])

    console = Console()

    # ── Welcome ──────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        "[bold]Let's build your scorerole profile![/bold]\n\n"
        "The more context you provide, the better we can filter and\n"
        "score roles against your background.\n\n"
        "  [dim]1.[/dim]  Point us to your resume (PDF, DOCX, or TXT)\n"
        "  [dim]2.[/dim]  Optionally add your LinkedIn profile\n"
        "  [dim]3.[/dim]  Tell us about your aspirations, achievements,\n"
        "       deal-breakers, and preferences\n"
        "  [dim]4.[/dim]  Review and tweak the final profile before saving\n\n"
        "[dim]It takes about 2 mins.  Run `scorerole init` anytime to update.[/dim]",
        border_style="dim",
        box=rich_box.ROUNDED,
        padding=(1, 3),
    ))

    # ── Step 1: Resume ───────────────────────────────────────────────────────
    console.print()
    console.print("[dim]  Step 1 of 4 — Resume[/dim]")
    console.print("[dim]  ─────────────────────[/dim]")
    console.print()
    console.print("  [dim]Accepted: PDF, DOCX, TXT/MD"
                  "   ·   Tip: drag the file into this window to paste its path.[/dim]\n")

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
    console.print(f"\n  [green]✓[/green]  {resume_path.name} "
                  f"[dim]({len(resume_text):,} characters)[/dim]\n")

    # ── Step 2: LinkedIn (optional) ──────────────────────────────────────────
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
            raw = questionary.path(
                "  Path to LinkedIn PDF (Enter to skip):", style=Q_STYLE
            ).ask()
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
            console.print(f"\n  [green]✓[/green]  {supp_path.name} "
                          f"[dim]({len(supp_text):,} characters)[/dim]")

    full_text = resume_text
    if supp_text:
        full_text += "\n\n--- SUPPLEMENTARY PROFILE ---\n\n" + supp_text

    # ── Step 3: Preferences ──────────────────────────────────────────────────
    prefs = _collect_preferences(console, Q_STYLE)
    user_context = _format_user_context(prefs)

    # ── Step 4: Extract + review ─────────────────────────────────────────────
    console.print()
    console.print("[dim]  Step 4 of 4 — Build your profile[/dim]")
    console.print("[dim]  ──────────────────────────────────[/dim]")
    console.print()

    try:
        import yaml
    except ImportError:
        sys.exit("❌  pyyaml not installed. Run: pip install pyyaml")

    with console.status("  [dim]Analyzing your resume with Claude…[/dim]"):
        try:
            profile = _extract_with_claude(api_key, full_text, user_context)
        except Exception as e:
            sys.exit(f"❌  Extraction failed: {e}")

    console.print("  [green]✓[/green]  Extraction complete\n")
    _show_profile(profile, console)
    console.print()

    # ── Review loop ───────────────────────────────────────────────────────────
    while True:
        action = questionary.select(
            "  Looks good?",
            choices=[
                questionary.Choice("Save profile", value="save"),
                questionary.Choice("Edit target roles & level", value="roles"),
                questionary.Choice("Edit aspirations & career direction", value="asp"),
                questionary.Choice("Edit deal-breakers", value="dbs"),
                questionary.Choice("Edit soft preferences", value="prefs"),
                questionary.Choice("Edit green flags", value="gf"),
                questionary.Choice("Edit salary floor", value="salary"),
                questionary.Choice("Edit scoring notes", value="notes"),
                questionary.Choice("Re-run extraction", value="rerun"),
            ],
            style=Q_STYLE,
        ).ask()

        if action is None or action == "save":
            break

        elif action == "roles":
            t = profile.setdefault("target", {})
            current_roles = ", ".join(t.get("roles", []))
            current_level = t.get("level", "")
            new_roles = questionary.text(
                "  Target roles:",
                default=current_roles,
                instruction="(comma-separated aspirational titles)",
                style=Q_STYLE,
            ).ask()
            if new_roles:
                t["roles"] = [r.strip() for r in new_roles.split(",") if r.strip()]
            new_level = questionary.select(
                "  Seniority level:",
                choices=["ic", "senior", "staff", "director", "vp", "c-suite"],
                default=current_level if current_level in
                        ["ic","senior","staff","director","vp","c-suite"] else "staff",
                style=Q_STYLE,
            ).ask()
            if new_level:
                t["level"] = new_level

        elif action == "asp":
            asp = profile.setdefault("aspirations", {})
            new_track = questionary.select(
                "  Career track:",
                choices=[
                    questionary.Choice("IC-focused", value="ic"),
                    questionary.Choice("Management", value="management"),
                    questionary.Choice("Flexible — open to both", value="flexible"),
                ],
                default=asp.get("track", "flexible"),
                style=Q_STYLE,
            ).ask()
            if new_track:
                asp["track"] = new_track
            new_dir = questionary.text(
                "  Career direction:",
                default=(asp.get("direction") or "").strip(),
                instruction="(2-3 sentences — where you want to go and why)",
                style=Q_STYLE,
            ).ask()
            if new_dir is not None:
                asp["direction"] = new_dir.strip()
            new_co = questionary.text(
                "  Company types drawn to:",
                default=", ".join(asp.get("company_types") or []),
                instruction="(comma-separated, e.g. AI-native startup, enterprise SaaS)",
                style=Q_STYLE,
            ).ask()
            if new_co is not None:
                asp["company_types"] = [c.strip() for c in new_co.split(",") if c.strip()]

        elif action == "dbs":
            current = ", ".join(profile.get("deal_breakers", []))
            console.print(
                "  [dim]Hard no's — any match caps the role score at ≤30.[/dim]\n"
            )
            new_val = questionary.text(
                "  Deal-breakers:", default=current, style=Q_STYLE
            ).ask()
            if new_val is not None:
                profile["deal_breakers"] = [
                    d.strip() for d in new_val.split(",") if d.strip()
                ]

        elif action == "prefs":
            pref = profile.setdefault("preferences", {})
            new_stage = questionary.checkbox(
                "  Company stage (select all that appeal):",
                choices=["Seed / Series A", "Growth (Series B–D)", "Late-stage / pre-IPO", "Public / enterprise"],
                default=pref.get("company_stage") or [],
                style=Q_STYLE,
            ).ask()
            if new_stage is not None:
                pref["company_stage"] = new_stage
            new_ws = questionary.select(
                "  Work style:",
                choices=[
                    "Deep IC / individual focus",
                    "Cross-functional leadership",
                    "Player-coach (both)",
                    "No preference",
                ],
                default=pref.get("work_style", "No preference"),
                style=Q_STYLE,
            ).ask()
            if new_ws:
                pref["work_style"] = new_ws
            new_ind = questionary.text(
                "  Industries to move toward:",
                default=", ".join(pref.get("industry_targets") or []),
                instruction="(may differ from past — comma-separated)",
                style=Q_STYLE,
            ).ask()
            if new_ind is not None:
                pref["industry_targets"] = [i.strip() for i in new_ind.split(",") if i.strip()]

        elif action == "gf":
            current = ", ".join(profile.get("green_flags", []))
            console.print(
                "  [dim]Role / company signals that boost your score when matched.[/dim]\n"
            )
            new_val = questionary.text(
                "  Green flags:", default=current, style=Q_STYLE
            ).ask()
            if new_val is not None:
                profile["green_flags"] = [
                    g.strip() for g in new_val.split(",") if g.strip()
                ]

        elif action == "salary":
            current = str(profile.get("salary_floor_usd") or "")
            new_val = questionary.text(
                "  Minimum salary (USD, numbers only):", default=current, style=Q_STYLE
            ).ask()
            if new_val:
                try:
                    profile["salary_floor_usd"] = int(
                        new_val.replace(",", "").replace("$", "")
                    )
                except ValueError:
                    console.print("  [red]Could not parse — keeping current value.[/red]")

        elif action == "notes":
            current = (profile.get("notes") or "").strip()
            console.print(
                "  [dim]e.g. 'Title understates scope — weight impact over title level.'[/dim]\n"
            )
            new_val = questionary.text(
                "  Scoring notes:", default=current, style=Q_STYLE
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
            questionary.Choice("Open .env in editor", value="env"),
            questionary.Choice("Done", value="exit"),
        ],
        style=Q_STYLE,
    ).ask()

    if next_action == "profile":
        open_in_editor(PROFILE_PATH)
    elif next_action == "env":
        env_example = Path(__file__).parent.parent / ".env.example"
        target = env_example if env_example.exists() else PROFILE_PATH.parent
        open_in_editor(target)

    console.print(
        "\n  [dim]Run `scorerole init` any time to update your profile.[/dim]\n"
        "  [dim]Run `scorerole config profile` to open it in your editor.[/dim]\n"
    )
