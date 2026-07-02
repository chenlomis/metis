from __future__ import annotations
import os, re, sys, datetime, logging
from pathlib import Path
from dotenv import load_dotenv

from .llm import (
    LLMProviderError,
    create_llm_client,
    normalize_provider,
    provider_api_key_env,
    resolve_stage_models,
)

# Load .env from project root (dev/editable install) or ~/.job_pipeline/.env (pipx/pip install).
# Preserve scheduler-pinned state paths so a launchd/cron job installed for one
# data directory cannot silently drift to another because of project .env loading.
_PINNED_STATE_ENV = {
    key: os.environ[key]
    for key in ("METIS_DATA_DIR", "METIS_PROFILE")
    if key in os.environ
}
_dotenv_candidates = [
    Path(__file__).parent.parent / ".env",
    Path(os.environ.get("METIS_DATA_DIR", Path.home() / ".job_pipeline")) / ".env",
]
for _dotenv_path in _dotenv_candidates:
    if load_dotenv(_dotenv_path, override=True):
        break
os.environ.update(_PINNED_STATE_ENV)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY       = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY          = os.getenv("OPENAI_API_KEY")
_RAW_LLM_PROVIDER       = os.getenv("METIS_LLM_PROVIDER", os.getenv("LLM_PROVIDER", "anthropic"))
_PROVIDER_CONFIG_ERROR  = None
try:
    LLM_PROVIDER        = normalize_provider(_RAW_LLM_PROVIDER)
except LLMProviderError as exc:
    LLM_PROVIDER        = "anthropic"
    _PROVIDER_CONFIG_ERROR = exc
_MODEL_SETTINGS         = resolve_stage_models(LLM_PROVIDER)
LLM_API_KEY        = "" if _PROVIDER_CONFIG_ERROR else os.getenv(provider_api_key_env(LLM_PROVIDER), "")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", GMAIL_ADDRESS)
MODEL              = _MODEL_SETTINGS["model"]
PRESCREEN_MODEL    = _MODEL_SETTINGS["prescreen_model"]
EXTRACT_MODEL      = _MODEL_SETTINGS["extract_model"]
MAX_JOBS_PER_RUN   = int(os.getenv("MAX_JOBS_PER_RUN", "40"))
DEFAULT_LOOKBACK   = os.getenv("DEFAULT_LOOKBACK", "3d")  # fallback only; main() uses last-run timestamp when available

from .state import (
    DATA_DIR, LOG_DIR, SEEN_FILE,
    load_seen_roles, save_seen_roles, save_skipped_roles, _role_hash,
    load_role_queue, save_role_queue,
)
from .feedback import save_last_run
from .sources import fetch_alerts
from .sources.linkedin import extract_jobs, extract_jobs_html, _extract_text
from .score import score_jobs_batch, rank_jobs, build_score_system
from .render import render_html
from .deliver import send_digest

# ---------------------------------------------------------------------------
# Startup validation — fail fast with a clear message if config is missing
# ---------------------------------------------------------------------------
def _validate_env(require_gmail: bool = True):
    errors = []
    provider = LLM_PROVIDER
    key_env = provider_api_key_env(provider)
    if _PROVIDER_CONFIG_ERROR:
        errors.append(f"  {_PROVIDER_CONFIG_ERROR}")
    elif not os.getenv(key_env):
        if key_env == "OPENAI_API_KEY":
            key_url = "https://platform.openai.com/api-keys"
            note = "Get one at"
        else:
            key_url = "https://console.anthropic.com"
            note = "Get one at"
        errors.append(
            f"  {key_env} is not set for METIS_LLM_PROVIDER={provider}.\n"
            f"  {note} {key_url}."
        )
    if require_gmail:
        if not GMAIL_ADDRESS:
            errors.append(
                "  GMAIL_ADDRESS is not set.\n"
                "  Add your Gmail address to .env."
            )
        if not GMAIL_APP_PASSWORD:
            errors.append(
                "  GMAIL_APP_PASSWORD is not set.\n"
                "  Generate one at https://myaccount.google.com/apppasswords (requires 2FA)."
            )
    if errors:
        print("❌  Missing required configuration:\n")
        for e in errors:
            print(e)
        print("\nSee .env.example for all required fields.")
        raise SystemExit(1)

