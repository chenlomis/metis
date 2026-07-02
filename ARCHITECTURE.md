# metis — Architecture & Design Notes

## What It Does

metis is a personal CLI tool that reads job alert emails via OAuth or IMAP fallback, scores
each role against the user's structured profile using the configured LLM provider, and
delivers a ranked HTML digest to Gmail. Anthropic is the default provider; OpenAI is
supported across public AI tasks through the same provider boundary. It runs on demand or
on a schedule (launchd/cron).

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
    │ 3. role_hash in seen_roles.json  (30-day cross-run TTL gate)
    ▼
pipeline.py — cap / prompt decision
    │ ≤ MAX_JOBS_PER_RUN  →  proceed
    │ > cap, interactive  →  prompt user with count + cost estimate
    │ > cap, --all flag   →  show cost estimate + require "y" confirmation (interactive)
    │                         or cap to MAX_JOBS_PER_RUN + warn (cron / non-TTY)
    │ > cap, non-TTY      →  silently cap + log warning
    ▼
score.py — prescreen_jobs_batch()  [only when going beyond cap]
    │ fast-model pass: title+company only, no JD fetch
    │ condensed profile context: target roles, seniority, deal-breakers
    │ returns ~50% of roles; falls back to full list on parse failure
    ▼
sources/linkedin.py — enrich_jobs()
    │ sequential HTTP fetch of each LinkedIn job page
    │ extracts JD text from JSON-LD JobPosting structured data
    │ extracts external ATS URL (Greenhouse/Lever/Ashby) from applyAction
    │ retries 3x with exponential backoff on 429/5xx/timeout
    ▼
extract.py — extract_jd_structs()          ← Layer 1 (NEW)
    │ extraction-model call at temperature=0 per chunk (≤10 jobs/chunk)
    │ extracts 27 structured fields: salary, work model, domain, seniority,
    │   degree req, visa, company stage, customer type, culture signals, etc.
    │ extraction failure → jd_quality="extraction_failed" structs (jd_blank gate does NOT fire — JD content exists)
    ▼
extract.py — check_hard_gates()
    │ jd_blank gate:    no JD text → verdict="filtered", skip full scorer
    │ salary_floor:     disclosed salary_max < floor*0.9 → filtered
    │ (all other gates handled by Layer 2 — require nuanced judgment)
    ▼
score.py — score_jobs_batch()
    │ full scoring model call on gate-surviving jobs only (cost savings)
    │ each job block includes [EXTRACTED CONTEXT] from Layer 1
    │ profile as cached system prompt; explicit 6-dimension rubric
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
    │ merges into seen_roles.json, prunes entries older than 30 days
    ▼
xlsx.py — write_to_tracker()
    │ _is_plausible_job_row() validation gate (blocks mis-parsed rows)
    │ appends Apply + Consider roles to applications.xlsx
    │ deduped by normalized title+company key; sorts by date descending
```

### Feedback calibration flow (separate from pipeline)

```
metis feedback
    │ load last_run.json → display last digest summary
    │ collect free-form text (blank line to finish)
    ▼
feedback.py — _llm_process()
    │ Haiku call: parse roles/dims mentioned, detect real conflicts with
    │   existing feedback.md, flag explicit permanent preferences
    │ conflict resolution: user picks new/both/discard
    │ profile-item routing: save to feedback.md / profile / both
    ▼
feedback.py — append_feedback_entry()
    │ appends tagged entry to ~/.job_pipeline/feedback.md
    │ comment header: <!-- id:fb_YYYYMMDD_xxxx | run:... | roles:... | dims:... -->
    │ followed by: ## [user] YYYY-MM-DD + raw text
    ▼
feedback.py — write_feedback_log()
    │ appends audit record to feedback_log.jsonl (never injected into prompts)
    │ fields: feedback_id, run_id, timestamp, roles, dims, text_length
    ▼
score.py — build_score_system()
    │ load_feedback_text() reads all of feedback.md (no TTL)
    │ injected as CANDIDATE CALIBRATION FEEDBACK block in Sonnet system prompt
    │ cached with system prompt — zero marginal cost per role
