# scorerole — Architecture & Design Notes

## What It Does

scorerole is a personal CLI tool that reads LinkedIn job alert emails via IMAP, scores
each role against the user's structured profile using Claude, and delivers a ranked HTML
digest to Gmail. It runs on demand or on a schedule (launchd/cron).

The core value: turn 50+ noisy job alert emails per week into a prioritized shortlist
of 3–8 roles worth acting on.

---

## Pipeline Flow

```
Gmail (IMAP)
    │ fetch LinkedIn alert emails since <lookback>
    ▼
sources/linkedin.py — extract_jobs() / extract_jobs_html()
    │ parse title, company, location, job_id, URL from email body or HTML
    ▼
pipeline.py — 3-layer dedup
    │ 1. job_id  (exact duplicate within a run)
    │ 2. title+company key  (same role, different location email)
    │ 3. role_hash in seen_roles.json  (14-day cross-run TTL gate)
    ▼
pipeline.py — cap / prompt decision
    │ ≤ MAX_JOBS_PER_RUN  →  proceed
    │ > cap, interactive  →  prompt user with count + cost estimate
    │ > cap, --all flag   →  proceed (pre-screen will filter)
    │ > cap, non-TTY      →  silently cap + log warning
    ▼
score.py — prescreen_jobs_batch()  [only when going beyond cap]
    │ Haiku pass: title+company only, no JD fetch
    │ condensed profile context: target roles, seniority, deal-breakers
    │ returns ~50% of roles; falls back to full list on parse failure
    ▼
sources/linkedin.py — enrich_jobs()
    │ sequential HTTP fetch of each LinkedIn job page
    │ extracts JD text from JSON-LD JobPosting structured data
    │ extracts external ATS URL (Greenhouse/Lever/Ashby) from applyAction
    │ retries 3x with exponential backoff on 429/5xx/timeout
    ▼
score.py — score_jobs_batch()
    │ single Sonnet batch call: all surviving jobs in one message
    │ profile rendered as cached system prompt block
    │ returns score (0-100), verdict, leveragePoints, frictionPoints, tags
    ▼
score.py — rank_jobs()
    │ re-derives verdict from score + profile thresholds (guards Claude drift)
    │ sorts: apply → consider → skipped, then by score desc within each tier
    ▼
render.py — render_html() / build_digest_html()
    │ tries Node/ts-node React Email renderer first
    │ falls back to Python inline HTML builder
    ▼
render.py — send_digest()
    │ SMTP_SSL to Gmail; raises on auth failure or SMTP error
    ▼
state.py — save_seen_roles()
    │ persists ONLY the roles that were actually scored
    │ merges into seen_roles.json, prunes entries older than 14 days
```

---

## Key Architectural Decisions

### 1. Single-file IMAP source (no DB, no queue)
Gmail IMAP is the canonical data store. No local DB, no job cache beyond the dedup TTL.
Simplifies setup (no Postgres, no Redis) at the cost of re-fetching email metadata on
every run. Fine for personal use at 1-3 runs/day.

### 2. Batch Sonnet call with cached system prompt
All jobs are scored in a single `messages.create()` call. The profile is sent as a
`cache_control: ephemeral` system prompt block, so subsequent calls within the 5-minute
cache window pay reduced input token costs. Trade-off: one large call is more fragile
than N small calls (partial-JSON recovery in `_recover_partial_json()` mitigates this).

### 3. Two-pass scoring: Haiku pre-screen → Sonnet
Activated when role count exceeds `MAX_JOBS_PER_RUN`. Haiku sees only title+company
(no JD fetch), returns Y/N per role. Only Y's proceed to enrichment + Sonnet scoring.
Reduces cost by ~40–60% on large catch-up runs without sacrificing quality on survivors.

### 4. seen_roles.json as the dedup gate (not email Message-IDs)
Role identity is `md5(normalize(title + company))[:12]` — not the email Message-ID.
This means the same job appearing in two different LinkedIn alert emails is deduplicated
correctly. TTL is 14 days; pruned on every write. Only roles that are actually scored
get written — capped/filtered roles remain unseen and reappear in future runs.

