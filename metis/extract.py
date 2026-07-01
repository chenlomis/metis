"""Layer 1 JD extraction — Haiku structured extraction before Sonnet scoring.

Reads JD text for each job and returns a structured dict of factual fields.
These serve two purposes:
  1. Hard gate checks (salary_floor, blank JDs) evaluated in Python before calling Sonnet
  2. Structured context passed into each Layer 2 scoring block for grounding

Temperature=0 for determinism. Batched calls (≤10 jobs/chunk), same retry strategy
as score.py. Extraction failures fall back gracefully — missing structs never block scoring.
"""
import json, logging, os, re, time
from pathlib import Path
from .llm import LLMTransientError, complete_text
from .prompts import JD_EXTRACT_SYSTEM

log = logging.getLogger(__name__)

_DEFAULT_EXTRACT_MODEL = "claude-haiku-4-5"
_EXTRACT_CHUNK_SIZE = 10


# ---------------------------------------------------------------------------
# Retry constants (mirror score.py)
# ---------------------------------------------------------------------------

_RETRYABLE = (LLMTransientError,)
_MAX_ATTEMPTS = 3

_REQUIRED_KEYS = {
    "jd_quality", "unknown_fields", "role_function_match",
    "salary_disclosed", "salary_min", "salary_max",
    "work_model", "degree_hard_requirement", "government_export_control",
}

_BLANK_STRUCT: dict = {
    "jd_quality": "blank",
    "unknown_fields": [],
    "role_function_match": True,
    "inferred_structural_level": None,
    "management_type": None,
    "manages_pm_team": None,
    "reports_to_level": None,
    "work_model": "unspecified",
    "hybrid_days_required": None,
    "salary_min": None,
    "salary_max": None,
    "salary_disclosed": False,
    "equity_type": None,
    "company_stage": "unknown",
    "company_tier": None,
    "customer_type": None,
    "customer_segment": None,
    "product_surface": [],
    "technical_depth_required": None,
    "org_maturity": None,
    "autonomy_level": None,
    "degree_hard_requirement": False,
    "degree_level": None,
    "visa_sponsorship": None,
    "government_export_control": False,
    "years_exp_min": None,
    "primary_execution_stack": [],
}


def _is_valid_struct(obj: object) -> bool:
    """Verify extracted object has all required keys with sensible types."""
    if not isinstance(obj, dict):
        return False
    return _REQUIRED_KEYS.issubset(obj.keys())


def _extract_chunk(client: anthropic.Anthropic, jobs: list[dict],
                   *, model: str = _DEFAULT_EXTRACT_MODEL) -> list[dict]:
    """Extract structured fields for one chunk of jobs. Returns list of structs."""
    job_blocks = "\n\n---\n\n".join(
        "JOB {n}: {title} at {company}\n{jd_line}".format(
            n=i + 1,
            title=j["title"],
            company=j["company"],
            jd_line=(
                "JD:\n" + j["jd"][:1500]
                if j.get("jd")
                else "(No JD — title/company only)"
            ),
        )
        for i, j in enumerate(jobs)
    )

    response = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max(1024, len(jobs) * 350),
                temperature=0,
                system=JD_EXTRACT_SYSTEM,
                messages=[{
                    "role": "user",
                    "content": (
                        f"Extract structured fields for all {len(jobs)} jobs. "
                        f"Return a JSON array of exactly {len(jobs)} objects.\n\n"
                        f"{job_blocks}"
                    ),
                }],
            )
            break
        except _RETRYABLE as exc:
            if attempt < _MAX_ATTEMPTS - 1:
                wait = 2 ** attempt
                log.warning(
                    "Extraction API error (attempt %d/%d): %s — retrying in %ds",
                    attempt + 1, _MAX_ATTEMPTS, exc, wait,
                )
                time.sleep(wait)
            else:
                log.error("Extraction API error after %d attempts: %s", _MAX_ATTEMPTS, exc)
                raise

    usage = response.usage
    log.info(
        "Extraction (%s) — input: %d tok, output: %d tok",
        model, usage.input_tokens, usage.output_tokens,
    )

    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", response.content[0].text.strip(), flags=re.MULTILINE
    ).strip()

    try:
        structs = json.loads(raw)
        if isinstance(structs, dict):
            structs = [structs]
        if not isinstance(structs, list):
            raise ValueError("Expected JSON array")
        return [s if _is_valid_struct(s) else {**_BLANK_STRUCT, "jd_quality": "low"} for s in structs]
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Extraction JSON parse failed (%s) — using blank structs for %d jobs", exc, len(jobs))
        # Use "extraction_failed" not "blank" — JD content may exist; only "blank" triggers
        # the jd_blank hard gate in check_hard_gates(). Scoring proceeds without grounding.
        return [{**dict(_BLANK_STRUCT), "jd_quality": "extraction_failed"} for _ in jobs]


