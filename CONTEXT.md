# scorerole — Project Context (lean chat starter)

Feed this file to new Claude sessions instead of the full repo.
Then ask for specific files on demand.

---

## What it is
Personal CLI tool: pull LinkedIn job alerts → score against candidate profile → email ranked digest.
Also scrapes company career pages proactively (Greenhouse/Lever APIs + Playwright fallback).

## Current state (as of 2026-06-27)
- **Done:** Full pipeline (fetch → dedup → extract → score → digest email → tracker write), `scorerole init`, `scorerole init2` (beta conversational onboarding), proactive sources (53 companies, tier-free — Greenhouse/Lever/Ashby/Playwright), scheduling (launchd/cron with retry), `scorerole track`, `scorerole feedback`, `scorerole sources`, scoring traceability (`trace.py` → `~/.job_pipeline/runs.jsonl`), format regression tests (`tests/test_render_format.py`), LinkedIn 3-case positional shift detection, tracker input validation (`_is_plausible_job_row`), broadened title filter (level-prefix stripping for base-role recall), within-run dedup fix, **console centralization** (all modules import `console` from `theme.py`; `schedule_cmd.py` is the intentional exception — it uses questionary directly), **ARCHITECTURE.md restructured** with "How It Works" section + Mermaid system overview diagram + corrected artifact map table
- **In progress:** `scorerole init2` UX polish (beta); email parsing reliability (regex primary, LLM fallback planned)
- **Near-term backlog:** `scorerole summary` (score distribution + apply-rate trends from `runs.jsonl`), config-as-parameters refactor (MCP prerequisite), one-command install, cross-platform scheduling (Windows Task Scheduler)
- **Later:** MCP server wrapper, PyPI publish, employer-lens scoring, evaluation harness

## OSS readiness — open questions (to investigate, not yet resolved)

1. **Email provider: Gmail-only today** — IMAP (`imap.gmail.com`) and SMTP config is Gmail-specific. Outlook/Exchange uses different hosts, OAuth2 instead of app passwords, different folder conventions. Open question: config flag or separate `sources/outlook.py`?

2. **Spreadsheet: xlsx via openpyxl, no Excel required** — `applications.xlsx` is standard xlsx written by `openpyxl`. Opens in Excel, Apple Numbers, LibreOffice, Google Sheets without conversion. The `openpyxl` dep is already in `requirements.txt`. No OSS blocker here.

3. **Shell compatibility** — the CLI itself is pure Python and runs in any shell. `setup_cron.sh` is bash-only. Venv activation docs assume bash/zsh syntax (`source venv/bin/activate`). Windows users need different syntax; PowerShell path is undocumented. `schedule_cmd.py` uses Python subprocess at runtime — no bash dependency there.

4. **Optional dependencies at install time** — several are assume-present: `ts-node`/Node (React Email renderer — has Python fallback, not blocking), Playwright Chromium (for playwright_companies — has Greenhouse/Lever/Ashby fallbacks), `pdfplumber`/`python-docx` (resume parsing, only needed for `scorerole init`). Need to audit whether `requirements.txt` separates optional from required.

## Mental model: config boundary

| File | Owns |
|---|---|
| `~/.job_pipeline/profile.yaml` | Candidate identity + search preferences |
| `~/.job_pipeline/sources.yaml` *(planned)* | Job sources config (proactive company overrides) |
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
scorerole/
  pipeline.py       — CLI entry point, all subcommand routing
  init_cmd.py       — scorerole init (structured 4-step wizard, InquirerPy + Rich)
  init2_cmd.py      — scorerole init2 (conversational beta: freeform → Claude extract → clarify → review)
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
  feedback_cmd.py   — scorerole feedback: collect notes → append to feedback.md → injected into scoring
  sources_cmd.py    — scorerole sources: list/add/remove/on/off company career-page sources
  sources/
    __init__.py     — fetch_alerts() router (LinkedIn + proactive)
    linkedin.py     — IMAP fetch, email parse, JD enrichment
    proactive.py    — Greenhouse/Lever API scraping + Playwright fallback
    companies.yml   — 53-company curated list (Greenhouse/Lever/Ashby/Playwright, no tiers)
```

## Data files (runtime, live outside repo at ~/.job_pipeline/)
```
profile.yaml          — candidate profile (written by scorerole init / init2)
seen_roles.json       — role_hash → timestamp, 30-day TTL dedup store
runs.jsonl            — append-only scoring trace (one JSON line per job per run)
applications.xlsx     — job tracker (written by scorerole track)
feedback.md           — calibration notes injected into every scoring prompt
schedule.json         — active schedule config (written by scorerole schedule set)
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
- **`render.py` email format is locked.** Legend: "Strength match / Caution / domain gap / Hard blocker". Stat tile: "Evaluated". Score breakdown must not appear in cards. Skipped section is a flat 2-col table. Enforced by `tests/test_render_format.py` — run after any render.py edit.
- **`_role_hash()` in `state.py` — treat as stable.** MD5, `[:12]` slice. Now uses `_normalize_company()` to strip trailing suffixes (" AI", " Inc", " LLC", etc.) before hashing — "NVIDIA AI" and "NVIDIA" hash identically. Changing the function further invalidates `seen_roles.json` and causes a flood re-send; document any changes in CLAUDE.md §6.
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
- `scorerole/DESIGN.md` — UI/UX design rationale for init / init2
