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

## Configuration Hierarchy

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
| `init_cmd.py` | `scorerole init` wizard (4-step, re-runnable) |

---

## Known Issues & Tech Debt

### P1 — Would break things silently

**T-01: score_jobs_batch() makes a single large API call**
With 200+ roles (after pre-screen), a single Sonnet call risks hitting `max_tokens=4096`
for the response. `_recover_partial_json()` salvages truncated arrays, but roles beyond
the truncation point get marked as "skipped" silently. Mitigation: chunk into batches of
25–30 for large runs.

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

### P3 — Future extensibility

**T-09: Only LinkedIn supported**
`sources/` is designed to support multiple job sources (Indeed, Greenhouse RSS, etc.),
but only LinkedIn IMAP parsing is implemented. The `fetch_alerts()` router in
`sources/__init__.py` is the right extension point.

**T-10: No multi-user / team support**
Profile path, data dir, and credentials are all single-user. Each user needs their own
install. Parameterizing `DATA_DIR` and allowing `--profile-dir` flag would enable
shared infra.
