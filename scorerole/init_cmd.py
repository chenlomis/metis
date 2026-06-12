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

Given resume text (and optionally a LinkedIn export or supplementary notes),
extract the candidate's information and return ONLY valid YAML matching this
schema exactly — no markdown fences, no commentary, no extra keys:

candidate:
  name: string
  email: string or null
  location: "City, State"
  open_to_remote: bool
  open_to_relocation: []         # list of cities/regions, or empty

target:
  roles: []                      # infer from trajectory — e.g. ["Staff PM", "Principal PM"]
  level: string                  # "ic", "senior", "staff", "director", "vp", "c-suite"
  industries: []                 # inferred from background

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
green_flags: []                  # role/company types they'd love
yellow_flags: []                 # things to watch out for (honest)
red_flags: []                    # hard blockers
deal_breakers: []                # absolute no's
salary_floor_usd: int or null    # if inferable from location + seniority
notes: |
  Any important scoring calibration notes. Include a level-mismatch rule if
  the candidate's current title understates their actual scope.

Rules:
- Use null or [] when information is absent; never omit a key.
- Infer target roles and level from title trajectory, not just current title.
- Be honest in yellow_flags — surface real gaps or risks.
- Return ONLY the YAML block.
"""


def _extract_with_claude(api_key: str, text: str) -> dict:
    import anthropic, yaml

    client = anthropic.Anthropic(api_key=api_key)
    model  = os.getenv("MODEL", "claude-sonnet-4-6")

    msg = client.messages.create(
        model=model,
        max_tokens=2048,
        system=_EXTRACT_SYSTEM,
        messages=[{"role": "user", "content": text[:14_000]}],
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
    tbl.add_row("Remote",       "yes" if c.get("open_to_remote") else "no")
    tbl.add_row("Target roles", "\n".join(t.get("roles", [])) or "—")
    tbl.add_row("Level",        t.get("level", "—"))

    dbs = profile.get("deal_breakers", [])
    tbl.add_row("Deal-breakers", ("\n".join(dbs)) if dbs else "none set")

    strengths = profile.get("strengths", [])
    if strengths:
        preview = strengths[0]
        if len(strengths) > 1:
            preview += f"\n[dim]… and {len(strengths) - 1} more[/dim]"
        tbl.add_row("Strengths", preview)

    salary = profile.get("salary_floor_usd")
    if salary:
        tbl.add_row("Salary floor", f"${salary:,}")

    console.print(Panel(tbl, title="[bold green]Extracted profile[/bold green]",
                        subtitle="[dim]review below — nothing saved yet[/dim]",
                        border_style="green", box=rich_box.ROUNDED, padding=(1, 2)))


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
        "[bold]scorerole init[/bold]\n\n"
        "Sets up your profile so scorerole can match job listings to\n"
        "[italic]your[/italic] background, interests, and aspirations — not a generic template.\n\n"
        "  [dim]1.[/dim]  Provide your resume (PDF, DOCX, or TXT)\n"
        "  [dim]2.[/dim]  Claude extracts your profile\n"
        "  [dim]3.[/dim]  Review and adjust before anything is saved\n\n"
        "[dim]Takes about 2 minutes.  Run `scorerole init` any time to update.[/dim]",
        border_style="dim",
        box=rich_box.ROUNDED,
        padding=(1, 3),
    ))

    # ── Step 1: Resume ───────────────────────────────────────────────────────
    console.print()
    console.print("[dim]  Step 1 of 3 — Resume[/dim]")
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
    console.print("[dim]  Step 2 of 3 — LinkedIn (optional)[/dim]")
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

    # ── Step 3: Extract + review ─────────────────────────────────────────────
    console.print()
    console.print("[dim]  Step 3 of 3 — Build your profile[/dim]")
    console.print("[dim]  ──────────────────────────────────[/dim]")
    console.print()

    try:
        import yaml
    except ImportError:
        sys.exit("❌  pyyaml not installed. Run: pip install pyyaml")

    with console.status("  [dim]Analyzing your resume with Claude…[/dim]"):
        try:
            profile = _extract_with_claude(api_key, full_text)
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
                questionary.Choice("Edit target roles", value="roles"),
                questionary.Choice("Edit deal-breakers", value="dbs"),
                questionary.Choice("Edit salary floor", value="salary"),
                questionary.Choice("Re-run extraction", value="rerun"),
            ],
            style=Q_STYLE,
        ).ask()

        if action is None or action == "save":
            break

        elif action == "roles":
            current = ", ".join(profile.get("target", {}).get("roles", []))
            console.print("  [dim]Comma-separated.  e.g. Staff PM, Principal PM, Director of Product[/dim]\n")
            new_val = questionary.text("  Target roles:", default=current, style=Q_STYLE).ask()
            if new_val:
                profile.setdefault("target", {})["roles"] = [
                    r.strip() for r in new_val.split(",") if r.strip()
                ]

        elif action == "dbs":
            current = ", ".join(profile.get("deal_breakers", []))
            console.print("  [dim]Hard no's — roles matching these will be skipped.[/dim]")
            console.print("  [dim]e.g. no equity, on-site 5 days/week, no AI component[/dim]\n")
            new_val = questionary.text("  Deal-breakers:", default=current, style=Q_STYLE).ask()
            if new_val:
                profile["deal_breakers"] = [
                    d.strip() for d in new_val.split(",") if d.strip()
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

        elif action == "rerun":
            with console.status("  [dim]Re-running extraction…[/dim]"):
                try:
                    profile = _extract_with_claude(api_key, full_text)
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
