"""scorerole init — interactive profile setup wizard.

Uses InquirerPy for prompts and Rich for formatted output.
Parses a resume (PDF / DOCX / TXT), optionally a LinkedIn export,
collects preferences interactively, extracts a structured profile
with Claude, lets the user review and edit, then saves to
~/.job_pipeline/profile.yaml.
"""
import os, re, sys, shutil, subprocess, logging
from pathlib import Path
from rich.style import Style

from .theme import (
    QUESTIONARY_STYLE,
    INQUIRER_STYLE,
    THEME,
    console,
    print_hint,
    print_section,
    print_section_intro,
    print_eg,
    print_confirmed,
    print_separator,
    print_kb_hint,
)

log = logging.getLogger(__name__)

# Suppress HTTP request logs so they don't bleed into the Rich spinner
# or appear between questionary prompts.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("InquirerPy").setLevel(logging.WARNING)

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
# InquirerPy prompt helpers
#
# Spacing rule (Gemini / UX consensus):
#   WITHIN a question block  → 0 blank lines  (label, hint, input are one unit)
#   BETWEEN question blocks  → 1 blank line   (the \n prefix in each helper)
#
# Pointer: "  › " — 2 spaces + › + 1 space — fixed width so text and list
# prompts align horizontally regardless of prompt type.
#
# All helpers use INQUIRER_STYLE from theme.py (dark/light adaptive).
# ---------------------------------------------------------------------------

def _ask(label: str, hint: str = "", default: str = "", examples: str = "", **kw) -> str:
    """Single-line text input block.

    Label is flush left (typographic anchor). Hint and examples are 2-space
    indented to create a clear subordinate tier. One blank line is injected
    before the label via the leading newline — callers must NOT add extra spacing.
    """
    from InquirerPy import inquirer
    console.print(f"\n[bold]{label}[/bold]")
    if hint:
        print_hint(hint)
    if examples:
        print_eg(examples)
    return inquirer.text(
        message="  › ", default=default, style=INQUIRER_STYLE, multiline=False, **kw
    ).execute() or ""


def _ask_select(label: str, choices: list, hint: str = "", default=None, examples: str = "", **kw):
    """Single-selection list block.

    qmark and message are suppressed so InquirerPy doesn't render '? ›' before
    the choice list — that marker looks like a text input affordance, which is wrong.
    The label and hint printed above already set context; choices speak for themselves.
    """
    from InquirerPy import inquirer
    console.print(f"\n[bold]{label}[/bold]")
    if hint:
        print_hint(hint)
    if examples:
        print_eg(examples)
    return inquirer.select(
        message="", qmark="", choices=choices, default=default, style=INQUIRER_STYLE, **kw
    ).execute()


def _ask_checkbox(label: str, choices: list, hint: str = "Space to toggle  ·  Enter to confirm") -> list:
    """Multi-selection checkbox block. Choices may be strings or InquirerPy Choice objects."""
    from InquirerPy import inquirer
    console.print(f"\n[bold]{label}[/bold]")
    if hint:
        print_hint(hint)
    return inquirer.checkbox(message="", qmark="", choices=choices, style=INQUIRER_STYLE).execute() or []


def _ask_confirm(label: str, default: bool = True) -> bool:
    """Yes/No confirmation block."""
    from InquirerPy import inquirer
    console.print()
    return bool(inquirer.confirm(message=f"  › {label}", default=default, style=INQUIRER_STYLE).execute())


def _ask_filepath(label: str, hint: str = "", examples: str = "") -> str:
    """File-path input with tab-completion."""
    from InquirerPy import inquirer
    console.print(f"\n[bold]{label}[/bold]")
    if hint:
        print_hint(hint)
    if examples:
        print_eg(examples)
    return inquirer.filepath(message="  › ", style=INQUIRER_STYLE).execute() or ""


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
# LinkedIn URL scraper
# ---------------------------------------------------------------------------

def _scrape_linkedin_url(url: str, console) -> str:
    """Fetch a LinkedIn profile URL and return extracted plain text.

    Uses the li_at session cookie from .env. Returns empty string on any failure
    so the caller can continue without supplementary text.
    """
    li_at = os.getenv("LINKEDIN_COOKIE", "").strip()
    if not li_at:
        console.print(
            "  [{THEME['warning']}]⚠[/]  LINKEDIN_COOKIE not set in .env — skipping LinkedIn scrape.\n"
            "  [dim]Add li_at cookie value to .env to enable this.[/dim]\n"
        )
        return ""

    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError as _e:
        console.print(
            f"  [{THEME['warning']}]⚠[/]  Missing dep ({_e}) — run: pip install requests beautifulsoup4\n"
        )
        return ""

    # Normalise URL — strip trailing slash, query params
    url = url.split("?")[0].rstrip("/")
    if not url.startswith("http"):
        url = "https://" + url

    headers = {
        "cookie": f"li_at={li_at}",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "accept-language": "en-US,en;q=0.9",
    }

    try:
        with console.status("  [dim]Fetching LinkedIn profile…[/dim]"):
            resp = requests.get(url, headers=headers, timeout=15)
    except Exception as exc:
        console.print(f"  [{THEME['warning']}]⚠[/]  LinkedIn fetch failed ({exc}) — skipping.\n")
        return ""

    if resp.status_code == 401 or resp.status_code == 403:
        console.print(
            f"  [{THEME['warning']}]⚠[/]  LinkedIn returned {resp.status_code} — "
            "session cookie may be expired. Skipping.\n"
            "  [dim]Refresh LINKEDIN_COOKIE in .env (DevTools → Application → Cookies → li_at).[/dim]\n"
        )
        return ""
    if resp.status_code != 200:
        console.print(f"  [{THEME['warning']}]⚠[/]  LinkedIn returned {resp.status_code} — skipping.\n")
        return ""

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "img"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)

    if len(text) < 200:
        console.print(
            "  [{THEME['warning']}]⚠[/]  LinkedIn page returned very little text"
            "(may require browser login). Skipping.\n"
        )
        return ""

    return text[:8_000]   # cap at 8k chars — enough for extraction, stays within token budget


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

