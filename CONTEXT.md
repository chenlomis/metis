# metis — Project Context

Short version of the repo for new agent sessions. Read this first, then pull specific
files only when needed.

---

## What It Is
metis is a privacy-first CLI for job search triage. It pulls saved job alerts, optionally
checks company career pages, scores roles against a local profile with Claude, emails a
ranked digest, and keeps an application tracker up to date.

## Current State (as of 2026-06-30)
- **Done:** Full pipeline (fetch → dedup → extract → score → digest email → tracker write), `metis init`, proactive sources, non-LinkedIn email alert sources, scheduling (launchd/cron with retry), `metis track`, `metis feedback`, `metis summary`, scoring traceability (`trace.py` → `~/.job_pipeline/runs.jsonl`), React Email digest templates with Python fallback, format regression tests, tracker input validation, and role queueing for capped runs.
- **In progress:** config-as-parameters cleanup for cleaner library/MCP use, more reliable tracker parsing across ATS templates, and public-launch documentation polish.
- **Near-term backlog:** one-command install, PyPI packaging as `metis-job`, MCP server wrapper, Outlook/Microsoft 365 support, and broader source adapters.
- **Later:** Docker, employer-lens scoring, evaluation harness, and web/app surfaces only if OSS usage proves demand.

## Mental model: config boundary

| File | Owns |
|---|---|
| `~/.job_pipeline/profile.yaml` | Candidate identity + search preferences |
| `~/.job_pipeline/email_sources.yaml` | Extra non-LinkedIn alert email senders |
| `~/.job_pipeline/config.yaml` *(planned)* | Thresholds, schedule, system knobs |
| `~/.job_pipeline/feedback.md` | Free-text calibration notes injected into scoring prompt |

`profile.yaml` still holds most user preferences. Extra alert senders now live in
`email_sources.yaml`; the remaining split is the target mental model for v2 and should
guide new code even before the files are fully separated.

## Profile Schema (simplified)

```yaml
candidate:
  name:
  email:
  location:
  location_preference:       # remote / flexible / local
  open_to_relocation: []
  experience: []
  education: []
  strengths: []

target:
  roles: []
  level:

aspirations:
  track:                     # ic / management / flexible
  direction:
  company_types: []
  avoid_company_types: []

preferences:
  company_stage: []
  company_size:
  company_environment:
  industry_targets: []
  industry_avoid: []
  base_salary_target_usd:

deal_breakers: []
salary_floor_usd:

inferred:
  customer_types: []
  degree_level:

track:
  llm_fallback:

notes:
```

Note: `experience`, `education`, `strengths` live under `candidate` (come from resume/LinkedIn only).
`deal_breakers` and `salary_floor_usd` are top-level (come from Steps 2–3 freeform input).
Removed from the user-facing profile: `green_flags`, `yellow_flags`, `red_flags` (scoring-internal, not preferences).

## File map

```
metis/
  cli.py            — CLI entry point and subcommand routing
  pipeline.py       — digest orchestration and cap/queue logic
  init_cmd.py       — metis init profile wizard (InquirerPy + Rich)
  theme.py          — ALL colors, styles, print helpers (single source of truth; never inline colors elsewhere)
  profile.py        — load/save ~/.job_pipeline/profile.yaml
  extract.py        — Haiku Layer 1: extracts 27 structured fields from JD text
  score.py          — Haiku pre-screen + Sonnet Layer 2 scoring; rank_jobs()
  render.py         — React Email/Python HTML digest rendering
  deliver.py        — SMTP delivery
  schedule_cmd.py   — launchd (macOS) / cron (Linux) scheduling wizard
  state.py          — seen_roles.json TTL store; _role_hash()
  trace.py          — write_trace() → ~/.job_pipeline/runs.jsonl (one record/job, every run)
  track.py          — parse confirmation/rejection emails → Applications.xlsx
  tracker.py        — Applications.xlsx write helpers (called by track.py)
  feedback.py       — metis feedback: collect notes → append to feedback.md → injected into scoring
  sources_cmd.py    — metis sources: list/add/remove/on/off email and company sources
  sources/
    __init__.py     — fetch_alerts() router (LinkedIn + proactive)
    linkedin.py     — IMAP fetch, email parse, JD enrichment
    proactive.py    — Greenhouse/Lever API scraping + Playwright fallback
    companies.yml   — curated company pool
```