```

---

## Key Architectural Decisions

### 1. Single-file IMAP source (no DB, no queue)
Gmail IMAP is the canonical data store. No local DB, no job cache beyond the dedup TTL.
Simplifies setup (no Postgres, no Redis) at the cost of re-fetching email metadata on
every run. Fine for personal use at 1-3 runs/day.

### 2. Batch full-scoring call with cached system prompt
All jobs are scored in a single `messages.create()` call. The profile is sent as a
`cache_control: ephemeral` system prompt block, so subsequent calls within the 5-minute
cache window pay reduced input token costs. Trade-off: one large call is more fragile
than N small calls (partial-JSON recovery in `_recover_partial_json()` mitigates this).

### 3. Three-pass scoring: pre-screen → extraction → full scoring
Pass 1 (pre-screen): activated when role count exceeds `MAX_JOBS_PER_RUN`. The fast model sees
only title+company, returns Y/N. Reduces catch-up run cost by ~40–60%.

Pass 2 (Layer 1 extraction): always runs on enriched jobs. The extraction model at temperature=0
extracts 27 structured fields from each JD. Two Python hard gates run here:
`jd_blank` (no JD text → skip full scoring) and `salary_floor` (disclosed salary_max < floor * 0.9).
Extraction failures fall back to blank structs — scoring is never blocked.
Cost depends on provider/model choice and is partially offset by gate filtering savings.

Pass 3 (Layer 2 full scoring): only runs on roles that passed hard gates. Each job block
includes the Layer 1 `[EXTRACTED CONTEXT]` as grounding. The scoring prompt includes
an explicit 6-dimension rubric (seniority_scope, experience_relevance, compensation_fit,
culture_values, domain_background, company_stage) with weights and multipliers.

### 4. seen_roles.json as the dedup gate (not email Message-IDs)
Role identity is `md5(normalize(title + company))[:12]` — not the email Message-ID.
This means the same job appearing in two different LinkedIn alert emails is deduplicated
correctly. TTL is 30 days; pruned on every write. Only roles that are actually scored
get written — capped/filtered roles remain unseen and reappear in future runs.
The hash function (`md5`, `[:12]`, normalization regex) is intentional and frozen —
changing it invalidates all keys in `seen_roles.json` and causes a flood re-send (see D-43).

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

#### Email alert sources

Email alert sources are registered via `metis sources email add <sender>`. The system
dispatches by sender address to a format-specific parser in `sources/email_alerts.py`:

| Format | Sender pattern | Parser |
|---|---|---|
| Wellfound | `wellfound` / `angellist` | `_parse_wellfound` — structured HTML cards |
| Ladders | `theladders` | `_parse_ladders` — plain-text rows |
| ClinchTalent | `clinchtalent` | `_parse_clinchtalent` — tracking link extraction |
| iCIMS | `icims` | `_parse_icims` — job URL anchor scraping |
| Unknown | anything else | `_parse_with_llm` — LLM extraction, no code change needed |

To add a dedicated parser for a new known sender:
1. Add a parser function `_parse_<name>(body_html, body_text, company) -> list[dict]` in `sources/email_alerts.py`
2. Add a detection case in `detect_format()` keyed on sender domain substring
3. Add the format label in `format_label()`
4. Add a dispatch case in `fetch_email_alerts()`

Unknown senders automatically use `_parse_with_llm` — no steps needed for one-off sources.

#### Proactive career-page sources

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
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | unset | Gmail OAuth desktop-app credentials for `metis config access` / `metis init` inbox connection |
| `OUTLOOK_CLIENT_ID` | unset | Microsoft Graph public-client ID for Outlook OAuth |
| `METIS_EMAIL_PROVIDER` | auto | Optional override: `gmail_oauth`, `outlook_oauth`, or `imap`; otherwise latest successful OAuth connection wins |
| `MAX_JOBS_PER_RUN` | `40` | Cap before interactive prompt triggers; `0` = no cap |
| `DEFAULT_LOOKBACK` | `3d` | How far back IMAP search reaches |
| `METIS_LLM_PROVIDER` | `anthropic` | Digest scoring provider. Accepts normalized aliases such as `open_ai` and `Claude`. |
| `ANTHROPIC_MODEL` / `OPENAI_MODEL` | provider default | Full scoring model |
| `ANTHROPIC_PRESCREEN_MODEL` / `OPENAI_PRESCREEN_MODEL` | provider default | Fast model for pre-screen pass |
| `ANTHROPIC_EXTRACT_MODEL` / `OPENAI_EXTRACT_MODEL` | provider default | Model for structured JD extraction |
| `MODEL`, `PRESCREEN_MODEL`, `EXTRACT_MODEL` | provider default | Backward-compatible generic model variables |

### LLM Provider Boundary

All public AI tasks should go through `metis.llm`:

- `create_llm_client(provider, api_key)` constructs the provider client.
- `complete_text(...)` returns provider-neutral `LLMResponse(text, usage, raw)`.
- `normalize_provider(...)` accepts user-facing aliases such as `OpenAI`, `open_ai`,
  `Claude`, and mixed casing.
- `resolve_stage_models(...)` chooses full, prescreen, and extract models with
  provider-specific env vars taking priority over generic `MODEL` variables.

Call sites should not depend on Anthropic or OpenAI SDK response shapes. Provider quirks
belong in the adapter or in narrow parser recovery functions.

### Profile Normalization

`metis init` intentionally separates raw extraction from deterministic post-processing.
The LLM captures the candidate's literal resume/profile/Step 2/Step 3 evidence; then
`normalization.py` maps common free-text signals into canonical profile fields:

- `target.role_family`, `target.roles`, `target.level`
- `aspirations.track`, `aspirations.direction`, `aspirations.company_types`
- `preferences.company_stage`, `preferences.company_scale`, `preferences.team_environment`
- `candidate.location_preference`
- `inferred.customer_types`

This layer is provider-agnostic and runs for Anthropic, OpenAI, and future adapters once
their raw output parses into a profile dict. `unknown` means no usable signal was present;
`other` means the user gave a signal that sits outside the current taxonomy.

Dev-only env vars (never put in `.env`):
| Variable | Effect |
|---|---|
| `METIS_PROFILE` | Override profile path — points `profile.py` at a different YAML without touching `~/.job_pipeline/profile.yaml`. Used by `run_persona_test.py`. Unset with `unset METIS_PROFILE`. |

---

## Data Files

```
~/.job_pipeline/
  profile.yaml       — structured candidate profile (generated by metis init)
  gmail_token.json   — Gmail OAuth token cache (owner-only, never committed)
  outlook_token.json — Outlook OAuth token cache (owner-only, never committed)
  email_provider.json — active OAuth provider marker; latest successful connection wins
  seen_roles.json    — {role_hash: iso_timestamp} — 30-day TTL dedup store
  logs/YYYY-MM-DD.log
  debug_email.txt    — written by metis debug