inferred:
  customer_types: []              # list from [b2b, b2c, b2b2c, marketplace, developer, internal]
                                  # inferred from past employers; [] if unclear
  degree_level: null              # "none" / "bs" / "ms_phd" — from education section; null if unclear

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

    _REQUIRED_SECTIONS = ("candidate", "target", "experience")

    try:
        result = yaml.safe_load(raw)
        if isinstance(result, dict):
            missing = [k for k in _REQUIRED_SECTIONS if k not in result]
            if missing:
                raise ValueError(
                    f"Claude-generated profile is missing required sections: {missing}. "
                    "Try re-running extraction."
                )
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
                missing = [k for k in _REQUIRED_SECTIONS if k not in result]
                if missing:
                    log.warning("Recovered profile is missing sections: %s — re-run extraction if scoring looks off", missing)
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

def _show_profile(profile: dict):
    from rich.table import Table
    from rich.panel import Panel
    from rich import box as rich_box

    c    = profile.get("candidate", {})
    t    = profile.get("target", {})
    asp  = profile.get("aspirations", {})
    pref = profile.get("preferences", {})
    scr  = profile.get("scoring", {})

    # expand=True makes the table fill available panel width so text wraps
    # instead of overflowing the terminal edge.
    tbl = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    tbl.add_column(style="dim", min_width=18, no_wrap=True, max_width=22)
    tbl.add_column(overflow="fold")

    def _sep(label: str):
        tbl.add_row(f"[dim]── {label}[/dim]", "")

    # ── Identity ────────────────────────────────────────────
    _sep("Identity")
    tbl.add_row("Name",     c.get("name", "—"))
    tbl.add_row("Location", c.get("location", "—"))
    loc_pref = c.get("location_preference")
    if loc_pref:
        _lbl = {
            "remote":   "remote only",
            "local":    "local / open to onsite",
            "relocate": "open to relocation",
            "flexible": "flexible",
        }
        remote_str = _lbl.get(loc_pref, loc_pref)
    else:
        remote_str = "remote OK" if c.get("open_to_remote") else "onsite"
    reloc = c.get("open_to_relocation") or []
    if reloc:
        remote_str += f"  [dim]· relocation: {', '.join(reloc)}[/dim]"
    tbl.add_row("Work pref", remote_str)

    # ── Target ──────────────────────────────────────────────
    _sep("Target")
    tbl.add_row("Roles",    ", ".join(t.get("roles", [])) or "—")
    tbl.add_row("Level",    t.get("level", "—"))
    if asp.get("track"):
        tbl.add_row("Track", asp["track"])
    if asp.get("direction"):
        first = asp["direction"].strip().splitlines()[0][:80]
        tbl.add_row("Direction", first + ("[dim]…[/dim]" if len(asp["direction"]) > 80 else ""))
    if pref.get("industry_targets"):
        tbl.add_row("Industries", ", ".join(pref["industry_targets"]))
    if pref.get("company_stage"):
        tbl.add_row("Stage pref", ", ".join(pref["company_stage"]))

    # ── Constraints ─────────────────────────────────────────
    _sep("Constraints")
    dbs = profile.get("deal_breakers", [])
    tbl.add_row("Deal-breakers", "\n".join(dbs) if dbs else "[dim]none set[/dim]")
    salary = profile.get("salary_floor_usd")
    if salary:
        tbl.add_row("Salary floor", f"${salary:,}")

    # ── Strengths ────────────────────────────────────────────
    strengths = profile.get("strengths", [])
    if strengths:
        _sep("Strengths")
        s_text = "\n".join(strengths[:3])
        if len(strengths) > 3:
            s_text += f"\n[dim]… and {len(strengths)-3} more[/dim]"
        tbl.add_row("Strengths", s_text)

    # ── Green flags / environment ─────────────────────────────
    gf = profile.get("green_flags", [])
    co = asp.get("company_types") or []
    if gf or co:
        _sep("Green flags")
        if co:
            tbl.add_row("Drawn to", "\n".join(co))
        if gf:
            gf_text = "\n".join(gf[:2])
            if len(gf) > 2:
                gf_text += f"\n[dim]… and {len(gf)-2} more[/dim]"
            tbl.add_row("Boost signals", gf_text)

    # ── Gaps ─────────────────────────────────────────────────
    yf = profile.get("yellow_flags", [])
    rf = profile.get("red_flags", [])
    if yf or rf:
        _sep("Gaps")
        if yf:
            tbl.add_row("Yellow flags", "\n".join(yf[:2]) + (f"\n[dim]… and {len(yf)-2} more[/dim]" if len(yf) > 2 else ""))
        if rf:
            tbl.add_row("Red flags",    "\n".join(rf[:2]) + (f"\n[dim]… and {len(rf)-2} more[/dim]" if len(rf) > 2 else ""))

    # ── Scoring config ───────────────────────────────────────
    if scr:
        _sep("Scoring config")
        if scr.get("apply_threshold") is not None:
            tbl.add_row("Apply ≥",    str(scr["apply_threshold"]))
        if scr.get("consider_threshold") is not None:
            tbl.add_row("Consider ≥", str(scr["consider_threshold"]))

    # ── Scoring overrides ────────────────────────────────────
    notes = (profile.get("notes") or "").strip()
    if notes:
        _sep("Scoring overrides")
        first_line = notes.splitlines()[0][:80]
        tbl.add_row("Overrides", first_line + ("[dim]…[/dim]" if len(notes) > len(first_line) else ""))

    console.print(Panel(
        tbl,
        title="[bold]Extracted profile[/bold]",
        subtitle="[dim]review below — nothing saved yet[/dim]",
        border_style="dim",
        box=rich_box.ROUNDED,
        padding=(1, 2),
    ))