def extract_jd_structs(client: anthropic.Anthropic, jobs: list[dict],
                       *, model: str = _DEFAULT_EXTRACT_MODEL) -> list[dict]:
    """Extract Layer 1 structured fields for all jobs. Returns one dict per job, same order.

    Jobs without JD text (job.get('jd') is falsy) still run through extraction so
    the model can infer what it can from title+company. If extraction fails entirely,
    returns blank structs so scoring can proceed unblocked.
    """
    if not jobs:
        return []

    chunks = [jobs[i : i + _EXTRACT_CHUNK_SIZE] for i in range(0, len(jobs), _EXTRACT_CHUNK_SIZE)]
    if len(chunks) > 1:
        log.info("Extraction: %d jobs in %d chunks of ≤%d", len(jobs), len(chunks), _EXTRACT_CHUNK_SIZE)

    all_structs: list[dict] = []
    for idx, chunk in enumerate(chunks, start=1):
        if len(chunks) > 1:
            log.info("Extraction chunk %d/%d — %d role(s); still working…", idx, len(chunks), len(chunk))
        try:
            structs = _extract_chunk(client, chunk, model=model)
        except Exception as exc:
            log.warning("Extraction chunk failed (%s) — blank structs for %d jobs", exc, len(chunk))
            structs = [{**dict(_BLANK_STRUCT), "jd_quality": "extraction_failed"} for _ in chunk]

        # Pad if response was short
        while len(structs) < len(chunk):
            structs.append({**dict(_BLANK_STRUCT), "jd_quality": "extraction_failed"})
        all_structs.extend(structs[: len(chunk)])

    return all_structs


# ---------------------------------------------------------------------------
# Gate checker
# ---------------------------------------------------------------------------

def check_hard_gates(jd_struct: dict, profile_data: dict) -> tuple[bool, str]:
    """Evaluate hard gates against extracted struct. Returns (passes, gate_name).

    passes=True  → role proceeds to Sonnet scoring
    passes=False → role gets verdict="filtered" with gate_name in tags

    Gates checked here (Python code, not LLM):
      jd_blank    — no JD text at all; skip Sonnet to avoid wasting tokens
      salary_floor — salary_max is explicitly below profile floor * 0.9

    All other gates (work_model conflict, degree, visa, deal_breakers) are handled by
    Layer 2 via the extracted context block — they require nuanced judgment.
    """
    # Gate: blank JD
    if jd_struct.get("jd_quality") == "blank":
        return False, "jd_blank"

    # Gate: salary floor (only when both sides are explicit numbers)
    salary_floor = profile_data.get("salary_floor_usd")
    if (
        salary_floor
        and jd_struct.get("salary_disclosed")
        and jd_struct.get("salary_max") is not None
    ):
        try:
            if int(jd_struct["salary_max"]) < int(salary_floor) * 0.9:
                return False, "salary_floor"
        except (TypeError, ValueError):
            pass

    return True, ""


# ---------------------------------------------------------------------------
# Formatting for Layer 2
# ---------------------------------------------------------------------------

