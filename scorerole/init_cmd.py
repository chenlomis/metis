"""scorerole init — interactive profile setup wizard.

Parses a resume (PDF / DOCX / TXT) and an optional supplementary file
(LinkedIn export, extra bio, etc.), uses Claude to extract a structured
candidate profile, asks a few follow-up questions, then writes the result
to ~/.job_pipeline/profile.yaml.

Usage (via pipeline.py):
    scorerole init [--resume PATH] [--supplement PATH]
"""
import os, sys, textwrap, logging
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
            sys.exit(
                "❌  pdfplumber is required to read PDFs.\n"
                "    Run: pip install pdfplumber"
            )
        with pdfplumber.open(path) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    if suffix in (".docx", ".doc"):
        try:
            import docx
        except ImportError:
            sys.exit(
                "❌  python-docx is required to read Word files.\n"
                "    Run: pip install python-docx"
            )
        doc = docx.Document(path)
        return "\n".join(p.text for p in doc.paragraphs)

    # TXT / MD / anything else
    return path.read_text(errors="replace")


# ---------------------------------------------------------------------------
# Claude extraction
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """\
You are a career profile extractor.

Given resume text (and optionally a LinkedIn export or supplementary notes),
extract the candidate's information and return ONLY valid YAML matching this schema
exactly — no markdown fences, no commentary, no extra keys:

candidate:
  name: string
  email: string or null
  location: "City, State"        # or "City, Country"
  open_to_remote: bool
  open_to_relocation: []         # list of cities/regions, or empty

target:
  roles: []                      # e.g. ["Staff PM", "Principal PM"]
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
    highlights: []               # 2-4 bullet points per role

education:
  - institution: string or null
    degree: string
    year: int or null

strengths: []                    # 6-10 items, each a concrete phrase with evidence
green_flags: []                  # role/company types they'd love
yellow_flags: []                 # things to watch out for
red_flags: []                    # hard blockers
deal_breakers: []                # absolute no's
salary_floor_usd: int or null    # if inferable
notes: |
  Any important scoring calibration notes (level rules, caveats, etc.)

Rules:
- Use null or [] when information is absent; never omit a key.
- Infer target roles and level from title trajectory, not just current title.
- green_flags / yellow_flags / red_flags should reflect the candidate's actual
  history and stated preferences, not generic advice.
- Return ONLY the YAML block.
"""


def _extract_with_claude(api_key: str, text: str) -> dict:
    """Ask Claude to parse resume text into a structured profile dict."""
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

    # Strip accidental markdown fences
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    return yaml.safe_load(raw)


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    try:
        val = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)
    return val if val else default


def _resolve_path(raw: str) -> Path | None:
    if not raw:
        return None
    p = Path(raw.strip()).expanduser().resolve()
    return p if p.exists() else None


def run_init(api_key: str, resume_path_arg: str = "", supplement_path_arg: str = ""):
    """Run the interactive init wizard."""
    print("\n✨  scorerole — profile setup\n")
    print(textwrap.dedent("""\
        This wizard will parse your resume (and optionally your LinkedIn export),
        use Claude to build a scoring profile, then let you review and adjust it.
        The profile is saved to ~/.job_pipeline/profile.yaml.
    """))

    # ── Step 1: resume ───────────────────────────────────────────────────────
    print("─" * 60)
    print("Step 1 of 3 — Resume")
    print("  Accepted: PDF, DOCX, TXT/MD")
    print("  Tip: drag the file into your terminal window to paste its path.\n")

    resume_path = _resolve_path(resume_path_arg)
    if not resume_path:
        raw = _ask("  Path to your resume: ")
        resume_path = _resolve_path(raw)
    if not resume_path:
        sys.exit("❌  Resume file not found.")

    resume_text = _parse_file(resume_path)
    print(f"  ✓  Parsed {len(resume_text):,} characters from {resume_path.name}\n")

    # ── Optional supplement ───────────────────────────────────────────────────
    print("  (Optional) LinkedIn export or additional bio/notes file")
    print("  LinkedIn tip: Settings → Data Privacy → Get a copy of your data")
    print("                → select 'Profile', wait for email, then download the PDF.\n")

    supp_path = _resolve_path(supplement_path_arg)
    if not supp_path:
        raw = _ask("  Path to supplement (Enter to skip): ")
        supp_path = _resolve_path(raw) if raw else None

    supp_text = ""
    if supp_path:
        supp_text = _parse_file(supp_path)
        print(f"  ✓  Parsed {len(supp_text):,} characters from {supp_path.name}\n")

    full_text = resume_text
    if supp_text:
        full_text += "\n\n--- SUPPLEMENTARY PROFILE ---\n\n" + supp_text

    # ── Step 2: Claude extraction ─────────────────────────────────────────────
    print("─" * 60)
    print("Step 2 of 3 — Generating profile with Claude...")
    try:
        import yaml
    except ImportError:
        sys.exit("❌  pyyaml not installed. Run: pip install pyyaml")

    try:
        profile = _extract_with_claude(api_key, full_text)
    except Exception as e:
        sys.exit(f"❌  Claude extraction failed: {e}")
    print("  ✓  Done\n")

    # ── Step 3: review + customise ────────────────────────────────────────────
    print("─" * 60)
    print("Step 3 of 3 — Review\n")

    c = profile.get("candidate", {})
    t = profile.get("target", {})
    print(f"  Name:          {c.get('name', '?')}")
    print(f"  Location:      {c.get('location', '?')}")
    print(f"  Remote:        {'yes' if c.get('open_to_remote') else 'no'}")
    print(f"  Target roles:  {', '.join(t.get('roles', []))}")
    print(f"  Level:         {t.get('level', '?')}")
    dbs = profile.get("deal_breakers", [])
    print(f"  Deal-breakers: {', '.join(dbs) if dbs else '(none set)'}")

    strengths = profile.get("strengths", [])
    if strengths:
        print(f"\n  Strengths detected ({len(strengths)}):")
        for s in strengths[:5]:
            print(f"    • {s}")
        if len(strengths) > 5:
            print(f"    … and {len(strengths) - 5} more")

    print("\n  Anything to add or correct? (press Enter to skip each)\n")

    extra = _ask("  Additional strengths (comma-separated): ")
    if extra:
        for s in [x.strip() for x in extra.split(",") if x.strip()]:
            profile.setdefault("strengths", []).append(s)

    extra_dbs = _ask("  Additional deal-breakers (comma-separated): ")
    if extra_dbs:
        for d in [x.strip() for x in extra_dbs.split(",") if x.strip()]:
            profile.setdefault("deal_breakers", []).append(d)

    salary = _ask("  Minimum acceptable salary in USD (or Enter to skip): ")
    if salary:
        try:
            profile["salary_floor_usd"] = int(salary.replace(",", "").replace("$", ""))
        except ValueError:
            print("  (Could not parse salary — skipping)")

    # ── Write ─────────────────────────────────────────────────────────────────
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(yaml.dump(profile, allow_unicode=True, sort_keys=False))

    print(f"\n  ✓  Profile saved to {PROFILE_PATH}")
    print("\n  Next steps:")
    print("  1. Review / edit any time:  nano ~/.job_pipeline/profile.yaml")
    print("  2. Set up your .env file (see README for ANTHROPIC_API_KEY, GMAIL_APP_PASSWORD)")
    print("  3. Run: scorerole\n")
    print("─" * 60)