# ---------------------------------------------------------------------------
# Preferences collection — Step 3
# Three focused sub-functions, merged by _collect_preferences.
#
# All prompts use the _ask/_ask_select/_ask_checkbox helpers defined above,
# which enforce the spacing rule (0 blank lines within a block, 1 between)
# and consistent "  › " pointer alignment.
# ---------------------------------------------------------------------------

def _collect_goals(Q_STYLE=None) -> dict:
    """Step 2 — what you want: target roles, career path, direction, domain, stage."""
    from InquirerPy.base.control import Choice as IChoice

    target_roles = _ask(
        "Target roles",
        hint="Enter role titles you're targeting next, separated by commas.",
        examples="Staff Content Designer, Principal Content Designer, Developer Experience Lead",
    )

    track = _ask_select(
        "Career track",
        hint="Select the path you want scorerole to favor.",
        choices=[
            IChoice(name="Senior IC — Staff / Principal / Distinguished", value="ic"),
            IChoice(name="Management — Director / VP / Head of",          value="management"),
            IChoice(name="Flexible — evaluate both IC and management roles", value="flexible"),
        ],
    ) or "flexible"

    direction = _ask(
        "Career aspiration",
        hint="1–3 sentences on the kind of work you want next.  Press Enter to skip.",
        examples="Developer platforms for AI products — CLI, API, SDK, docs systems.",
    )

    industry_targets = _ask(
        "Preferred industries",
        hint="Enter industries scorerole should prioritize.  Use commas for multiple.  Press Enter to skip.",
        examples="healthcare, developer tools, AI infrastructure",
    )

    company_stage = _ask_checkbox(
        "Preferred company stage",
        hint="Used for ranking, not exclusion.  Space to toggle  ·  Enter to confirm",
        choices=["Seed / Series A", "Growth (Series B–D)", "Late-stage / pre-IPO", "Public / enterprise"],
    )

    domain_flex = _ask_select(
        "Domain match weighting",
        hint="Choose how strongly scorerole should penalize roles outside your past domains.",
        choices=[
            IChoice(name="Transferable — adjacent experience counts strongly; small penalty for domain gaps", value="flexible"),
            IChoice(name="Balanced — domain gaps matter, but strong role fit can compensate  (default)",     value="moderate"),
            IChoice(name="Specialist — prioritize close domain matches; larger penalty for gaps",             value="strict"),
        ],
    ) or "moderate"

    return {
        "target_roles":     [r.strip() for r in target_roles.split(",") if r.strip()],
        "track":            track,
        "direction":        direction.strip(),
        "industry_targets": [i.strip() for i in industry_targets.split(",") if i.strip()],
        "company_types":    [],  # inferred from direction + stage, not asked explicitly
        "company_stage":    company_stage,
        "domain_flex":      domain_flex,
    }


def _collect_hard_constraints(Q_STYLE=None) -> dict:
    """Step 3 — hard constraints: location and minimum salary."""
    from InquirerPy.base.control import Choice as IChoice

    location_preference = _ask_select(
        "Where do you prefer to work?",
        choices=[
            IChoice(name="Remote — no office required, not open to relocation",   value="remote"),
            IChoice(name="Local — onsite or hybrid in my current city",            value="local"),
            IChoice(name="Open to relocating — willing to move for the right role", value="relocate"),
            IChoice(name="Flexible — any arrangement works",                       value="flexible"),
        ],
    ) or "flexible"

    relocation: list[str] = []
    if location_preference in ("relocate", "flexible"):
        relocation = _ask_checkbox(
            "Open to relocating to",
            choices=_RELOCATION_CITIES,
        )

    salary_floor = ""
    while True:
        raw_salary = _ask(
            "Min. base salary (USD)",
            "Enter a number like 150000 or 150k  ·  leave blank to skip",
        )
        cleaned = raw_salary.replace(",", "").replace("$", "").strip()
        if not cleaned:
            break
        if cleaned.lower().endswith("k"):
            try:
                salary_floor = str(int(float(cleaned[:-1]) * 1000))
                break
            except ValueError:
                pass
        try:
            salary_floor = str(int(cleaned))
            break
        except ValueError:
            console.print(f"  [{THEME['warning']}]⚠[/]  Couldn't parse '{raw_salary}' as a number. "
                          "Try: 150000 or 150k  (leave blank to skip)")

    return {
        "location_preference": location_preference,
        "relocation_cities":   relocation,
        "salary_floor":        salary_floor,
    }


def _collect_deal_breakers(Q_STYLE=None) -> dict:
    """Step 3c — absolute walk-away rules."""
    deal_breakers = _ask(
        "Deal-breakers",
        "Comma-separated  ·  e.g. no equity, on-site 5d/wk, no AI/ML surface",
    )
    return {
        "deal_breakers": [d.strip() for d in deal_breakers.split(",") if d.strip()],
    }


def _collect_overrides(Q_STYLE) -> dict:
    """Scoring overrides — skipped in the main wizard flow; available in the review menu."""
    return {"calibration": ""}


