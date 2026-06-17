"""scorerole init — interactive profile setup wizard.

Uses questionary for prompts and Rich for formatted output.
Parses a resume (PDF / DOCX / TXT), optionally a LinkedIn export,
collects preferences interactively, extracts a structured profile
with Claude, lets the user review and edit, then saves to
~/.job_pipeline/profile.yaml.
"""
import os, re, sys, shutil, subprocess, logging
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

    loc_pref = c.get("location_preference")
    if loc_pref:
        _lbl = {"remote": "remote only", "local": "local / open to onsite", "flexible": "flexible"}
        remote_str = _lbl.get(loc_pref, loc_pref)
    else:
        remote_str = "yes" if c.get("open_to_remote") else "no"
    reloc = c.get("open_to_relocation") or []
    if reloc:
        remote_str += f"  [dim]· relocation: {', '.join(reloc)}[/dim]"
    tbl.add_row("Location", remote_str)

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
    print("  Location preference:")
    location_preference = questionary.select(
        "  Where do you need to work?",
        choices=[
            questionary.Choice("Remote — not willing to relocate",               value="remote"),
            questionary.Choice("Local or willing to relocate — open to onsite",  value="local"),
            questionary.Choice("Flexible — either works",                        value="flexible"),
        ],
        style=Q_STYLE,
    ).ask() or "flexible"

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
    print("  Min. base salary: enter a number like 150000 or 150,000  (leave blank to skip)")
    salary_floor = ""
    while True:
        raw_salary = questionary.text("  Min. base salary (USD):", style=Q_STYLE).ask() or ""
        cleaned = raw_salary.replace(",", "").replace("$", "").strip()
        if not cleaned:
            break
        # Reject shorthand like "150k" — int() would silently fail downstream
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
            print(f"  ⚠  Couldn't parse '{raw_salary}' as a number. "
                  f"Try: 150000 or 150,000  (leave blank to skip)")

    return {
        "target_roles":        [r.strip() for r in target_roles.split(",") if r.strip()],
        "location_preference": location_preference,
        "relocation_cities":   relocation,
        "deal_breakers":       [d.strip() for d in deal_breakers.split(",") if d.strip()],
        "salary_floor":        salary_floor,
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
    print("  Domain flexibility: how should scorerole treat roles outside your core domain?")
    domain_flex = questionary.select(
        "  Domain experience gaps:",
        choices=[
            questionary.Choice(
                "Flexible — credit transferable skills; domain gaps are friction, not dealbreakers",
                value="flexible",
            ),
            questionary.Choice(
                "Moderate — penalise significant domain gaps but don't disqualify",
                value="moderate",
            ),
            questionary.Choice(
                "Strict — only score highly if I have direct domain experience",
                value="strict",
            ),
        ],
        style=Q_STYLE,
    ).ask() or "moderate"

    print()
    print("  Anything else Claude should know? e.g. 'Staff-scope despite Senior title'")
    calibration = questionary.text("  Anything else:", style=Q_STYLE).ask() or ""

    return {
        "company_stage":    company_stage,
        "industry_targets": [i.strip() for i in industry_targets.split(",") if i.strip()],
        "domain_flex":      domain_flex,
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
    _loc_str   = {"remote": "remote only", "local": "local/onsite OK", "flexible": "flexible"}.get(hard.get("location_preference", "flexible"), "flexible")
    _sal_str   = f"  ·  ${hard['salary_floor']} floor" if hard.get("salary_floor") else ""
    _db_str    = f"  ·  {len(hard['deal_breakers'])} deal-breaker{'s' if len(hard['deal_breakers']) != 1 else ''}" if hard.get("deal_breakers") else ""
    print()
    print(f"  ✓  Hard filters  ·  {_roles_str or '—'}  ·  {_loc_str}{_sal_str}{_db_str}")

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
    _loc = prefs.get("location_preference", "flexible")
    _loc_labels = {"remote": "remote only", "local": "local / open to onsite", "flexible": "flexible"}
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
        cand["location_preference"] = loc_pref
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

def _run_proactive_sources_wizard(profile: dict, console, Q_STYLE=None):
    """Wizard step that configures proactive company scraping in profile['proactive_sources']."""
    try:
        import questionary
    except ImportError:
        return  # non-interactive env; skip silently

    from .sources.proactive import count_companies, estimate_monthly_cost

    n_sa   = count_companies(["S", "A"])
    n_sab  = count_companies(["S", "A", "B"])
    n_all  = count_companies(["S", "A", "B", "C"])
    cost_sa  = estimate_monthly_cost(["S", "A"])
    cost_sab = estimate_monthly_cost(["S", "A", "B"])

    target_roles = ", ".join(profile.get("target", {}).get("roles", ["your target role"]))
    existing = profile.get("proactive_sources", {})

    console.print()
    console.rule("[dim]Proactive job sources[/dim]")
    console.print()
    console.print(
        f"  Beyond LinkedIn alerts, scorerole can check company career pages directly each run.\n"
        f"  Currently [bold]{n_all}[/bold] companies are available across 4 tiers (Anthropic, Figma, Stripe…)\n"
        f"  Based on your profile, we'll filter for: [italic]{target_roles}[/italic]"
    )
    console.print()

    choices = [
        questionary.Choice(
            f"S + A tier only  ({n_sa} companies, +{cost_sa} est.)",
            value="SA",
        ),
        questionary.Choice(
            f"S + A + B tier   ({n_sab} companies, +{cost_sab} est.)  — broader coverage",
            value="SAB",
        ),
        questionary.Choice(
            "Add specific companies  (you pick from a list or enter names)",
            value="custom",
        ),
        questionary.Choice(
            "Other  (describe what you want — free text)",
            value="other",
        ),
        questionary.Choice(
            "Skip  (LinkedIn alerts only, no extra cost)",
            value="skip",
        ),
    ]

    # Pre-select based on existing config
    default_val = "skip"
    if existing.get("enabled"):
        tiers = set(existing.get("tiers", []))
        if "B" in tiers:
            default_val = "SAB"
        elif existing.get("extra_companies"):
            default_val = "custom"
        else:
            default_val = "SA"

    answer = questionary.select(
        "  Which companies should we check each run?",
        choices=choices,
        default=next((c for c in choices if c.value == default_val), choices[0]),
    ).ask()

    if answer is None or answer == "skip":
        profile["proactive_sources"] = {"enabled": False}
        console.print("  [dim]Skipped — LinkedIn alerts only. Run `scorerole init` any time to change this.[/dim]")
        return

    if answer == "SA":
        profile["proactive_sources"] = {
            "enabled": True,
            "tiers": ["S", "A"],
            "extra_companies": [],
            "exclude_companies": [],
        }
        console.print(f"  [green]✓[/green]  Proactive sources enabled: S + A tier ({n_sa} companies)")

    elif answer == "SAB":
        profile["proactive_sources"] = {
            "enabled": True,
            "tiers": ["S", "A", "B"],
            "extra_companies": [],
            "exclude_companies": [],
        }
        console.print(f"  [green]✓[/green]  Proactive sources enabled: S + A + B tier ({n_sab} companies)")

    elif answer == "custom":
        _configure_custom_companies(profile, existing, console)

    elif answer == "other":
        freeform = questionary.text(
            "  Describe what you want (e.g. 'only Anthropic and Stripe', "
            "'all S-tier plus Notion and Ramp'):",
            style=Q_STYLE,
        ).ask()
        if freeform:
            # Store as a note and default to S+A — user can edit profile.yaml directly
            profile["proactive_sources"] = {
                "enabled": True,
                "tiers": ["S", "A"],
                "extra_companies": [],
                "exclude_companies": [],
                "notes": freeform.strip(),
            }
            console.print(
                f"  [green]✓[/green]  Saved your preference. Starting with S + A tier as baseline.\n"
                f"  [dim]Edit [bold]proactive_sources[/bold] in your profile.yaml to fine-tune, "
                f"or run `scorerole init` again.[/dim]"
            )

    console.print(
        "  [dim]You can always reconfigure proactive sources via `scorerole init`.[/dim]"
    )


def _configure_custom_companies(profile: dict, existing: dict, console):
    """Sub-wizard for the 'Add specific companies' path."""
    try:
        import questionary
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
        questionary.Choice(
            f"[{c['tier']}] {c['name']} ({c.get('ats', '?')})",
            value=c["name"],
            checked=(c.get("tier") in existing_tiers or c["name"] in existing_extras),
        )
        for c in sorted(all_companies, key=lambda x: (x.get("tier", "Z"), x.get("name", "")))
    ]

    selected = questionary.checkbox(
        "  Select companies to include (space to toggle, enter to confirm):",
        choices=choices,
    ).ask()

    if selected is None:
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
    console.print(f"  [green]✓[/green]  Proactive sources enabled: {len(selected)} companies selected")


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
                questionary.Choice("Quick edits   — jump to review menu, no re-extraction",  value="quick"),
                questionary.Choice("Open in editor — edit profile.yaml directly",             value="editor"),
                questionary.Choice("Start fresh   — new resume, full re-extraction",          value="full"),
                questionary.Choice("Exit",                                                    value="exit"),
            ],
            style=Q_STYLE,
        ).ask()

        if mode is None or mode == "exit":
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
        print("  Arrow keys to navigate  ·  Enter to select")
        try:
            action = questionary.select(
                "  Looks good?",
                choices=review_choices,
                style=Q_STYLE,
            ).ask()
        except KeyboardInterrupt:
            print()
            try:
                save_now = questionary.confirm(
                    "  Save profile before exiting?", default=True, style=Q_STYLE
                ).ask()
            except KeyboardInterrupt:
                save_now = False
            if save_now:
                break          # fall through to the save block below
            print("  Exited without saving.")
            sys.exit(0)

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
            print("  List your key strengths, separated by semicolons.")
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

    # ── Proactive sources ─────────────────────────────────────────────────────
    _run_proactive_sources_wizard(profile, console, Q_STYLE)

    # ── Save ──────────────────────────────────────────────────────────────────
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)  # restrict to owner
    PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))
    PROFILE_PATH.chmod(0o600)   # profile contains salary, deal-breakers — owner-only
    console.print(f"\n  [green]✓[/green]  Saved to [dim]{PROFILE_PATH}[/dim]\n")

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
        change = questionary.confirm(
            "  Update the schedule?", default=False, style=Q_STYLE
        ).ask()
        if change:
            run_schedule_wizard()
    else:
        console.print()
        setup_schedule = questionary.confirm(
            "  Set up automated digests? scorerole can email you on a schedule without manual runs.",
            default=True,
            style=Q_STYLE,
        ).ask()
        if setup_schedule:
            run_schedule_wizard()
        else:
            console.print(
                "  [dim]You can set this up later with: scorerole schedule --set[/dim]"
            )

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
        "\n  [dim]Run `scorerole init` any time to update your profile or schedule.[/dim]\n"
    )