## Data files (runtime, live outside repo at ~/.job_pipeline/)
```
profile.yaml          — candidate profile (written by metis init)
seen_roles.json       — role_hash → timestamp, 30-day TTL dedup store
runs.jsonl            — append-only scoring trace (one JSON line per job per run)
applications.xlsx     — job tracker (written by metis track)
feedback.md           — calibration notes injected into every scoring prompt
schedule.json         — active schedule config (written by metis schedule set)
role_queue.json       — pre-screen survivors deferred by MAX_JOBS_PER_RUN cap
skipped_roles.json    — Limited Match metadata for summary/reporting
email_sources.yaml    — extra alert email sources
logs/                 — daily run logs + scheduled.log
```

## Key invariants (do not break these)
- `theme.py` is the only place colors live. Use `Style(color=THEME["..."])` objects — never f-string `style=f"color:#hex"` (Rich rejects that syntax).
- `proactive.py` returns same job dict shape as LinkedIn (`source: "proactive"`, pre-filled `jd`); `enrich_jobs()` skips proactive jobs.
- `open_to_remote` still exists in profile for backward compat; `location_preference` is the canonical field.
- Profile lives at `~/.job_pipeline/profile.yaml` (outside repo). Repo has `profile.template.yaml` and `examples/`.
- Python 3.9 compat: no `X | Y` union type syntax; use `Optional[X]` or string annotations.
- `trace.py` is the observability layer — `write_trace()` must be called for every job (prescreened, filtered, and scored). Do not remove or skip these calls.
- Several modules still call `os.getenv()` at import time (score.py, extract.py, linkedin.py). This is a known library hygiene issue — do not make it worse. The config-as-parameters refactor will fix it.
- **`render.py` email format is locked.** Section labels: "Solid Match / Moderate Match / Limited Match". Legend: "Strengths / Caution / Blockers". Stat tiles: "Evaluated / Solid Match / Moderate Match". Buttons: filled with verdict color, white text. Greeting: personalized when `candidate_name` set. Score breakdown must not appear in cards. Skipped section is a flat 2-col table. Enforced by `tests/test_render_format.py` — run after any render.py edit.
- **`_role_hash()` in `state.py` is frozen.** MD5, `[:12]` slice, same normalization regex. Changing it invalidates `seen_roles.json` and causes a flood re-send.
- **`_HEADERS` column order in `tracker.py` is frozen.** Existing `applications.xlsx` data relies on positional column indices. Header text is safe to rename; order is not.
- **`pipeline.py` stage order is load-bearing.** Deal-breaker split must run after `new_role_timestamps` is built and before `render_html`. `save_seen_roles` must run after `send_digest`. Do not reorder stages without explicit instruction.
- **`score.py` eval schema and `render.py` are a coupled contract.** Verdict enum, 6 dimension names, 2 leveragePoints, 1 frictionPoint. Change both together or not at all.

## Scoring pipeline summary
```
Gmail IMAP → email parse (3-case shift detection) → dedup
→ [cap / Haiku pre-screen / role queue] → JD fetch (LinkedIn HTTP, 3x retry)
→ Haiku Layer 1 extract (27 fields) → hard gates (jd_blank, salary_floor)
→ Sonnet Layer 2 score (6-dim rubric, prompt-cached system prompt)
→ rank_jobs() → deal-breaker split → write_trace()
→ React Email digest (Python fallback; format locked) → Gmail SMTP
→ save_seen_roles() → tracker write gate → applications.xlsx
```

## Where to look for more
- `ARCHITECTURE.md` — deep dive: all decisions, data flow diagram, extensibility guide, tech debt
- `DECISIONS.md` — why specific choices were made (concise, chat-optimized)
- `CLAUDE.md` — full command reference
- `metis/DESIGN.md` — UI/UX design rationale for init