```

**File permissions:** `~/.job_pipeline/` is created with `mode=0o700` (owner-only directory).
`profile.yaml`, OAuth token files, `email_provider.json`, and `seen_roles.json` are created
with `mode=0o600` (owner-only read/write).
Log files use the system default (typically 644) and may contain job titles/companies from
warning messages — avoid sharing raw log output in bug reports without redaction.

**What leaves the machine:** Resume text during `metis init`, feedback notes during
`metis feedback`, and scoring inputs during `metis` runs go to the selected LLM provider.
OAuth tokens and legacy Gmail credentials stay local. Browser OAuth goes directly to Google
or Microsoft; token exchange uses `state` validation and PKCE (`S256`) before saving tokens.
See README § Privacy for the full data flow table.

### Email OAuth and Provider State

`metis init` and `metis config access` can connect Gmail or Outlook via OAuth. Provider code
lives in `metis/auth/gmail_oauth.py` and `metis/auth/outlook_oauth.py`; shared security
helpers live in `metis/auth/oauth_security.py`.

Security invariants:

- Authorization URLs include a random `state` value and PKCE `code_challenge` with
  `code_challenge_method=S256`.
- Callback handling must reject missing or mismatched `state` before exchanging the code.
- Token exchange must include the matching `code_verifier`.
- Reconnect flows force account selection (`prompt=consent select_account` for Gmail,
  `prompt=select_account` for Outlook).
- Token files and `email_provider.json` are local-only runtime state and must never be
  committed.

Provider selection is deterministic: `METIS_EMAIL_PROVIDER` overrides everything; otherwise
`email_provider.json` identifies the latest successful OAuth provider; without that marker,
the newest OAuth token file is used for backward compatibility; if no OAuth token exists,
the legacy Gmail IMAP fallback is used.

Current rollout boundary: non-LinkedIn email alert sources use the provider-neutral
`sources/email_fetcher.py`. Some older workflows still contain Gmail IMAP/SMTP-specific
call sites (`sources/linkedin.py`, tracker/backfill, and main digest delivery) until the
email-provider abstraction is wired through end-to-end.

---

## Module Map

| Module | Responsibility |
|---|---|
| `cli.py` | CLI parsing, subcommand registration, and command routing |
| `auth/` | Gmail/Outlook OAuth, active provider state, PKCE/state helpers |
| `config_access_cmd.py` | `metis config access`: connect/reconnect Gmail or Outlook via browser OAuth |
| `pipeline.py` | Digest pipeline orchestration and cap/prompt logic — stage order is load-bearing, do not reorder |
| `sources/linkedin.py` | IMAP fetch, email parsing (3-case positional shift detection), JD enrichment, IMAP retry |
| `sources/__init__.py` | Routing between alert modes (lookback vs. seen-ID gate) |
| `llm/` | Provider-neutral LLM boundary, provider normalization, per-stage model resolution |
| `extract.py` | Layer 1 structured extraction (27 fields), hard gate checker, context formatter |
| `score.py` | Fast pre-screen, full scoring (Layer 2), JSON recovery, rank — eval schema is a locked contract with render.py |
| `render.py` | HTML digest building, SMTP delivery — output format locked; see CLAUDE.md constraint #0 |
| `profile.py` | Profile YAML loader + `render_profile()` for scoring prompt |
| `state.py` | `seen_roles.json` read/write/prune, `_role_hash()` — hash function frozen, do not change |
| `xlsx.py` | `applications.xlsx` write helpers; `_is_plausible_job_row()` validation gate; column order frozen |
| `track.py` | Parse confirmation/rejection emails → update tracker status |
| `trace.py` | `write_trace()` → `runs.jsonl`; called for every job regardless of verdict |
| `init_cmd.py` | `metis init` wizard (4-step, re-runnable); offers schedule setup at end |
| `schedule_cmd.py` | Schedule install/remove/show; builds launchd plist (macOS) or crontab line (Linux) |
| `feedback.py` | `metis feedback`: collect → configured LLM parse → confirm → append to `feedback.md` |

---

## Automated Scheduling

### Overview

metis can run unattended via an OS-level scheduled job so digests arrive
without any manual command. The schedule is configured either:

- **during `metis init`** — offered at the end of the wizard; re-runnable
- **at any time via `metis schedule set`** — standalone setup or update

### OS integration

| Platform | Mechanism | File |
|---|---|---|
| macOS | launchd user agent | `~/Library/LaunchAgents/com.metis.digest.plist` |
| Linux | user crontab | entry added/removed via `crontab -l / crontab -` |
| Windows | unsupported | use Task Scheduler manually |

The launchd plist uses `StartCalendarInterval` so the job fires at the
configured wall-clock time. It does **not** backfill if the machine was asleep
or off at that time. The plist encodes:
- absolute path to the metis binary (from the active venv at install time)
- `WorkingDirectory` pointing to the project root (where `.env` lives)
- `--lookback` derived from frequency: daily → `1d`, twice-weekly → `4d`, weekly → `7d`
- stdout/stderr redirected to `~/.job_pipeline/logs/scheduled.log`

### Subcommand pattern

Follows the same argparse conventions as the other subcommands:

```
metis schedule          # show current schedule + OS job health check
metis schedule set      # interactive wizard (install or replace)
metis schedule remove   # unload OS job, delete plist + schedule.json
```

### Persistence

`~/.job_pipeline/schedule.json` (mode 0o600) is the human-readable config:

```json
{
  "frequency":     "twice_weekly",
  "time":          "08:00",
  "metis_bin": "/path/to/metis/venv/bin/metis",
  "working_dir":   "/path/to/metis",
  "installed_at":  "2026-06-15T08:00:00",
  "platform":      "Darwin"
}
```

`metis schedule` reads this to display status and detect stale binary paths
without needing to query launchctl. The plist/crontab is always derived from
it at install time — the JSON is the source of truth.

### Key constraints

- No root or admin required — launchd user agents run in the `gui/<uid>` domain
- Venv path is baked into the plist at install time; if the venv is moved,
  `metis schedule` warns and `metis schedule set` reinstalls cleanly
- `run_pipeline()` is entirely unchanged — the scheduled job is `metis --lookback Xd`
- SMTP failure behavior (T-07) applies equally to scheduled runs

---

## Target Persona (updated June 2026)

The better-fit user is a **passive job seeker** — someone selectively open to the right
role, not urgently mass-applying. Key implications:

- Biweekly or weekly digest cadence is preferred over daily
- Score quality matters more than throughput — they'll act on fewer, better recommendations
- They're willing to configure once and let it run unattended
- They will NOT apply to every role regardless of score (unlike active seekers)
- Churn is inherently high (users leave when hired) — acceptable given the use case

**Design consequence:** Scoring precision and filtering quality are more important than
speed or volume. The two-layer Haiku extraction → Sonnet scoring architecture is the
right call for this persona.

---

## Interface Roadmap (decided June 2026)

metis has one current interface (CLI) and a planned sequence of additional surfaces.
Each stage is independent — later stages don't replace earlier ones.

```
Stage 0 (done)   CLI              metis [subcommand]
                                  Entry point for the author and technical users.