def _collect_preferences(Q_STYLE) -> dict:
    """Orchestrate preference collection: what you want → hard constraints → deal-breakers."""
    console.print()
    print_section("Step 2 of 4", "Target roles + aspirations", "— what you want")
    console.print()
    print_section_intro(
        "Your resume and LinkedIn show your past experience and competencies. "
        "This section captures your next-step goals so scorerole can rank roles "
        "based on what you want, not only what you've already done.",
        ctrl_hint=True,
    )
    console.print()  # double blank before first question

    try:
        goals = _collect_goals(Q_STYLE)
        _track_label = {"ic": "IC track", "management": "Management", "flexible": "Flexible"}.get(goals["track"], goals["track"])
        _roles_str   = ", ".join(goals["target_roles"][:2]) + ("…" if len(goals["target_roles"]) > 2 else "")
        _ind_str     = ", ".join(goals["industry_targets"][:2]) + ("…" if len(goals["industry_targets"]) > 2 else "") if goals["industry_targets"] else ""
        console.print()
        print_confirmed("What you want", f"{_roles_str or '—'}  ·  {_track_label}", _ind_str)
        console.print()  # success sandwich — buffer before next section
        print_separator()

        print_section("Step 3 of 4", "Constraints + deal-breakers", "— what to exclude")
        console.print()
        print_section_intro(
            "Hard filters — roles below these thresholds are excluded, not just ranked lower.",
        )
        console.print()  # double blank before first question
        constraints = _collect_hard_constraints(Q_STYLE)
        _loc_str = {"remote": "remote only", "local": "local/onsite OK", "relocate": "open to relocation", "flexible": "flexible"}.get(constraints["location_preference"], "flexible")
        _sal_meta = f"${constraints['salary_floor']} floor" if constraints.get("salary_floor") else ""
        console.print()
        print_confirmed("Hard constraints", _loc_str, _sal_meta)

        dbs = _collect_deal_breakers(Q_STYLE)
        _db_n = len(dbs["deal_breakers"])
        print_confirmed("Deal-breakers", f"{_db_n} rule{'s' if _db_n != 1 else ''}" if _db_n else "none")
        console.print()  # success sandwich before next section

        overrides = _collect_overrides(Q_STYLE)
        console.print()

    except KeyboardInterrupt:
        console.print()
        try:
            save_now = _ask_confirm(
                "Exit preferences early? (Extraction will use whatever you entered so far)",
                default=True,
            )
        except KeyboardInterrupt:
            save_now = True
        if not save_now:
            sys.exit(0)
        # Return whatever was collected — missing keys get safe defaults in _apply_prefs_to_profile
        collected = {}
        for d in [locals().get("goals", {}), locals().get("constraints", {}),
                  locals().get("dbs", {}), locals().get("overrides", {})]:
            collected.update(d)
        return collected

    return {**goals, **constraints, **dbs, **overrides}


def _format_user_context(prefs: dict) -> str:
    """Format collected preferences into a structured block Claude ingests."""
    lines = ["--- USER-PROVIDED PREFERENCES (override inferred values) ---", ""]

    lines.append("# Hard filters")
    if prefs.get("target_roles"):
        lines.append(f"Target roles: {', '.join(prefs['target_roles'])}")
    _loc = prefs.get("location_preference", "flexible")
    _loc_labels = {
        "remote":   "remote only — no relocation",
        "local":    "local / open to onsite in current city",
        "relocate": "open to relocation",
        "flexible": "flexible on location",
    }
    lines.append(f"Location preference: {_loc_labels.get(_loc, _loc)}")
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

    # Location preference — replaces the old work_mode checkbox.
    # Also derive open_to_remote for backward compat with legacy profile readers.
    loc_pref = prefs.get("location_preference")
    if loc_pref:
        cand = profile.setdefault("candidate", {})
        # "relocate" is the wizard value; normalise to "local" for profile storage
        # (profile.py renders "local" as "local / open to onsite")
        stored_pref = "local" if loc_pref == "relocate" else loc_pref
        cand["location_preference"] = stored_pref
        cand["open_to_remote"] = loc_pref in ("remote", "flexible")
    elif prefs.get("work_mode"):
        # Legacy path: old wizard ran and produced work_mode list
        work_mode = prefs["work_mode"]
        cand = profile.setdefault("candidate", {})
        cand["work_mode"] = work_mode
        on_site_only = ("On-site OK" in work_mode
                        and "Remote-first" not in work_mode
                        and "Hybrid OK" not in work_mode)
        cand["open_to_remote"] = not on_site_only

    # Relocation
    reloc = prefs.get("relocation_cities")
    if reloc is not None:
        profile.setdefault("candidate", {})["open_to_relocation"] = reloc

    # Deal-breakers (replace, not append)
    if prefs.get("deal_breakers") is not None:
        profile["deal_breakers"] = prefs["deal_breakers"]

    # Salary floor — salary_floor_usd is the single source of truth.
    # If Claude's extraction also wrote a salary mention into deal_breakers, remove it
    # so the two don't conflict (deal-breakers fire first and would override the explicit floor).
    if prefs.get("salary_floor"):
        try:
            floor = int(prefs["salary_floor"])
            profile["salary_floor_usd"] = floor
            _salary_re = re.compile(r"salary|compensation|pay|base", re.IGNORECASE)
            profile["deal_breakers"] = [
                d for d in profile.get("deal_breakers", [])
                if not _salary_re.search(str(d))
            ]
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

    # Domain flexibility — inject a scoring calibration note so Claude doesn't
    # over-penalise domain gaps when the user prefers transferable-skills weighting.
    _DOMAIN_FLEX_NOTES = {
        "flexible": (
            "Domain gaps are friction, not disqualifiers: deduct 5–15 points for missing "
            "domain experience, but do not drop the score below 'consider' on that basis alone. "
            "Credit transferable PM skills, technical depth, and adjacent domain experience at full weight."
        ),
        "moderate": (
            "Domain gaps are a soft signal: deduct up to 20 points for significant domain "
            "distance, but always credit transferable skills and technical background."
        ),
        "strict": (
            "Domain experience is important: apply a meaningful penalty (20–30 points) when "
            "the candidate lacks direct experience in the role's primary domain."
        ),
    }
    domain_flex = prefs.get("domain_flex", "")
    if domain_flex and domain_flex in _DOMAIN_FLEX_NOTES:
        flex_note = _DOMAIN_FLEX_NOTES[domain_flex]
        existing  = (profile.get("notes") or "").strip()
        # Replace any prior domain-flex note (line starting with "Domain gaps") before appending.
        existing_lines = [l for l in existing.splitlines() if not l.startswith("Domain gaps")]
        existing = "\n".join(existing_lines).strip()
        profile["notes"] = (existing + "\n\n" + flex_note).strip() if existing else flex_note

    # Extra free-text calibration notes — append to existing
    if prefs.get("calibration"):
        existing = (profile.get("notes") or "").strip()
        profile["notes"] = (existing + "\n\n" + prefs["calibration"]).strip() if existing else prefs["calibration"]