# Module-level logger reference — handlers are wired up inside main()
# so that importing pipeline doesn't create directories or touch the root logger.
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_lookback(value: str) -> datetime.datetime | None:
    """Parse '3d', '7d', '2026-05-10', 'yesterday' → datetime. Returns None on failure."""
    if re.match(r'^\d+d(ay)?s?$', value):
        days = int(re.match(r'^\d+', value).group())
        return datetime.datetime.now() - datetime.timedelta(days=days)
    import dateparser
    return dateparser.parse(value, settings={"RETURN_AS_TIMEZONE_AWARE": False})


def _since_last_run(fallback: str = DEFAULT_LOOKBACK) -> tuple[datetime.datetime, str]:
    """Return (since_dt, label) using last_run.json timestamp, or fallback duration.

    Label is shown to the user so they know which window is active.
    """
    try:
        from .feedback import load_last_run
        run = load_last_run()
        if run and run.get("run_timestamp"):
            since_dt = datetime.datetime.fromisoformat(run["run_timestamp"])
            label = f"since last run ({run.get('run_date', since_dt.date())})"
            return since_dt, label
    except Exception:
        pass
    since_dt = _parse_lookback(fallback)
    return since_dt, f"{fallback} (no prior run found)"


_COST_GATE_USD = 0.50   # show prominent cost warning when upper-bound estimate exceeds this