Stage 1 (next)   MCP server       metis/mcp_server.py, local subprocess
                                  Claude Code users add via: claude mcp add metis
                                  No hosting required. Runs on user's machine with
                                  their own credentials. Natural language → tool calls
                                  → real pipeline.
                                  Prerequisite: config-as-parameters refactor.

Stage 2           Python package  pip install metis (PyPI)
                                  Developers can import score_jobs(), extract_jd() etc.
                                  into their own agents or workflows.
                                  Prerequisite: stable public API + Stage 1 complete.

Stage 3           Docker image    docker run -p 3000:3000 metis/metis
                                  Browser-based setup wizard. No Python/venv/cron.
                                  Still runs locally — no user data leaves machine.
                                  Prerequisite: demand signal from Stage 1/2 users.

Stage 4           Web app         OAuth Gmail login, server-side scheduling.
                                  Only if demonstrated demand AND compliance overhead
                                  is justified (Google OAuth verification for >100 users).
                                  Do not build speculatively.
```

### MCP server — implementation notes

Thin wrapper around existing functions. Does NOT require a rewrite.

Tools to expose:
- `score_jobs`       → wraps `run_pipeline()`
- `get_last_digest`  → reads last_run.json, returns summary
- `check_tracker`    → reads Applications xlsx, returns recent rows
- `run_track`        → wraps `run_track()` from track.py
- `get_profile`      → returns current profile.yaml summary

**Key prerequisite — config as parameters:**
MCP server runs as a subprocess in an arbitrary working directory and cannot rely on
`.env` being present. All config (api_key, gmail_address, gmail_app_password,
profile_path) must be passable as explicit parameters to core functions. The CLI layer
continues reading from `.env` and passing values through — the change is that the
library layer accepts explicit parameters rather than reading env vars at import time.

**Discovery:** Publish to MCP registry after Stage 1 is stable. The CLI audience is
the author; the MCP audience is Claude Code users — technical, self-selecting, more
likely to share.

---

## Reusable Package Design (for Stage 2)

Three requirements before publishing to PyPI:

1. **Clean public API** — 3–4 stable importable functions:
   ```python
   from metis import score_jobs, extract_jd, load_profile
   profile = load_profile("~/.job_pipeline/profile.yaml")
   results = score_jobs(jobs, profile, api_key="sk-ant-...")
   ```
   CLI and MCP server become consumers of this API, not the inverse.

2. **Config as parameters** — same refactor as MCP prerequisite above.

3. **Stable surface first** — don't publish to PyPI while core functions are still
   changing signatures. Finish `metis summary` and the feedback loop first.

---

## Feedback Capture Design (decided June 2026)

**No silent auto-calibration.** All calibration is explicit and user-initiated.

**Signal source:** `metis track` detects deviations between scored verdicts and
actual apply behavior (confirmation emails for skipped roles; no confirmation for
apply-verdicted roles).

**Delivery:** Deviation flag written to `~/.job_pipeline/deviation.json` when threshold
is crossed (e.g. 2+ skipped roles applied to). Next digest reads the flag and appends
a footer nudge:

> "Last week: you applied to 2 roles scored 'skip' and passed on 1 scored 'apply'.
> If scores feel off, run `metis init → Quick edits` to adjust thresholds."

Flag cleared after nudge is shown. User decides whether to act.

**Explicit feedback:** `metis feedback` injects free-text notes into the scoring
system prompt via `~/.job_pipeline/feedback.md`.

**What we deliberately don't do:**
- Auto-adjust score thresholds based on apply behavior
- Prompt the user mid-run to explain deviations
- Track individual role outcomes for ML training

---

## OSS Strategy (decided June 2026)

OSS (public repo) and a hosted web app are not mutually exclusive. Recommended sequence:

1. OSS the repo when the core is stable (finish report + feedback loop first)
2. Write one honest launch post — "Show HN" or r/jobsearch. Meta angle works:
   "I built a tool to fix my own LinkedIn job search" is a genuine hook.
3. MCP registry submission alongside or just after OSS launch
4. Facebook job search groups / PM communities are underrated surfaces —
   warmer referral dynamics than HN, directly reaches the target persona
5. Web app only if there's demonstrated inbound demand from OSS users

---

## Known Issues & Tech Debt

### Recently resolved (June 2026)

**`jd_quality: "extraction_failed"` distinguishable from `"blank"`** (June 28 2026)
`_extract_chunk()` in `extract.py` previously fell back to `dict(_BLANK_STRUCT)` on JSON parse errors — `_BLANK_STRUCT` has `jd_quality: "blank"`, causing the `jd_blank` hard gate to fire even when the JD had 15K+ chars of content. All three fallback sites now return `{**_BLANK_STRUCT, "jd_quality": "extraction_failed"}`. The `check_hard_gates()` gate only fires on `"blank"`. Enforced by `TestHardGates` in `test_core.py`.

**Company name normalization before hashing** (June 28 2026)
`_canonical_company()` in `state.py` strips branding/legal suffixes (AI, Labs, Inc, Corp, etc.) before `_role_hash()` is called. "NVIDIA AI" and "NVIDIA" now hash identically, preventing duplicate digest cards across LinkedIn email and Greenhouse API variants. `_role_hash()` itself is untouched (frozen per D-43).

**domain_background dimension no longer penalizes cross-industry roles** (June 28 2026)
Score prompt updated: "foreign" (10) only applies when the role requires domain-specific niche expertise the candidate lacks (healthcare regulatory, finance compliance, etc.). A different industry alone is not a "foreign" score — when `industry_avoid` is empty, no industry is penalized by default. AI/ML and B2B SaaS experience is explicitly noted as broadly transferable.

**`--no-limit` doom loop guard** (June 28 2026)
`--no-limit` in interactive mode now requires explicit `y` confirmation after showing the cost estimate. In non-interactive (cron) mode, `--no-limit` is silently downgraded to `MAX_JOBS_PER_RUN` with a warning — prevents runaway spend on scheduled runs.

**CI via GitHub Actions** (June 28 2026)
`.github/workflows/test.yml` runs `pytest tests/ -q` on Python 3.11 and 3.12 on every push/PR to main. Requires `ANTHROPIC_API_KEY` secret in GitHub repo settings.

**Format regression prevention** (June 21 2026)
Silent format regressions in `render.py` (legend labels, stat tile label, score breakdown visibility) were caused by agents making unsolicited changes during unrelated bug fixes. Fixed at two layers: CLAUDE.md constraint #0 names the locked strings; `tests/test_render_format.py` (18 assertions) enforces them after every edit. Format-breaking changes now fail tests immediately rather than surfacing in the next email.

**LinkedIn positional parser: 3-case shift detection** (June 21 2026)
`extract_jobs()` in `sources/linkedin.py` now detects when `before_lines[-2]` (expected company slot) looks like a location string and shifts all three fields up one position. Two additional cases handle company-name-in-title-slot patterns. `xlsx.py` adds a second layer of defense via `_is_plausible_job_row()` which blocks mis-parsed rows at the xlsx write point.

**launchd scheduled run reliability** (June 20 2026)
DNS failures at Mac wake-from-sleep caused scheduled runs to fail with `[Errno 8] nodename nor servname provided`. Fixed with two layers: 3-attempt code-level retry (30s backoff) in `sources/linkedin.py`, plus `KeepAlive.SuccessfulExit=false` + `ThrottleInterval=900` in the launchd plist for a 15-minute OS-level retry if all code retries fail.

**init_cmd.py fully migrated from questionary → InquirerPy** (June 17 2026)
All `_ask*` helpers use InquirerPy with consistent `"  › "` pointer and centralized `INQUIRER_STYLE` (created via `get_style()` in theme.py — must be an `InquirerPyStyle` object, not a plain dict). Fixed: cursor duplication on UP/DOWN nav, inconsistent indentation, user input color buried in gray.

**`score_jobs_batch` now public + accepts profile dict** (June 16 2026)
`build_score_system(profile: dict)` is public and wired through from pipeline so there's no double disk read. `score_jobs_batch(client, jobs, profile=None)` accepts optional profile; shim `_build_score_system()` retained for test backward compat.

**`metis feedback` subcommand added** (June 16 2026)
`~/.job_pipeline/feedback.md` is injected into every scoring run system prompt. Use `metis feedback` to add calibration notes after reviewing a digest.

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

**T-05: metis config subcommand not re-added (resolved)**
`metis config` was removed; profile editing is handled through `metis init`
(Quick edits / Open in editor modes). This is intentional — see SPEC Q6. No separate
config subcommand is planned.

**T-06: Digest duplication on reset + rerun**
`metis reset` + `metis --all --lookback 14d` re-scores roles that were already
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
Workaround: run `metis --lookback 7d` manually after a gap, or widen
`DEFAULT_LOOKBACK` in `.env` for scheduled runs.

---

## Eval history (vibe code scorecard)

Scored against a 22-dimension framework across four quadrants: Value, Usability, Design, Under the Hood.

| Version | Date | Overall | Key movers |
|---|---|---|---|
| v1 | 2026-06-16 | 7.0 | Baseline — tracker + init wizard + prompt caching |
| v2 | 2026-06-18 | 7.5 | +source diversity (proactive ATS), +feedback loop, +AI moat (extract.py 27-field schema) |
| v3 | 2026-06-18 | 7.6 | +onboarding polish (InquirerPy), +career-page sources |
| v4 | 2026-06-19 | 7.8 | +observability (trace.py / runs.jsonl), +learnability, +feedback (structured JSONL) |
| v5 | 2026-06-22 | 8.0 | +reliability (3-retry backoff, launchd fallback), +polish (v7 visual refresh), test suite green |
| v6 | 2026-06-23 | 8.2 | +polish 9.0 (✓/? symbols, you/your voice), +primary `metis init`, +summary report |

Top strengths (v6): AI proprietary moat 9.0, edge case grittiness 9.0, consistency & polish 9.0, documentation 9.0.
Remaining gaps: bootstrap experience 5.5 (Gmail + venv setup still manual), performance 7.0 (no async fetch).