# ---------------------------------------------------------------------------
# Proactive sources wizard
# ---------------------------------------------------------------------------

def _run_proactive_sources_wizard(profile: dict, Q_STYLE=None):
    """Wizard step that configures proactive company scraping in profile['proactive_sources']."""
    try:
        from .sources.proactive import count_companies
    except (ImportError, ModuleNotFoundError) as e:
        log.debug("proactive sources not available (%s) — skipping wizard", e)
        return

    n_curated = count_companies()
    target_roles = ", ".join(profile.get("target", {}).get("roles", ["your target role"]))
    already_enabled = profile.get("proactive_sources", {}).get("enabled", False)

    console.print()
    console.rule("[dim]Company career pages[/dim]")
    console.print()
    console.print(
        f"  LinkedIn alerts are already included. scorerole can also check a curated set of\n"
        f"  company career pages directly each run, then score matching roles against your profile.\n"
        f"  ({n_curated} companies: Anthropic, Figma, Stripe, Databricks and more)\n"
        f"\n"
        f"  Roles will be filtered for: [italic]{target_roles}[/italic]"
    )
    console.print()

    answer = _ask_confirm(
        "Check curated company career pages too?",
        default=True,
    )

    if answer:
        profile["proactive_sources"] = {
            "enabled": True,
            "extra_companies": profile.get("proactive_sources", {}).get("extra_companies", []),
            "exclude_companies": profile.get("proactive_sources", {}).get("exclude_companies", []),
        }
        console.print(
            f"  [{THEME['success']}]✓[/]  Curated company sources enabled — {n_curated} companies.\n"
            f"  [dim]Manage sources anytime with: scorerole sources list / add / remove[/dim]"
        )
    else:
        profile["proactive_sources"] = {
            "enabled": False,
            "extra_companies": profile.get("proactive_sources", {}).get("extra_companies", []),
            "exclude_companies": profile.get("proactive_sources", {}).get("exclude_companies", []),
        }
        console.print(
            "  [dim]LinkedIn alerts only. Enable company sources anytime with: scorerole sources on[/dim]"
        )


def _configure_custom_companies(profile: dict, existing: dict):
    """Sub-wizard for the 'Add specific companies' path."""
    try:
        from InquirerPy.base.control import Choice as IChoice
    except ImportError:
        return

    from .sources.proactive import _load_companies_yml

    cfg = _load_companies_yml()
    all_companies = (
        cfg.get("greenhouse_companies", [])
        + cfg.get("lever_companies", [])
        + cfg.get("ashby_companies", [])
    )
    by_name = {c["name"]: c for c in all_companies}

    existing_tiers  = set(existing.get("tiers", []))
    existing_extras = {c.get("name", "") for c in (existing.get("extra_companies") or [])}

    choices = [
        IChoice(
            name=f"[{c['tier']}] {c['name']} ({c.get('ats', '?')})",
            value=c["name"],
            enabled=(c.get("tier") in existing_tiers or c["name"] in existing_extras),
        )
        for c in sorted(all_companies, key=lambda x: (x.get("tier", "Z"), x.get("name", "")))
    ]

    selected = _ask_checkbox(
        "Select companies to include",
        choices=choices,
    )

    if not selected:
        return

    known_names = set(selected)
    tiers_covered = {by_name[n]["tier"] for n in known_names if n in by_name}
    extra_names   = [n for n in known_names if n in by_name and by_name[n]["tier"] not in {"S", "A"}]

    # Derive tiers from selection — use tier list where possible, extras for one-offs
    tiers = list(tiers_covered & {"S", "A"}) or []
    extra_companies = [
        {"name": by_name[n]["name"], "ats": by_name[n].get("ats", "greenhouse"), "slug": by_name[n].get("slug", "")}
        for n in selected
        if n in by_name and by_name[n]["tier"] not in set(tiers)
    ]

    profile["proactive_sources"] = {
        "enabled": True,
        "tiers": tiers,
        "extra_companies": extra_companies,
        "exclude_companies": [],
    }
    console.print(f"  [{THEME['success']}]✓[/]  Proactive sources enabled: {len(selected)} companies selected")


# ---------------------------------------------------------------------------
# Main wizard
# ---------------------------------------------------------------------------