### 5. Profile in ~/.job_pipeline/profile.yaml (outside the repo)
All personal data lives outside the repo. The repo has `.env.example` and
`examples/profile_*.yaml` (fake personas). This is the key security boundary.

### 6. Verdict re-validation in rank_jobs()
Claude is instructed on score thresholds but doesn't guarantee compliance. `rank_jobs()`
re-derives the verdict from the score against the profile's configured thresholds before
sorting. This prevents a score-62 role from being surfaced as "apply".

### 7. Two HTML renderers
Primary: `ts-node render.ts` (React Email — rich, pixel-perfect).
Fallback: `build_digest_html()` (pure Python inline HTML).
The fallback activates if Node isn't available or ts-node fails — the digest is always
delivered even if the rich renderer isn't set up.

---

## Extensibility Guide

The three most likely extension points, and how to use them.

### Adding a new job source (e.g., Indeed, Greenhouse RSS, Lever)

The `sources/` package is the only layer that knows about email providers or HTTP feeds.
Everything downstream (dedup, scoring, rendering) works on a list of `Job` dicts.

**Steps:**

1. Create `sources/<provider>.py`. Implement:
   ```python
   def fetch_jobs(since_dt: datetime) -> list[dict]:
       """Return list of Job dicts: title, company, location, url, job_id, source."""
   ```
2. Register it in `sources/__init__.py` → `fetch_alerts()`. Add a condition on a new
   `ALERT_SOURCE` env var (default: `"linkedin"`):
   ```python
   elif source == "indeed":
       from .indeed import fetch_jobs
       return fetch_jobs(since_dt)
   ```
3. Add `ALERT_SOURCE=indeed` to `.env.example`.
4. Write a unit test in `tests/test_core.py` covering at least: empty result, single job,
   dedup-key shape (`title + company` must be consistent with existing dedup logic).

The pre-screen, JD enrichment, scoring, rendering, and delivery steps are unchanged —
they only see the `Job` dict list, not the source.

**What you don't need to touch:** `pipeline.py`, `score.py`, `render.py`, `state.py`.

---

### Adding a new digest output format (e.g., Slack message, Markdown file, webhook)

Output is isolated to `render.py`. The pipeline calls two functions:
- `render_html(jobs) -> str` — builds the HTML string
- `send_digest(html, run_date)` — delivers it

To add a new output format:

1. Add a new delivery function in `render.py`, e.g. `send_slack(jobs, run_date)`.
2. In `pipeline.py`, check a new `OUTPUT_MODE` env var and call the appropriate function.
   Keep `send_digest()` as the default so existing users are unaffected.
3. Add `OUTPUT_MODE=slack` to `.env.example`.

If the new format doesn't use HTML (e.g., Slack blocks), bypass `render_html()` entirely
and work directly from the ranked `jobs` list that `rank_jobs()` returns.

---

### Extending the profile schema

`profile.yaml` is loaded by `profile.py → load_profile_yaml()` and rendered into the
Sonnet system prompt by `render_profile()`. The scoring prompt reads whatever is in the
profile — Claude interprets free-text fields, so adding new fields often *just works*
without code changes.

**For structured new fields** (ones that affect code behavior, not just prompt text):

1. Add the field to `init_cmd.py` wizard if it should be user-configurable at setup.
2. Read it in the relevant module (e.g., a new `scoring.deal_breaker_weight` field would
   be read in `score.py` alongside the existing threshold reads).
3. Update `SPEC.md §6` if it's a user-visible configuration option.
4. Update `M-04` validation in `init_cmd.py` if the new field is required.

**Schema versioning:** There is no formal version field in `profile.yaml`. If you add a
required field, make the code tolerate its absence (default gracefully) so existing
profiles don't break on upgrade. Document the new field in `README.md § Configuration`.

---

```
~/.job_pipeline/profile.yaml   — candidate profile (scoring criteria, background)
.env                           — secrets + runtime config (never committed)
.env.example                   — safe template (committed, no real values)
```

Key `.env` fields:
| Variable | Default | Effect |
|---|---|---|
| `MAX_JOBS_PER_RUN` | `20` | Cap before interactive prompt triggers; `0` = no cap |
| `DEFAULT_LOOKBACK` | `3d` | How far back IMAP search reaches |
| `MODEL` | `claude-sonnet-4-6` | Sonnet model for full scoring |
| `PRESCREEN_MODEL` | `claude-haiku-4-5` | Haiku model for pre-screen pass |