def format_extraction_for_scoring(ext: dict, listing_company: str = "") -> str:
    """Convert extraction struct to a compact human-readable block for Sonnet context.

    listing_company: the company name from the original job listing (e.g. from LinkedIn).
    When company_stage is unknown but a listing company is known, it is included so Sonnet
    does not apply the 'anon employer' tag contradicting the displayed job title.

    Returns empty string when struct is absent or blank (no JD case was already gated).
    """
    if not ext or ext.get("jd_quality") == "blank":
        return ""

    lines = ["[EXTRACTED CONTEXT]"]

    # Level & management
    level      = ext.get("inferred_structural_level") or "unknown"
    mgmt       = ext.get("management_type") or "unknown"
    manages_pm = ext.get("manages_pm_team")
    reports_to = ext.get("reports_to_level")
    mgmt_str   = f"manages PMs: {'yes' if manages_pm else 'no' if manages_pm is False else '?'}"
    rpt_str    = f"reports to: {reports_to}" if reports_to else ""
    lines.append(f"  Level: {level} | {mgmt_str}" + (f" | {rpt_str}" if rpt_str else ""))

    # Work model
    wm   = ext.get("work_model", "unspecified")
    days = ext.get("hybrid_days_required")
    wm_str = f"hybrid ({days}d/wk)" if wm == "hybrid" and days else wm
    lines.append(f"  Work model: {wm_str}")

    # Compensation
    sal_min = ext.get("salary_min")
    sal_max = ext.get("salary_max")
    sal_dis = ext.get("salary_disclosed", False)
    eq_type = ext.get("equity_type")
    if sal_dis and (sal_min or sal_max):
        lo = f"${sal_min:,}" if sal_min else "?"
        hi = f"${sal_max:,}" if sal_max else "?"
        comp_str = f"{lo}–{hi} disclosed"
    else:
        comp_str = "not disclosed"
    eq_str = f" | equity: {eq_type}" if eq_type and eq_type not in ("null", "unspecified") else ""
    lines.append(f"  Compensation: {comp_str}{eq_str}")

    # Domain
    cust  = ext.get("customer_type")
    seg   = ext.get("customer_segment")
    surfs = ext.get("product_surface") or []
    depth = ext.get("technical_depth_required")
    dom_parts = []
    if cust:
        dom_parts.append(f"{cust}" + (f"/{seg}" if seg else ""))
    if surfs:
        dom_parts.append(", ".join(surfs[:3]))
    if depth:
        dom_parts.append(f"depth: {depth}")
    if dom_parts:
        lines.append(f"  Domain: {' | '.join(dom_parts)}")

    # Company
    stage = ext.get("company_stage", "unknown")
    tier  = ext.get("company_tier")
    co_str = stage + (f" / {tier}" if tier else "")
    # When stage is unknown but the listing has a known company name, surface it so
    # Sonnet does not apply an 'anon employer' tag that contradicts the job title display.
    if stage == "unknown" and listing_company and listing_company.lower() not in ("", "unknown", "anonymous"):
        co_str += f" (listed as: {listing_company})"
    lines.append(f"  Company: {co_str}")

    # Culture
    org  = ext.get("org_maturity")
    auto = ext.get("autonomy_level")
    if org or auto:
        parts = []
        if org:
            parts.append(f"org={org}")
        if auto:
            parts.append(f"autonomy={auto}")
        lines.append(f"  Culture: {', '.join(parts)}")

    # Hard gate signals
    deg_req = ext.get("degree_hard_requirement", False)
    deg_lvl = ext.get("degree_level")
    visa    = ext.get("visa_sponsorship")
    itar    = ext.get("government_export_control", False)
    gate_parts = []
    if deg_req:
        gate_parts.append(f"degree required ({deg_lvl or '?'})")
    if visa is False:
        gate_parts.append("no visa sponsorship")
    elif visa is True:
        gate_parts.append("visa sponsorship offered")
    if itar:
        gate_parts.append("export control / clearance required")
    if gate_parts:
        lines.append(f"  Flags: {', '.join(gate_parts)}")

    # JD quality & unknowns
    quality  = ext.get("jd_quality", "unknown")
    unknowns = ext.get("unknown_fields") or []
    q_str    = f"JD quality: {quality}"
    if unknowns:
        q_str += f" | unknowns: {', '.join(unknowns[:5])}"
    lines.append(f"  {q_str}")

    return "\n".join(lines)