def run_init(api_key: str, resume_path_arg: str = "", supplement_path_arg: str = ""):
    try:
        import yaml
        from rich.panel import Panel
        from rich import box as rich_box
        from InquirerPy.base.control import Choice as IChoice
        from InquirerPy.separator import Separator as ISep
    except ImportError as e:
        sys.exit(f"❌  Missing dependency: {e}\n    Run: pip install InquirerPy rich pyyaml")

    cols = console.width
    if cols < 80:
        console.print(
            f"\n  Terminal too narrow (currently {cols} cols). "
            "Resize to at least 80 cols and rerun.\n"
        )
        sys.exit(1)

    Q_STYLE = QUESTIONARY_STYLE  # kept for schedule_cmd compatibility

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

        from rich.text import Text as _RText

        _body = _RText()
        if cand_name:
            _body.append(cand_name + "\n", style=Style(bold=True))
        _body.append("Last updated ", style=Style(color=THEME["muted"]))
        _body.append(mod_time, style=Style(color=THEME["bright"], bold=True))

        console.print()
        console.print(Panel(
            _body,
            title="[dim]scorerole profile[/dim]",
            border_style=Style(color=THEME["separator"]),
            box=rich_box.ROUNDED,
            padding=(1, 3),
        ))
        console.print()

        mode = _ask_select(
            "What do you want to do?",
            choices=[
                IChoice(name="Quick edits    — jump to review menu, no re-extraction", value="quick"),
                IChoice(name="Open in editor — edit profile.yaml directly",             value="editor"),
                IChoice(name="Start fresh    — new resume, full re-extraction",         value="full"),
                IChoice(name="Exit",                                                    value="exit"),
            ],
        )

        if mode is None or mode == "exit":
            sys.exit(0)

        if mode == "editor":
            open_in_editor(PROFILE_PATH)
            sys.exit(0)

        elif mode == "quick":
            profile = existing
            skip_extraction = True
            console.print()
            _show_profile(profile)
            console.print()

        # mode == "full" falls through to the full wizard below

    if not skip_extraction:
        # ── Welcome ──────────────────────────────────────────────────────────
        from rich.style import Style as _RStyle
        from .theme import THEME as _T
        console.print()
        _panel_width = min(88, console.width)
        console.print(Panel(
            "[bold]Let's build your scorerole profile![/bold]\n\n"
            "The more context you provide, the better scorerole can filter and score roles against your background.\n\n"
            f"  [{THEME['accent']} bold]1.[/]  [bold]Resume + LinkedIn[/bold]  [{THEME['dim']}]— who you are[/]\n"
            f"  [{THEME['accent']} bold]2.[/]  [bold]Target roles + aspirations[/bold]  [{THEME['dim']}]— what you want[/]\n"
            f"  [{THEME['accent']} bold]3.[/]  [bold]Constraints + deal-breakers[/bold]  [{THEME['dim']}]— what to exclude[/]\n"
            f"  [{THEME['accent']} bold]4.[/]  [bold]Scoring preferences[/bold]  [{THEME['dim']}]— how to rank tradeoffs[/]\n\n"
            f"[{THEME['dim']}]Takes about 5 mins.  Run `scorerole init` anytime to update.[/]",
            style=_RStyle(bgcolor=_T["accent_bg"]),
            border_style=_RStyle(color=_T["accent"]),
            box=rich_box.ROUNDED,
            padding=(1, 3),
            width=_panel_width,
        ))

        # ── Step 1 of 4: Resume + LinkedIn (who you are) ────────────────────
        console.print()
        print_section("Step 1 of 4", "Resume + LinkedIn", "— who you are")
        console.print()
        print_section_intro(
            "Upload your resume so scorerole can extract your experience, skills, and background. "
            "Adding your LinkedIn profile URL supplements this with endorsements and additional context.",
            ctrl_hint=True,
        )
        console.print()  # double blank before first question

        resume_path = None
        if resume_path_arg:
            p = Path(resume_path_arg).expanduser().resolve()
            if not p.exists():
                console.print(f"  [{THEME['error']}]File not found:[/] {resume_path_arg}\n")
            elif p.is_dir():
                console.print(f"  [{THEME['error']}]That's a folder, not a file:[/] {resume_path_arg}\n")
            else:
                resume_path = p

        while not resume_path:
            raw = _ask_filepath(
                "Path to your resume",
                "PDF, DOCX, or TXT  ·  Tip: drag the file into this window to paste its path",
            )
            if not raw:
                sys.exit(0)
            raw = raw.strip().strip("\"'").replace("\\ ", " ")

            # Detect "two paths pasted together" — common drag-and-drop mishap
            dupe = re.match(r'^(.+) \1$', raw)
            if dupe:
                console.print()
                console.print(
                    "[bold]File not found.[/bold]",
                    style=Style(color=THEME["error"]),
                )
                console.print(
                    f"  I read this as:\n    {raw}\n"
                    "  This looks like two paths pasted together. "
                    "Paste one full path and try again.",
                    style=Style(color=THEME["dim"], italic=True),
                )
                console.print()
                continue

            p = Path(raw).expanduser().resolve()
            if not p.exists():
                console.print()
                console.print("[bold]File not found.[/bold]", style=Style(color=THEME["error"]))
                console.print(
                    f"  I read this as:\n    {p}\n  Check the path and try again.",
                    style=Style(color=THEME["dim"], italic=True),
                )
                console.print()
            elif p.is_dir():
                console.print()
                console.print("[bold]That's a folder, not a file.[/bold]", style=Style(color=THEME["error"]))
                console.print(
                    "  Drag your resume file into the terminal window to paste its path.",
                    style=Style(color=THEME["dim"], italic=True),
                )
                console.print()
            else:
                resume_path = p

        resume_text = _parse_file(resume_path)
        console.print(
            f"\n  [{THEME['success']}]✓[/]  {resume_path.name} "
            f"[dim]({len(resume_text):,} characters)[/dim]"
        )

        # LinkedIn — single URL prompt, no y/N gate (gate caused the double-advance bug)
        print_separator()
        supp_text = ""
        if supplement_path_arg:
            # Legacy: --supplement flag still accepts a file path
            p = Path(supplement_path_arg).expanduser().resolve()
            if p.exists() and not p.is_dir():
                supp_text = _parse_file(p)
                console.print(f"  [{THEME['success']}]✓[/]  Supplement: {p.name} [dim]({len(supp_text):,} chars)[/dim]")
        else:
            linkedin_raw = _ask(
                "LinkedIn URL",
                hint="Paste your profile URL to add skills + endorsements.  Enter to skip.",
                examples="https://www.linkedin.com/in/your-name/",
            ).strip()
            if linkedin_raw:
                supp_text = _scrape_linkedin_url(linkedin_raw, console)
                if supp_text:
                    print_confirmed("LinkedIn", f"{len(supp_text):,} characters loaded")

        full_text = resume_text
        if supp_text:
            full_text += "\n\n--- SUPPLEMENTARY PROFILE ---\n\n" + supp_text
        console.print()

        # ── Steps 2–3 of 4: Preferences ─────────────────────────────────────
        print_separator()
        prefs = _collect_preferences(Q_STYLE)
        user_context = _format_user_context(prefs)

        # ── Step 4 of 4: Review and save ─────────────────────────────────────
        print_separator()
        console.print()
        print_section("Step 4 of 4", "Review and save")
        console.print()

        with console.status("  [dim]Analyzing your resume with Claude…[/dim]"):
            try:
                profile = _extract_with_claude(api_key, full_text, user_context)
            except Exception as e:
                sys.exit(f"\n❌  Extraction failed: {e}")

        console.print(f"  [{THEME['success']}]✓[/]  Extraction complete\n")
        _show_profile(profile)
        console.print()

    # ── Review loop ── menu mirrors _show_profile section order 1:1 ─────────
    _review_base = [
        IChoice(name="Save profile",                                              value="save"),
        ISep(),
        IChoice(name="  Identity          (name, location, work preference)",    value="identity"),
        IChoice(name="  Target            (roles, level, track, direction)",     value="goals"),
        IChoice(name="  Constraints       (deal-breakers, salary floor)",        value="constraints"),
        IChoice(name="  Strengths         (differentiators, proof points)",      value="strengths"),
        IChoice(name="  Green flags       (environment preferences, signals)",   value="gf"),
        IChoice(name="  Gaps              (yellow flags, honest risks)",         value="gaps"),
        IChoice(name="  Scoring config    (apply/consider thresholds)",          value="scoring_config"),
        IChoice(name="  Scoring overrides (edge cases, AI instructions)",        value="notes"),
    ]
    _review_rerun = [ISep(), IChoice(name="  Re-run AI extraction", value="rerun")] if full_text else []

    while True:
        console.print()
        print_kb_hint()
        try:
            action = _ask_select(
                "Looks good?",
                choices=_review_base + _review_rerun,
                hint="",
            )
        except KeyboardInterrupt:
            print()
            try:
                save_now = _ask_confirm("Save profile before exiting?", default=True)
            except KeyboardInterrupt:
                save_now = False
            if save_now:
                break          # fall through to the save block below
            console.print("  Exited without saving.")
            sys.exit(0)

        if action is None or action == "save":
            break

        elif action == "identity":
            cand = profile.setdefault("candidate", {})
            new_name = _ask("Name", default=cand.get("name", ""))
            if new_name:
                cand["name"] = new_name.strip()
            new_loc = _ask("Location", default=cand.get("location", ""))
            if new_loc is not None:
                cand["location"] = new_loc.strip()
            new_pref = _ask_select(
                "Work preference",
                choices=[
                    IChoice(name="Remote — no office required",                         value="remote"),
                    IChoice(name="Local — onsite or hybrid in current city",            value="local"),
                    IChoice(name="Open to relocating — willing to move for right role", value="relocate"),
                    IChoice(name="Flexible — any arrangement works",                    value="flexible"),
                ],
                default=cand.get("location_preference", "flexible"),
            )
            if new_pref:
                cand["location_preference"] = new_pref
                cand["open_to_remote"] = new_pref in ("remote", "flexible")

        elif action == "goals":
            t   = profile.setdefault("target", {})
            asp = profile.setdefault("aspirations", {})
            pref = profile.setdefault("preferences", {})

            new_roles = _ask("Target roles", "Comma-separated", default=", ".join(t.get("roles", [])))
            if new_roles:
                t["roles"] = [r.strip() for r in new_roles.split(",") if r.strip()]

            valid_levels = ["ic", "senior", "staff", "director", "vp", "c-suite"]
            new_level = _ask_select(
                "Seniority level",
                choices=valid_levels,
                default=t.get("level") if t.get("level") in valid_levels else "staff",
            )
            if new_level:
                t["level"] = new_level

            new_track = _ask_select(
                "Career track",
                choices=[
                    IChoice(name="IC-focused  (Staff / Principal / Distinguished)", value="ic"),
                    IChoice(name="Management  (Director / VP / C-suite)",           value="management"),
                    IChoice(name="Flexible — open to both",                         value="flexible"),
                ],
                default=asp.get("track", "flexible"),
            )
            if new_track:
                asp["track"] = new_track

            new_dir = _ask("Career direction", default=(asp.get("direction") or "").strip())
            if new_dir is not None:
                asp["direction"] = new_dir.strip()

            new_ind = _ask(
                "Industries to move toward",
                "Comma-separated",
                default=", ".join(pref.get("industry_targets") or []),
            )
            if new_ind is not None:
                pref["industry_targets"] = [i.strip() for i in new_ind.split(",") if i.strip()]

            current_stages = pref.get("company_stage") or []
            stage_opts = ["Seed / Series A", "Growth (Series B–D)", "Late-stage / pre-IPO", "Public / enterprise"]
            new_stage = _ask_checkbox(
                "Company stage",
                choices=[IChoice(name=s, value=s, enabled=(s in current_stages)) for s in stage_opts],
            )
            if new_stage is not None:
                pref["company_stage"] = new_stage

        elif action == "strengths":
            current_strengths = "; ".join(profile.get("strengths", []))
            new_val = _ask(
                "Strengths",
                "Semicolons between items  ·  Be specific: 'ML depth — 2k+ models, RLHF at DocuSign'",
                default=current_strengths,
            )
            if new_val is not None:
                profile["strengths"] = [s.strip() for s in new_val.split(";") if s.strip()]

        elif action == "gf":
            asp = profile.setdefault("aspirations", {})
            new_gf = _ask(
                "Boost signals",
                "Role/company signals that raise the score  ·  e.g. 'lean team, 0-1 scope'  ·  comma-separated",
                default=", ".join(profile.get("green_flags", [])),
            )
            if new_gf is not None:
                profile["green_flags"] = [g.strip() for g in new_gf.split(",") if g.strip()]
            new_co = _ask(
                "Company types you're drawn to",
                "e.g. AI-native startup, mission-driven enterprise  ·  comma-separated",
                default=", ".join(asp.get("company_types") or []),
            )
            if new_co is not None:
                asp["company_types"] = [c.strip() for c in new_co.split(",") if c.strip()]

        elif action == "gaps":
            current_yf = ", ".join(profile.get("yellow_flags", []))
            new_yf = _ask(
                "Yellow flags",
                "Honest gaps or risks  ·  Claude surfaces these; you can correct them  ·  comma-separated",
                default=current_yf,
            )
            if new_yf is not None:
                profile["yellow_flags"] = [y.strip() for y in new_yf.split(",") if y.strip()]

        elif action == "scoring_config":
            scoring = profile.setdefault("scoring", {})
            new_apply = _ask(
                "Apply threshold",
                "Minimum score to show in the apply tier  (default 75)",
                default=str(scoring.get("apply_threshold", 75)),
            )
            if new_apply:
                try:
                    scoring["apply_threshold"] = int(new_apply)
                except ValueError:
                    console.print("  [{THEME['warning']}]⚠[/]  Not a valid integer — keeping current value.")
            new_consider = _ask(
                "Consider threshold",
                "Minimum score for the consider tier  (default 55)",
                default=str(scoring.get("consider_threshold", 55)),
            )
            if new_consider:
                try:
                    scoring["consider_threshold"] = int(new_consider)
                except ValueError:
                    console.print("  [{THEME['warning']}]⚠[/]  Not a valid integer — keeping current value.")

        elif action == "constraints":
            cand = profile.setdefault("candidate", {})
            new_loc = _ask_select(
                "Where do you prefer to work?",
                choices=[
                    IChoice(name="Remote — no office required, not open to relocation",   value="remote"),
                    IChoice(name="Local — onsite or hybrid in my current city",            value="local"),
                    IChoice(name="Open to relocating — willing to move for the right role", value="relocate"),
                    IChoice(name="Flexible — any arrangement works",                       value="flexible"),
                ],
                default=cand.get("location_preference", "flexible"),
            )
            if new_loc:
                cand["location_preference"] = new_loc
                cand["open_to_remote"] = new_loc in ("remote", "flexible")
            current_sal = str(profile.get("salary_floor_usd") or "")
            new_sal = _ask(
                "Min. base salary (USD)",
                "Hard floor — roles below this are excluded, not ranked lower",
                default=current_sal,
            )
            if new_sal:
                try:
                    profile["salary_floor_usd"] = int(new_sal.replace(",", "").replace("$", ""))
                except ValueError:
                    console.print(f"  [{THEME['error']}]Could not parse — keeping current value.[/]")
            new_dbs = _ask(
                "Deal-breakers",
                "Comma-separated  ·  e.g. no equity, on-site 5d/wk, no AI/ML surface",
                default=", ".join(profile.get("deal_breakers", [])),
            )
            if new_dbs is not None:
                profile["deal_breakers"] = [d.strip() for d in new_dbs.split(",") if d.strip()]

        elif action == "notes":
            new_val = _ask(
                "Scoring overrides",
                "Injected into Claude's scoring prompt  ·  e.g. 'Staff-scope despite Senior title — weight scope over title'",
                default=(profile.get("notes") or "").strip(),
            )
            if new_val is not None:
                profile["notes"] = new_val.strip()

        elif action == "rerun":
            with console.status("  [dim]Re-running extraction…[/dim]"):
                try:
                    profile = _extract_with_claude(api_key, full_text, user_context)
                except Exception as e:
                    console.print(f"  [{THEME['error']}]Extraction failed: {e}[/]")
                    continue

        console.print()
        _show_profile(profile)
        console.print()

    # ── Proactive sources ─────────────────────────────────────────────────────
    _run_proactive_sources_wizard(profile, Q_STYLE)

    # ── Save ──────────────────────────────────────────────────────────────────
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)  # restrict to owner
    PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))
    PROFILE_PATH.chmod(0o600)   # profile contains salary, deal-breakers — owner-only
    console.print(f"\n  [{THEME['success']}]✓[/]  Saved to [dim]{PROFILE_PATH}[/dim]\n")

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
        change = _ask_confirm("Update the schedule?", default=False)
        if change:
            run_schedule_wizard()
    else:
        setup_schedule = _ask_confirm(
            "Set up automated digests? scorerole can email you on a schedule without manual runs.",
            default=True,
        )
        if setup_schedule:
            run_schedule_wizard()
        else:
            console.print(
                "  [dim]You can set this up later with: scorerole schedule set[/dim]"
            )

    console.print(
        "\n  [dim]Run [bold]scorerole[/bold] to fetch your first digest.[/dim]\n"
        "  [dim]Run [bold]scorerole init[/bold] any time to update your profile.[/dim]\n"
    )