def _prompt_score_all(n_found: int, cap: int) -> int:
    """Interactively ask how many roles to score. Returns the chosen count.

    Accepts:
      Enter / n / N      → cap (default, no pre-screen)
      y / yes / all      → n_found (Haiku pre-screen runs first)
      a number           → that count, clamped to 1..n_found
    """
    from .score import estimate_cost, estimate_cost_hi
    cost_str  = estimate_cost(n_found)
    cost_hi   = estimate_cost_hi(n_found)
    high_cost = cost_hi >= _COST_GATE_USD

    print(f"\n  ⚠  Found {n_found} new roles in your lookback window.")
    print(f"     Your cap is {cap} (MAX_JOBS_PER_RUN in .env).")
    if high_cost:
        print(f"     ⚠  Estimated cost: ~{cost_str}  (up to ${cost_hi:.2f}) — higher than usual")
    else:
        print(f"     Estimated cost: ~{cost_str}")
    print(f"     (Haiku pre-screen runs first — actual cost typically 40–60% lower.)")
    print(f"     Roles beyond the number you choose are queued for the next run.\n")
    try:
        ans = input(
            f"  Score how many? Enter a number (1–{n_found}), 'all', or press Enter for cap ({cap}): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return cap

    if not ans or ans in ("n", "no"):
        return cap
    if ans in ("y", "yes", "all"):
        return n_found
    try:
        n = max(1, min(int(ans), n_found))
        print(f"     → {n} roles · estimated cost: ~{estimate_cost(n)}")
        return n
    except ValueError:
        print(f"     → Unrecognized input, using cap ({cap})")
        return cap


# ---------------------------------------------------------------------------
# Pipeline stages (private — called in sequence by run_pipeline)
# ---------------------------------------------------------------------------

# Keywords that, when found in a LinkedIn alert's quoted search term, mean the
# entire email is for a different job function and should be skipped before parsing.
# "When in doubt, don't filter" — only reject on unambiguous non-PM signals.
_NON_PM_SUBJECT_SIGNALS = frozenset({
    "designer", "design", "ux", "ui",
    "engineer", "engineering", "developer", "swe",
    "ml", "machine learning", "data scientist", "data science",
    "researcher",
    "finance", "accounting", "legal", "marketing", "sales",
    "recruiter", "recruiting",
})


def _is_pm_email(subject: str) -> bool:
    """Return False only when the email subject clearly indicates a non-PM function.

    LinkedIn alert subjects often start with a quoted search term:
      '"senior designer": Company - Role Title at ...'
    We extract that term and check for non-PM function signals. If the subject
    has no quoted term we can't tell, so we pass it through (return True).
    """
    m = re.match(r'^"([^"]+)"', subject.strip())
    if not m:
        return True  # ambiguous subject — don't filter
    search_term = m.group(1).lower()
    words = set(re.sub(r"[^a-z\s]", "", search_term).split())
    return not bool(words & _NON_PM_SUBJECT_SIGNALS)


def _stage_ingest(since_dt: datetime.datetime, seen_roles: set, profile=None) -> "list[dict] | None":
    """Fetch job alerts (LinkedIn + proactive sources) and return deduplicated new jobs.

    Applies three dedup layers:
      1. job_id exact duplicate within this run
      2. title+company key (same role, different location email)
      3. role_hash in seen_roles (30-day cross-run TTL gate)

    Returns:
      None           — no emails found and no proactive jobs
      []  (empty)    — sources returned jobs but all already seen within 30 days
      [...]           — new, unseen roles ready for cap + scoring
    """
    threads = fetch_alerts(since_dt, profile=profile)

    all_jobs: list[dict] = []
    seen_job_ids:   set[str]   = set()
    seen_role_keys: set[tuple] = set()

    def _ingest(job: dict):
        role_key  = (job["title"].lower().strip(), job["company"].lower().strip())
        role_hash = _role_hash(job["title"], job["company"])
        if (job["job_id"] not in seen_job_ids
                and role_key not in seen_role_keys
                and role_hash not in seen_roles):
            seen_job_ids.add(job["job_id"])
            seen_role_keys.add(role_key)
            seen_roles.add(role_hash)
            all_jobs.append(job)

    skipped_emails = 0
    for t in (threads or []):
        subject = t.get("subject", "")
        if subject and not _is_pm_email(subject):
            log.info("Subject filter: skipping non-PM alert — %s", subject[:80])
            skipped_emails += 1
            continue
        email_date = t.get("email_date")
        jobs_from_thread = extract_jobs(t["body"])
        # Recommendation emails ("Company is hiring" / "Similar jobs") have no plain-text
        # "View job:" line — fall back to HTML link extraction.
        if not jobs_from_thread and t.get("html"):
            jobs_from_thread = extract_jobs_html(t["html"])
            if jobs_from_thread:
                log.info(f"HTML extraction found {len(jobs_from_thread)} jobs in recommendation email")
        for job in jobs_from_thread:
            if email_date:
                job["email_date"] = email_date
            _ingest(job)

    if skipped_emails:
        log.info("Subject filter: skipped %d non-PM alert email(s)", skipped_emails)

    # Proactive company career-page scraping (enabled via profile proactive_sources)
    if profile and profile.get("proactive_sources", {}).get("enabled"):
        try:
            from .sources.proactive import fetch_proactive
            proactive_jobs = fetch_proactive(profile, seen_roles)
            for job in proactive_jobs:
                _ingest(job)
            if proactive_jobs:
                log.info("proactive: ingested %d new roles after dedup", len(proactive_jobs))
        except Exception as e:
            log.warning("proactive source failed — skipping: %s", e)

    if not threads and not all_jobs:
        log.info("No emails in lookback window and no proactive sources active. Done.")
        return None

    # Merge staged roles from prior capped runs, deduping against already-ingested jobs.
    staged = load_role_queue()
    if staged:
        ingested_hashes = {_role_hash(j["title"], j["company"]) for j in all_jobs}
        merged = 0
        for job in staged:
            h = _role_hash(job["title"], job["company"])
            if h not in ingested_hashes and h not in seen_roles:
                ingested_hashes.add(h)
                seen_roles.add(h)
                all_jobs.append(job)
                merged += 1
        log.info("Role queue: merged %d staged role(s) (%d in queue, %d already seen/ingested)",
                 merged, len(staged), len(staged) - merged)

    # Sort freshest first so the cap always scores the most recent roles.
    all_jobs.sort(key=lambda j: j.get("email_date", ""), reverse=True)

    return all_jobs


def _stage_cap(
    all_jobs: list[dict],
    score_all: bool,
    client,                  # Metis LLM client — passed through to prescreen if needed
    dry_run: bool = False,
) -> tuple[list[dict], bool]:
    """Apply the per-run cap and optionally run the Haiku pre-screen.

    Returns (jobs_to_score, did_prescreen).

    Cap logic (in priority order):
      --no-limit flag       → confirm, run Haiku pre-screen, score everything that survives
      interactive TTY       → ask how many to score; then pre-screen and cap to that number
      non-interactive cron  → pre-screen, then cap silently to MAX_JOBS_PER_RUN

    IMPORTANT: This function must be called BEFORE building new_role_timestamps so that
    only the final survivors get persisted to seen_roles.json (role-burial fix).
    """
    n_found = len(all_jobs)
    should_prescreen = False

    # Show cost estimate upfront when --no-limit is used (spec requirement).
    if score_all:
        from .score import estimate_cost
        print(
            f"\n  --no-limit: {n_found} role{'s' if n_found != 1 else ''} in window. "
            f"Estimated cost: ~{estimate_cost(n_found)}\n"
            f"  (Haiku pre-screen will filter obvious mismatches — actual cost typically lower.)\n"
        )
        # Guard: require explicit confirmation in interactive mode; block in cron.
        if n_found > MAX_JOBS_PER_RUN:
            if not sys.stdin.isatty():
                log.warning(
                    "--no-limit passed in non-interactive (cron) mode with %d roles — "
                    "capping to MAX_JOBS_PER_RUN=%d to prevent runaway spend. "
                    "Run interactively to confirm an uncapped run.",
                    n_found, MAX_JOBS_PER_RUN,
                )
                score_all = False   # fall through to normal cap logic below
            else:
                confirm = input(f"  Score all {n_found} roles without cap? [y/N] ").strip().lower()
                if confirm != "y":
                    print(f"  Capping to {MAX_JOBS_PER_RUN} (MAX_JOBS_PER_RUN). Pass --no-limit and confirm 'y' to override.")
                    score_all = False

    # Determine effective cap for this run (cap_to=None means score everything).
    cap_to: int | None = None
    if MAX_JOBS_PER_RUN > 0 and n_found > MAX_JOBS_PER_RUN:
        if score_all:
            log.info(f"{n_found} roles to evaluate (--no-limit flag; Haiku pre-screen will filter)")
            should_prescreen = True
        elif sys.stdin.isatty():
            chosen = _prompt_score_all(n_found, MAX_JOBS_PER_RUN)
            if chosen >= n_found:
                log.info(f"Scoring all {n_found} roles (Haiku pre-screen will filter first)")
                should_prescreen = True
            else:
                cap_to = chosen
                should_prescreen = True  # always prescreen before a custom cap too
                log.info(f"Scoring up to {chosen} role(s) (Haiku pre-screen runs first)")
        else:
            # Non-interactive (cron): prescreen full batch, then cap to MAX_JOBS_PER_RUN.
            cap_to = MAX_JOBS_PER_RUN
            should_prescreen = True
            log.info(f"{n_found} roles found — Haiku pre-screen before capping to {MAX_JOBS_PER_RUN}")
    else:
        log.info(f"{n_found} unique role(s) to evaluate")

    if should_prescreen:
        from .score import prescreen_jobs_batch
        from .trace import write_trace
        before_prescreen = list(all_jobs)
        all_jobs = prescreen_jobs_batch(client, all_jobs, model=PRESCREEN_MODEL)
        passed_ids = {id(j) for j in all_jobs}
        for job in before_prescreen:
            if id(job) not in passed_ids:
                job["eval"] = {"score": 0, "verdict": "prescreened", "reason": "haiku_prescreen_filtered"}
                if not dry_run:
                    write_trace(job)

    # Apply effective cap after pre-screen. Excess roles are queued for the next run.
    if cap_to is not None and len(all_jobs) > cap_to:
        excess = all_jobs[cap_to:]
        all_jobs = all_jobs[:cap_to]
        if not dry_run:
            save_role_queue(excess)
        queue_msg = "would be queued for next run" if dry_run else "queued for next run → role_queue.json"
        log.info("Capping to %d role(s); %d %s", cap_to, len(excess), queue_msg)
    else:
        if not dry_run:
            save_role_queue([])  # everything scored this run — queue is now empty

    return all_jobs, should_prescreen


def _stage_enrich_and_score(jobs: list[dict], client, dry_run: bool = False) -> list[dict]:
    """Fetch JDs (enrich), run Layer 1 extraction + gate checks, then score + rank with Sonnet.

    Layer 1 (Haiku, temperature=0):
      - Extracts structured fields (salary, work model, degree req, domain, etc.)
      - Hard gates: blank JDs skip Sonnet; disclosed salary below floor → filtered
      - Extraction failures fall back gracefully — scoring is never blocked

    Layer 2 (Sonnet):
      - Receives extraction context per job as grounding
      - Only runs on roles that passed all hard gates

    Returns ranked jobs with 'eval' key populated.
    Raises SystemExit if the profile is missing.
    """
    from .sources.linkedin import enrich_jobs
    from .extract import extract_jd_structs, check_hard_gates, format_extraction_for_scoring
    from .profile import load_profile_yaml

    jobs = enrich_jobs(jobs)

    # Load profile for gate checks
    try:
        profile_data = load_profile_yaml() or {}
    except Exception:
        profile_data = {}

    # Layer 1: structured extraction
    try:
        extractions = extract_jd_structs(client, jobs, model=EXTRACT_MODEL)
    except Exception as exc:
        log.warning("Layer 1 extraction failed (%s) — scoring without extraction context", exc)
        extractions = [{} for _ in jobs]

    # Attach extraction structs and apply hard gates
    to_score: list[dict] = []
    for job, ext in zip(jobs, extractions):
        job["extraction"] = ext
        passes, gate_name = check_hard_gates(ext, profile_data)
        if not passes:
            job["eval"] = {
                "score": 0,
                "verdict": "filtered",
                "leveragePoints": [],
                "frictionPoints": [],
                "tags": [{"text": f"gate: {gate_name}", "sentiment": "red"}],
            }
            log.info("Gate filtered: %s at %s — %s", job["title"], job["company"], gate_name)
        else:
            to_score.append(job)

    if to_score:
        try:
            score_jobs_batch(client, to_score, profile_data, model=MODEL)   # mutates job["eval"] in-place
        except FileNotFoundError:
            raise SystemExit(
                "\n❌  No scoring profile found.\n"
                "   Run `metis init` to create one from your resume.\n"
            )

    ranked = rank_jobs(jobs)
    if not dry_run:
        from .trace import write_trace
        for job in ranked:
            write_trace(job)
    return ranked


def _stage_split_filtered(jobs: list[dict]) -> tuple[list[dict], int]:
    """Separate deal-breaker-filtered roles from scoreable ones.

    Filtered roles must not appear in digest sections — only in the footer count.
    Their hashes are already recorded in new_role_timestamps (built before this call)
    so they ARE marked seen and won't reappear next run.

    Returns (scored_jobs, n_filtered).
    """
    filtered = [j for j in jobs if j["eval"].get("verdict") == "filtered"]
    scored   = [j for j in jobs if j["eval"].get("verdict") != "filtered"]
    if filtered:
        log.info("%d role(s) filtered by deal-breaker — excluded from digest sections", len(filtered))
    return scored, len(filtered)


def _stage_deliver(
    scored_jobs: list[dict],
    n_filtered: int,
    new_role_timestamps: dict,
    dry_run: bool = False,
) -> None:
    """Render the HTML digest and deliver it via SMTP.

    Raises SystemExit(1) on SMTP failure (so seen_roles.json is NOT written — the roles
    remain unseen and will be re-scored on the next run, per SPEC §8 / T-07).
    On success, persists new_role_timestamps to seen_roles.json and writes Apply/Consider
    rows to the Applications xlsx tracker.

    Skipped-role metadata is saved to skipped_roles.json BEFORE delivery so it survives
    even if SMTP fails — it's needed for future backport and costs nothing to keep.

    dry_run=True skips all writes: no email send, no seen_roles save, no tracker write.
    """
    # Save skipped role metadata before delivery (safe to write regardless of SMTP outcome)
    skipped_jobs = [j for j in scored_jobs if j.get("eval", {}).get("verdict") == "skipped"]
    if skipped_jobs and not dry_run:
        save_skipped_roles(skipped_jobs)

    run_date = datetime.datetime.now().strftime("%B %d, %Y")
    html = render_html(scored_jobs, run_date, deal_breaker_count=n_filtered)

    if dry_run:
        apply_n    = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "apply")
        consider_n = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "consider")
        filter_note = f", {n_filtered} filtered by deal-breaker" if n_filtered else ""
        print(f"  [dry-run] Would send digest — {len(scored_jobs)} evaluated: {apply_n} apply, {consider_n} consider{filter_note}")
        return

    try:
        send_digest(html, run_date, job_count=len(scored_jobs))
    except Exception:
        log.error("Pipeline finished scoring but failed to deliver digest — check SMTP settings in .env")
        raise SystemExit(1)

    save_seen_roles(new_role_timestamps)
    save_last_run(scored_jobs, run_date, filtered_count=n_filtered)

    from .xlsx import write_to_tracker, TRACKER_PATH
    write_to_tracker(scored_jobs, run_date=datetime.date.today().isoformat())
    print(f"  Tracker → {TRACKER_PATH}")

    apply_n    = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "apply")
    consider_n = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "consider")
    filter_note = f", {n_filtered} filtered by deal-breaker" if n_filtered else ""
    log.info(
        f"=== Done — {len(scored_jobs)} evaluated: {apply_n} apply, {consider_n} consider{filter_note} ==="
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_pipeline(since_dt: datetime.datetime, score_all: bool = False, dry_run: bool = False):
    """Fetch LinkedIn alert emails since since_dt, score unseen roles, deliver digest.

    seen_roles.json (30-day TTL) is the dedup gate — roles already scored
    within the last 30 days are skipped automatically.

    score_all=True (--no-limit flag) bypasses the cap and runs a Haiku pre-screen
    before full Sonnet scoring to keep costs down.

    dry_run=True skips all writes: no email send, no seen_roles save, no tracker write.
    """
    log.info(f"=== Pipeline run starting — lookback since {since_dt.strftime('%Y-%m-%d')} ===")
    try:
        provider_id = normalize_provider(LLM_PROVIDER)
        api_key = os.getenv(provider_api_key_env(provider_id), "")
        client = create_llm_client(provider=provider_id, api_key=api_key or "")
    except LLMProviderError as exc:
        raise SystemExit(f"\n❌  {exc}\n") from exc
    seen_roles = load_seen_roles()

    # Load profile early — needed for proactive source config and gate checks
    from .profile import load_profile_yaml
    profile_data = load_profile_yaml() or {}

    # Stage 1: Ingest + deduplicate
    all_jobs = _stage_ingest(since_dt, seen_roles, profile=profile_data)
    if all_jobs is None:
        return   # "No emails in lookback window" already logged inside _stage_ingest
    if not all_jobs:
        log.info("No new roles to evaluate — all already seen within the past 30 days.")
        return

    # Stage 2: Apply cap, optionally run Haiku pre-screen
    # new_role_timestamps is built AFTER this so only surviving roles get persisted.
    # (Pre-fix: capped roles were written to seen_roles.json and locked out for 14 days
    #  without ever being evaluated — the role-burial bug.)
    all_jobs, _prescreened = _stage_cap(all_jobs, score_all, client, dry_run=dry_run)
    if not all_jobs:
        log.info("Pre-screen filtered all roles — nothing left to score.")
        return

    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
    new_role_timestamps = {
        _role_hash(j["title"], j["company"]): now_iso
        for j in all_jobs
    }

    # Stage 3: Enrich (fetch JDs) + score + rank
    all_jobs = _stage_enrich_and_score(all_jobs, client, dry_run=dry_run)

    # Stage 4: Split deal-breaker filtered roles
    scored_jobs, n_filtered = _stage_split_filtered(all_jobs)

    if not scored_jobs:
        if n_filtered:
            log.info(
                "All %d role(s) were filtered by deal-breaker rules — no digest sent.\n"
                "If this seems wrong, run `metis init` → Quick edits → Deal-breakers "
                "to review your rules.",
                n_filtered,
            )
        else:
            log.info("No scoreable roles after filtering — no digest sent.")
        if not dry_run:
            save_seen_roles(new_role_timestamps)
        return

    # Stage 5: Render + deliver digest (persists seen_roles on success)
    _stage_deliver(scored_jobs, n_filtered, new_role_timestamps, dry_run=dry_run)


def debug_emails():
    """Dump the most recent LinkedIn email body to ~/.job_pipeline/debug_email.txt."""
    import imaplib, email as email_lib
    from .sources.linkedin import _LINKEDIN_SENDER_SEARCH
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        _, data = imap.search(None, _LINKEDIN_SENDER_SEARCH)
        all_ids = data[0].split()
        if not all_ids:
            print("No LinkedIn emails found.")
            return
        _, raw = imap.fetch(all_ids[-1], "(RFC822)")
        msg = email_lib.message_from_bytes(raw[0][1])
        body = _extract_text(msg)
    out = DATA_DIR / "debug_email.txt"
    out.write_text(body)
    print(f"Raw email body written to: {out}")
    print("--- First 2000 chars ---")
    print(body[:2000])


# ---------------------------------------------------------------------------
# Backward-compatible CLI shim
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None):
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    main()