---

## Data Files

```
~/.job_pipeline/
  profile.yaml       — structured candidate profile (generated by scorerole init)
  seen_roles.json    — {role_hash: iso_timestamp} — 14-day TTL dedup store
  logs/YYYY-MM-DD.log
  debug_email.txt    — written by scorerole debug
```

**File permissions:** `~/.job_pipeline/` is created with `mode=0o700` (owner-only directory).
`profile.yaml` and `seen_roles.json` are created with `mode=0o600` (owner-only read/write).
Log files use the system default (typically 644) and may contain job titles/companies from
warning messages — avoid sharing raw log output in bug reports without redaction.

**What leaves the machine:** Resume text (during `scorerole init`) and the full profile
(as a scoring system prompt on every run) are sent to the Anthropic API over HTTPS.
Job titles, company names, and JD snippets (≤1,500 chars each) are sent with each scoring
batch. Gmail credentials stay local — IMAP and SMTP connections go directly to Gmail (SSL).
See README § Privacy for the full data flow table.

---

## Module Map

| Module | Responsibility |
|---|---|
| `pipeline.py` | CLI entry point, orchestration, cap/prompt logic |
| `sources/linkedin.py` | IMAP fetch, email parsing, JD enrichment, retry logic |
| `sources/__init__.py` | Routing between alert modes (lookback vs. seen-ID gate) |
| `score.py` | Haiku pre-screen, Sonnet scoring, JSON recovery, rank |
| `render.py` | HTML digest building, SMTP delivery |
| `profile.py` | Profile YAML loader + `render_profile()` for scoring prompt |
| `state.py` | `seen_roles.json` read/write/prune, `_role_hash()` |
| `init_cmd.py` | `scorerole init` wizard (4-step, re-runnable); offers schedule setup at end |
| `schedule_cmd.py` | Schedule install/remove/show; builds launchd plist (macOS) or crontab line (Linux) |

---

## Automated Scheduling

### Overview

scorerole can run unattended via an OS-level scheduled job so digests arrive
without any manual command. The schedule is configured either:

- **during `scorerole init`** — offered at the end of the wizard; re-runnable
- **at any time via `scorerole schedule --set`** — standalone setup or update

### OS integration

| Platform | Mechanism | File |
|---|---|---|
| macOS | launchd user agent | `~/Library/LaunchAgents/com.scorerole.digest.plist` |
| Linux | user crontab | entry added/removed via `crontab -l / crontab -` |
| Windows | unsupported | use Task Scheduler manually |

The launchd plist uses `StartCalendarInterval` so the job fires at the
configured wall-clock time. It does **not** backfill if the machine was asleep
or off at that time. The plist encodes:
- absolute path to the scorerole binary (from the active venv at install time)
- `WorkingDirectory` pointing to the project root (where `.env` lives)
- `--lookback` derived from frequency: daily → `1d`, twice-weekly → `4d`, weekly → `7d`
- stdout/stderr redirected to `~/.job_pipeline/logs/scheduled.log`

### Subcommand pattern

Follows the same argparse conventions as the other subcommands:

```
scorerole schedule              # show current schedule + OS job health check
scorerole schedule --set        # interactive wizard (install or replace)
scorerole schedule --remove     # unload OS job, delete plist + schedule.json
```

### Persistence

`~/.job_pipeline/schedule.json` (mode 0o600) is the human-readable config:

```json
{
  "frequency":     "twice_weekly",
  "time":          "08:00",
  "scorerole_bin": "/Users/lomischen/job-alert-pipeline/venv/bin/scorerole",
  "working_dir":   "/Users/lomischen/job-alert-pipeline",
  "installed_at":  "2026-06-15T08:00:00",
  "platform":      "Darwin"
}
```

`scorerole schedule` reads this to display status and detect stale binary paths
without needing to query launchctl. The plist/crontab is always derived from
it at install time — the JSON is the source of truth.

### Key constraints

- No root or admin required — launchd user agents run in the `gui/<uid>` domain
- Venv path is baked into the plist at install time; if the venv is moved,
  `scorerole schedule` warns and `scorerole schedule --set` reinstalls cleanly
