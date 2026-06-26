from __future__ import annotations
import os, re, sys, datetime, logging
from pathlib import Path
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", GMAIL_ADDRESS)
MODEL              = os.getenv("MODEL", "claude-sonnet-4-6")
MAX_JOBS_PER_RUN   = int(os.getenv("MAX_JOBS_PER_RUN", "20"))
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
    if not ANTHROPIC_API_KEY:
        errors.append(
            "  ANTHROPIC_API_KEY is not set.\n"
            "  Get one at https://console.anthropic.com (separate from Claude.ai subscription)."
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
      a number (40, 60…) → that count, clamped to cap..n_found (no pre-screen)
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
    print(f"     Roles beyond the cap stay unseen until their 30-day TTL expires.\n")
    try:
        ans = input(
            f"  Score how many? Enter a number ({cap}–{n_found}), 'all', or press Enter for cap ({cap}): "
        ).strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return cap

    if not ans or ans in ("n", "no"):
        return cap
    if ans in ("y", "yes", "all"):
        return n_found
    try:
        n = int(ans)
        n = max(cap, min(n, n_found))
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
    client,                  # anthropic.Anthropic — passed through to prescreen if needed
) -> tuple[list[dict], bool]:
    """Apply the per-run cap and optionally run the Haiku pre-screen.

    Returns (jobs_to_score, did_prescreen).

    Cap logic (in priority order):
      --no-limit flag            → skip prompt, run Haiku pre-screen, score everything that survives
      interactive TTY       → ask user; if yes, run pre-screen; if no, cap to MAX_JOBS_PER_RUN
      non-interactive cron  → cap silently, log warning

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
        all_jobs = prescreen_jobs_batch(client, all_jobs)
        passed_ids = {id(j) for j in all_jobs}
        for job in before_prescreen:
            if id(job) not in passed_ids:
                job["eval"] = {"score": 0, "verdict": "prescreened", "reason": "haiku_prescreen_filtered"}
                write_trace(job)

    # Apply effective cap after pre-screen. Excess roles are queued for the next run.
    if cap_to is not None and len(all_jobs) > cap_to:
        excess = all_jobs[cap_to:]
        all_jobs = all_jobs[:cap_to]
        save_role_queue(excess)
        log.info("Capping to %d role(s); %d queued for next run → role_queue.json",
                 cap_to, len(excess))
    else:
        save_role_queue([])  # everything scored this run — queue is now empty

    return all_jobs, should_prescreen


def _stage_enrich_and_score(jobs: list[dict], client) -> list[dict]:
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
        extractions = extract_jd_structs(client, jobs)
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
            score_jobs_batch(client, to_score, profile_data)   # mutates job["eval"] in-place
        except FileNotFoundError:
            raise SystemExit(
                "\n❌  No scoring profile found.\n"
                "   Run `scorerole init` to create one from your resume.\n"
            )

    ranked = rank_jobs(jobs)
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
        send_digest(html, run_date)
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
    client     = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
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
    all_jobs, _prescreened = _stage_cap(all_jobs, score_all, client)
    if not all_jobs:
        log.info("Pre-screen filtered all roles — nothing left to score.")
        return

    now_iso = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
    new_role_timestamps = {
        _role_hash(j["title"], j["company"]): now_iso
        for j in all_jobs
    }

    # Stage 3: Enrich (fetch JDs) + score + rank
    all_jobs = _stage_enrich_and_score(all_jobs, client)

    # Stage 4: Split deal-breaker filtered roles
    scored_jobs, n_filtered = _stage_split_filtered(all_jobs)

    if not scored_jobs:
        if n_filtered:
            log.info(
                "All %d role(s) were filtered by deal-breaker rules — no digest sent.\n"
                "If this seems wrong, run `scorerole init` → Quick edits → Deal-breakers "
                "to review your rules.",
                n_filtered,
            )
        else:
            log.info("No scoreable roles after filtering — no digest sent.")
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
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    import argparse

    # Set up logging here (not at module level) so importing pipeline
    # doesn't create directories or hijack the root logger.
    LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)  # restrict ~/.job_pipeline to owner
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_DIR / f"{datetime.date.today()}.log"),
            logging.StreamHandler(),
        ],
    )

    parser = argparse.ArgumentParser(
        prog="scorerole",
        description="AI-powered job alert digest — filters, scores, and delivers "
                    "only what's worth your time.",
    )
    parser.add_argument(
        "--lookback", default=None, metavar="DURATION",
        help="Override lookback window. Accepts: '3d', '7d', '2026-05-10'. "
             "Default: since last run (falls back to 3d if no prior run).",
    )
    parser.add_argument(
        "--no-limit", dest="score_all", action="store_true",
        help="Score every role in the lookback window, ignoring MAX_JOBS_PER_RUN. "
             "A Haiku pre-screen runs first to keep API costs down. "
             "Useful for catch-up runs after a long gap or a reset.",
    )
    parser.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Full run (fetch + score) but no writes: no email sent, no seen_roles saved, no tracker updated.",
    )

    subparsers = parser.add_subparsers(dest="command")

    # init_bak subcommand (archived — use `scorerole init` for the conversational wizard)
    init_p = subparsers.add_parser(
        "init_bak",
        help="[archived] Create your scoring profile from a resume (PDF, DOCX, or TXT).",
    )
    init_p.add_argument(
        "--resume", metavar="PATH",
        help="Path to your resume (PDF, DOCX, or TXT). Prompted interactively if omitted.",
    )
    init_p.add_argument(
        "--linkedin", metavar="PATH",
        help="Optional: LinkedIn export PDF or data archive for profile enrichment.",
    )

    # init subcommand — conversational onboarding wizard
    subparsers.add_parser(
        "init",
        help="Conversational profile setup — freeform prompts instead of a form.",
    )

    # reset subcommand
    reset_p = subparsers.add_parser("reset", help="Clear seen-role state so all roles reprocess.")
    reset_p.add_argument("--force",   action="store_true", help="Skip confirmation prompt.")
    reset_p.add_argument("--profile", action="store_true", help="Also delete your scoring profile (~/.job_pipeline/profile.yaml).")

    # schedule subcommand  (git-style nested actions)
    schedule_p = subparsers.add_parser(
        "schedule",
        help="Install, inspect, or remove the automated digest schedule.",
        description=(
            "Show the current schedule when called with no action.\n\n"
            "  scorerole schedule        show current schedule + OS job status\n"
            "  scorerole schedule set    interactive setup (or update)\n"
            "  scorerole schedule remove remove the scheduled job"
        ),
    )
    schedule_sub = schedule_p.add_subparsers(dest="schedule_action")
    schedule_sub.add_parser(
        "set",
        help="Run the interactive setup wizard to install or replace the schedule.",
    )
    schedule_sub.add_parser(
        "pause",
        help="Temporarily disable the schedule without losing your settings.",
    )
    schedule_sub.add_parser(
        "resume",
        help="Re-enable a paused schedule.",
    )
    schedule_sub.add_parser(
        "remove",
        help="Remove the scheduled job and clear ~/.job_pipeline/schedule.json.",
    )
    _run_p = schedule_sub.add_parser(
        "run",
        help=argparse.SUPPRESS,   # internal: called by launchd/cron
    )
    _run_p.add_argument(
        "--lookback", dest="run_lookback", default="1d", metavar="DURATION",
    )

    # track subcommand
    track_p = subparsers.add_parser(
        "track",
        help="Parse confirmation and rejection emails, update the Applications tracker.",
        description=(
            "Fetches emails from Gmail, classifies them as confirmations or rejections,\n"
            "and updates the Applications xlsx tracker accordingly.\n\n"
            "  scorerole track                   # parse last 7 days\n"
            "  scorerole track --lookback 30d    # extend lookback\n"
            "  scorerole track --dry-run         # preview matches, no writes"
        ),
    )
    track_p.add_argument(
        "--lookback", default="7d", metavar="DURATION",
        help="How far back to look for emails. Accepts '7d', '30d', '2026-06-01'. Default: 7d",
    )
    track_p.add_argument(
        "--dry-run", dest="dry_run", action="store_true",
        help="Parse and classify emails; print matches to stdout without writing or opening the tracker.",
    )

    # sources subcommand
    sources_p = subparsers.add_parser(
        "sources",
        help="Manage company career-page sources.",
        description=(
            "View and manage the company career pages scorerole checks each run.\n\n"
            "  scorerole sources              show active sources\n"
            "  scorerole sources list         show active sources\n"
            "  scorerole sources add Stripe   add a company by name\n"
            "  scorerole sources remove       interactively remove companies\n"
            "  scorerole sources on           enable company sources\n"
            "  scorerole sources off          disable company sources"
        ),
    )
    sources_sub = sources_p.add_subparsers(dest="sources_action")
    sources_sub.add_parser("list",   help="Show active sources.")
    sources_add_p = sources_sub.add_parser("add",    help="Add a company by name.")
    sources_add_p.add_argument("company_name", nargs="+", help="Company name to add.")
    sources_sub.add_parser("remove", help="Interactively remove companies.")
    sources_sub.add_parser("on",     help="Enable company sources.")
    sources_sub.add_parser("off",    help="Disable company sources.")

    # feedback subcommand
    feedback_p = subparsers.add_parser(
        "feedback",
        help="Add calibration notes that shape future scoring runs.",
        description=(
            "Collect free-form feedback on past scoring, parsed by Claude and\n"
            "appended to ~/.job_pipeline/feedback.md. Injected into the scoring\n"
            "prompt on every subsequent run.\n\n"
            "  scorerole feedback        # interactive prompt\n"
            "  scorerole feedback list   # show recent entries"
        ),
    )
    feedback_sub = feedback_p.add_subparsers(dest="feedback_action")
    feedback_sub.add_parser("list", help="Show recent feedback entries.")

    # debug subcommand
    subparsers.add_parser("debug", help="Dump the most recent LinkedIn alert email for inspection.")

    # report subcommand
    report_p = subparsers.add_parser(
        "report",
        help="Generate and send the Scorerole market report.",
        description=(
            "Compiles cumulative pipeline metrics from the tracker and sends\n"
            "the report to your email address.\n\n"
            "  scorerole report                       # send to email\n"
            "  scorerole report --output report.html  # save as HTML\n"
            "  scorerole report --output report.pdf   # save as PDF\n"
            "  scorerole report --lookback 60d        # scope market intel to 60 days"
        ),
    )
    report_p.add_argument(
        "--output", default=None, metavar="FILE",
        help="Save report to FILE (.html or .pdf) instead of sending by email.",
    )
    report_p.add_argument(
        "--lookback", default="30d", metavar="DURATION",
        help="How far back to scope market intelligence sections. Default: 30d",
    )
    report_p.add_argument(
        "--send", action="store_true",
        help="Send as a real report email (no [DRAFT PREVIEW] prefix).",
    )



    args = parser.parse_args()

    if args.command == "init_bak":
        _validate_env(require_gmail=False)   # only needs API key to parse resume
        from .init_cmd import run_init
        run_init(
            api_key=ANTHROPIC_API_KEY,
            resume_path_arg=getattr(args, "resume", "") or "",
            supplement_path_arg=getattr(args, "linkedin", "") or "",
        )

    elif args.command == "init":
        _validate_env(require_gmail=False)
        from .init2_cmd import run_init2
        run_init2(api_key=ANTHROPIC_API_KEY)

    elif args.command == "reset":
        targets = [SEEN_FILE]
        if args.profile:
            targets.append(DATA_DIR / "profile.yaml")

        existing = [p for p in targets if p.exists()]
        if not existing:
            print("Nothing to reset — no state files found.")
            return

        names = ", ".join(p.name for p in existing)
        if not args.force:
            suffix = " + your scoring profile" if args.profile else ""
            ans = input(f"Clear dedup state{suffix}? This cannot be undone. [y/N] ")
            if ans.strip().lower() != "y":
                print("Aborted.")
                return

        for p in existing:
            p.unlink(missing_ok=True)
        print(f"Cleared: {names}")
        if args.profile and (DATA_DIR / "profile.yaml") in existing:
            print("Run `scorerole init` to rebuild your scoring profile.")

    elif args.command == "schedule":
        from .schedule_cmd import (
            show_schedule, run_schedule_wizard, remove_schedule,
            pause_schedule, resume_schedule,
        )
        action = getattr(args, "schedule_action", None)
        if action == "set":
            run_schedule_wizard()
        elif action == "pause":
            paused = pause_schedule()
            if paused:
                print("  Schedule paused. Run `scorerole schedule resume` to re-enable.")
            else:
                print("  Nothing to pause — schedule is already paused or not configured.")
        elif action == "resume":
            resumed = resume_schedule()
            if resumed:
                print("  Schedule resumed.")
            else:
                print("  Nothing to resume — schedule is already active or not configured.")
        elif action == "remove":
            removed = remove_schedule()
            print("  Schedule removed." if removed else "  No schedule was configured.")
        elif action == "run":
            _validate_env()
            lookback_str = getattr(args, "run_lookback", "1d") or "1d"
            since_dt = _parse_lookback(lookback_str)
            if not since_dt:
                print(f"Could not parse --lookback '{lookback_str}'. Try: '1d', '4d', '7d'")
                raise SystemExit(1)
            log.info("=== Scheduled run: digest + track (lookback %s) ===", lookback_str)
            run_pipeline(since_dt=since_dt)
            from .track import run_track
            run_track(
                gmail_address=GMAIL_ADDRESS,
                app_password=GMAIL_APP_PASSWORD,
                since_dt=since_dt,
                dry_run=False,
                api_key=ANTHROPIC_API_KEY,
            )
        else:
            show_schedule()

    elif args.command == "track":
        _validate_env()
        since_dt = _parse_lookback(getattr(args, "lookback", "7d"))
        if not since_dt:
            print(f"Could not parse --lookback '{args.lookback}'. Try: '7d', '30d', '2026-06-01'")
            raise SystemExit(1)
        from .track import run_track
        run_track(
            gmail_address=GMAIL_ADDRESS,
            app_password=GMAIL_APP_PASSWORD,
            since_dt=since_dt,
            dry_run=getattr(args, "dry_run", False),
            api_key=ANTHROPIC_API_KEY,
        )

    elif args.command == "sources":
        from .sources_cmd import run_sources
        action = getattr(args, "sources_action", None)
        name_parts = getattr(args, "company_name", None)
        name = " ".join(name_parts) if name_parts else None
        run_sources(action, name)

    elif args.command == "feedback":
        _validate_env(require_gmail=False)
        action = getattr(args, "feedback_action", None)
        if action == "list":
            from .feedback import run_feedback_list
            run_feedback_list()
        else:
            from .feedback import run_feedback
            run_feedback(api_key=ANTHROPIC_API_KEY)

    elif args.command == "debug":
        _validate_env()
        debug_emails()

    elif args.command == "report":
        _validate_env()
        from .report_cmd import run_report
        from .xlsx import TRACKER_PATH
        run_report(
            tracker_path=TRACKER_PATH,
            gmail_address=GMAIL_ADDRESS,
            app_password=GMAIL_APP_PASSWORD,
            output=getattr(args, "output", None),
            preview=not getattr(args, "send", False),
        )

    else:
        # Default: run the digest pipeline
        _validate_env()
        if args.lookback:
            since_dt = _parse_lookback(args.lookback)
            if not since_dt:
                print(f"Could not parse --lookback '{args.lookback}'. "
                      f"Try: '3d', '7d', '2026-05-10'")
                raise SystemExit(1)
        else:
            since_dt, label = _since_last_run()
            log.info("Lookback window: %s", label)
        run_pipeline(since_dt=since_dt, score_all=args.score_all, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
