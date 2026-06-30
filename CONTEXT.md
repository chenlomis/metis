# metis — Project Context (lean chat starter)

Feed this file to new Claude sessions instead of the full repo.
Then ask for specific files on demand.

---

## What it is
Personal CLI tool: pull LinkedIn job alerts → score against candidate profile → email ranked digest.
Also scrapes company career pages proactively (Greenhouse/Lever APIs + Playwright fallback).

## Current state (as of 2026-06-21)
- **Done:** Full pipeline (fetch → dedup → extract → score → digest email → tracker write), `metis init`, proactive sources (S/A/B tier company scraping), scheduling (launchd/cron with retry), `metis track`, `metis feedback`, `metis companies`, scoring traceability (`trace.py` → `~/.job_pipeline/runs.jsonl`), format regression tests (`tests/test_render_format.py`), LinkedIn 3-case positional shift detection, tracker input validation (`_is_plausible_job_row`)
- **In progress:** email parsing reliability (regex primary, LLM fallback planned)
- **Near-term backlog:** `metis summary` (score distribution + apply-rate trends), config-as-parameters refactor (MCP prerequisite), one-command install, cross-platform scheduling (Windows Task Scheduler)
- **Later:** MCP server wrapper, PyPI publish, employer-lens scoring, evaluation harness

## Mental model: config boundary

| File | Owns |
|---|---|
| `~/.job_pipeline/profile.yaml` | Candidate identity + search preferences |
| `~/.job_pipeline/sources.yaml` *(planned)* | Job sources config (proactive tiers, custom companies) |
| `~/.job_pipeline/config.yaml` *(planned)* | Thresholds, schedule, system knobs |
| `~/.job_pipeline/feedback.md` | Free-text calibration notes injected into scoring prompt |

Currently `profile.yaml` holds everything. The boundary above is the target mental model for v2 — they may stay in one file for v1 but should be treated as separate concerns in code.

## Profile schema (simplified, as of 2026-06-21)

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

notes:
```

Note: `experience`, `education`, `strengths` live under `candidate` (come from resume/LinkedIn only).
`deal_breakers` and `salary_floor_usd` are top-level (come from Steps 2–3 freeform input).
Removed: `scoring` block, `green_flags`, `yellow_flags`, `red_flags` (scoring-internal, not user-facing).

## File map

```
metis/
  pipeline.py       — CLI entry point, all subcommand routing
  init_cmd.py       — metis init (structured 4-step wizard, InquirerPy + Rich)
  init_cmd.py       — metis init (conversational profile setup)
  theme.py          — ALL colors, styles, print helpers (single source of truth; never inline colors elsewhere)
  profile.py        — load/save ~/.job_pipeline/profile.yaml
  extract.py        — Haiku Layer 1: extracts 27 structured fields from JD text
  score.py          — Haiku pre-screen + Sonnet Layer 2 scoring; rank_jobs()
  render.py         — HTML digest builder + SMTP delivery
  schedule_cmd.py   — launchd (macOS) / cron (Linux) scheduling wizard
  state.py          — seen_roles.json TTL store; _role_hash()
  trace.py          — write_trace() → ~/.job_pipeline/runs.jsonl (one record/job, every run)
  track.py          — parse confirmation/rejection emails → Applications.xlsx
  tracker.py        — Applications.xlsx write helpers (called by track.py)
  feedback_cmd.py   — metis feedback: collect notes → append to feedback.md → injected into scoring
  sources_cmd.py    — metis companies: list/add/remove/on/off company career-page sources
  sources/
    __init__.py     — fetch_alerts() router (LinkedIn + proactive)
    linkedin.py     — IMAP fetch, email parse, JD enrichment
    proactive.py    — Greenhouse/Lever API scraping + Playwright fallback
    companies.yml   — curated S/A/B/C tier company list
```

## Data files (runtime, live outside repo at ~/.job_pipeline/)
```
profile.yaml          — candidate profile (written by metis init)
seen_roles.json       — role_hash → timestamp, 30-day TTL dedup store
runs.jsonl            — append-only scoring trace (one JSON line per job per run)
applications.xlsx     — job tracker (written by metis track)
feedback.md           — calibration notes injected into every scoring prompt
schedule.json         — active schedule config (written by metis schedule set)
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
Gmail IMAP → email parse (3-case shift detection) → 3-layer dedup
→ [cap / Haiku pre-screen] → JD fetch (LinkedIn HTTP, 3x retry)
→ Haiku Layer 1 extract (27 fields) → hard gates (jd_blank, salary_floor)
→ Sonnet Layer 2 score (6-dim rubric, prompt-cached system prompt)
→ rank_jobs() → deal-breaker split → write_trace()
→ HTML digest (Python fallback; format locked) → Gmail SMTP
→ save_seen_roles() → tracker.py (_is_plausible_job_row gate) → applications.xlsx
```

## Where to look for more
- `ARCHITECTURE.md` — deep dive: all decisions, data flow diagram, extensibility guide, tech debt
- `DECISIONS.md` — why specific choices were made (concise, chat-optimized)
- `CLAUDE.md` — full command reference
- `metis/DESIGN.md` — UI/UX design rationale for init