- `run_pipeline()` is entirely unchanged — the scheduled job is `scorerole --lookback Xd`
- SMTP failure behavior (T-07) applies equally to scheduled runs

---

## Known Issues & Tech Debt

### P1 — Would break things silently

**~~T-01: score_jobs_batch() makes a single large API call~~** *(resolved June 2026)*
Chunking into ≤15 roles/call with `_SCORE_CHUNK_SIZE = 15` and `max_tokens = 8192`
eliminates the truncation risk. `_recover_partial_json()` remains as a safety net.
Closed.

**T-02: enrich_jobs() is sequential**
JD fetches run one at a time with a 0.4s delay. 100 jobs = ~60 seconds of HTTP time
before scoring even starts. LinkedIn rate-limits aggressively, so parallelism is risky,
but the current delay is conservative. Consider `asyncio` with a semaphore-bounded pool.

**T-03: IMAP search caps at last 100 / 30 emails**
`_fetch_emails()` uses `all_ids[-100:]` for lookback runs and `[-30:]` for the standard
flow. A gap of more than ~100 LinkedIn emails between runs will silently miss older ones.
Should be configurable or adaptive.

### P2 — UX / correctness gaps

**T-04: Pre-screen uses only title+company — no salary, no location**
The Haiku pre-screen filters on role fit but can't see salary, remote policy, or
location from the title alone. A role that passes pre-screen but is 100% on-site in
a location the candidate can't work from still gets full Sonnet scoring. Not a bug,
but a known precision gap.

**T-05: scorerole config subcommand not re-added (resolved)**
`scorerole config` was removed; profile editing is handled through `scorerole init`
(Quick edits / Open in editor modes). This is intentional — see SPEC Q6. No separate
config subcommand is planned.

**T-06: Digest duplication on reset + rerun**
`scorerole reset` + `scorerole --all --lookback 14d` re-scores roles that were already
correctly scored and delivered in a prior digest. The user receives them again. No
dedup across digest emails — the sent digest is the only record. Acceptable for a
personal tool; worth noting for multi-user scenarios.

**T-07: SMTP failure leaves roles unseen — they retry on the next run**
`save_seen_roles()` is called AFTER `send_digest()`. If SMTP raises → `SystemExit(1)` →
`save_seen_roles` never runs → the roles remain unseen and will be re-scored on the next
run. This is intentional (Q8 in SPEC): if delivery fails, you shouldn't lose the roles
permanently. Downside: if SMTP is broken for many runs, you accumulate re-scoring costs.
Consider persisting the rendered HTML locally on SMTP failure for manual resend.

**T-08: init_cmd.py profile schema validation (M-04) only checks top-level keys**
The M-04 fix checks for `candidate`, `target`, `experience` but not their sub-fields
(e.g., `candidate.name`, `target.roles`). A profile with empty dicts for those sections
passes validation but would produce a nearly-blank scoring prompt.

**~~T-12: _build_prescreen_context() hardcoded candidate function to "Product Management"~~** *(resolved June 2026)*
Pre-screen context now reads `target.roles[0]` from `profile.yaml` so non-PM profiles
(ML engineers, designers) get the correct function signal for Haiku filtering.
Closed.

### P3 — Future extensibility

**T-09: Only LinkedIn supported**
`sources/` is designed to support multiple job sources (Indeed, Greenhouse RSS, etc.),
but only LinkedIn IMAP parsing is implemented. The `fetch_alerts()` router in
`sources/__init__.py` is the right extension point.

**T-10: No multi-user / team support**
Profile path, data dir, and credentials are all single-user. Each user needs their own
install. Parameterizing `DATA_DIR` and allowing `--profile-dir` flag would enable
shared infra.

**T-11 (P3): Scheduled runs silently skip missed windows**
`StartCalendarInterval` fires at wall-clock time only. If the machine is off or
asleep at that time, the job is skipped — not queued. For a Monday+Thursday schedule,
sleeping through Thursday means the next run is Monday (7-day gap instead of 4-day).
Workaround: run `scorerole --lookback 7d` manually after a gap, or widen
`DEFAULT_LOOKBACK` in `.env` for scheduled runs.
