# scorerole — Claude Code context

## What this project is
A personal job alert pipeline. It pulls job listings, scores them against a user profile using Claude, and sends a personalized email digest. CLI entry point: `scorerole` → `scorerole/pipeline.py`.

## Key commands
```
# Main runner (no subcommand)
scorerole                          # pull → score → send digest; incremental (since last run, fallback 3d)
scorerole --lookback 7d            # override window; accepts: 3d, 14d, 2026-06-01
scorerole --no-limit               # ignore MAX_JOBS_PER_RUN cap; Haiku pre-screens to control cost
scorerole --dry-run                # full run (fetch + score), zero writes — no email, no seen_roles, no tracker

# init — build/update scoring profile
scorerole init
scorerole init --resume PATH       # PDF, DOCX, or TXT (prompted interactively if omitted)
scorerole init --linkedin PATH     # LinkedIn export PDF or data archive (optional enrichment)

# reset — clear seen-role state
scorerole reset                    # prompts for confirmation
scorerole reset --force            # skip confirmation
scorerole reset --profile          # also delete profile.yaml
scorerole reset --profile --force

# schedule — cron delivery
scorerole schedule                 # show current schedule + OS cron/launchd status
scorerole schedule set             # install or replace schedule (interactive wizard)
scorerole schedule remove          # remove job + delete schedule.json

# track — parse confirmation/rejection emails → update Applications xlsx
scorerole track                    # parse last 7 days; opens spreadsheet if rows changed
scorerole track --lookback 30d     # accepts same DURATION format as main runner
scorerole track --dry-run          # parse + classify, no xlsx write, no open; prints matches to stdout

# feedback — calibration notes that shape future scoring
scorerole feedback                 # collect → Claude parse → conflict detect → save to feedback.md
scorerole feedback list            # show last 5 entries (full history: ~/.job_pipeline/feedback.md)

# debug — dump most recent LinkedIn alert email
scorerole debug                    # → ~/.job_pipeline/debug_email.txt

# theme override (any command)
SCOREROLE_THEME=light scorerole [...]
SCOREROLE_THEME=dark  scorerole [...]
```

## File map
```
scorerole/
  pipeline.py      — CLI entry point, routes subcommands
  init_cmd.py      — interactive profile setup wizard (InquirerPy + Rich)
  theme.py         — ALL colors, styles, and print helpers (single source of truth)
  profile.py       — load/save ~/.job_pipeline/profile.yaml
  extract.py       — Claude extraction of structured profile from resume text
  score.py         — scoring logic against profile
  render.py        — builds DigestPayload, renders HTML via React Email or Python fallback
  schedule_cmd.py  — cron scheduling wizard
  state.py         — run state / seen-jobs tracking
  track.py         — job tracking
  tracker.py       — tracker helpers
  feedback_cmd.py  — feedback collection: collect → parse (Haiku) → save to feedback.md + feedback_log.jsonl
  sources/         — job source scrapers (proactive company career pages)

emails/
  JobAlertDigest.tsx        — root email template
  components/
    DigestHeader.tsx         — header with stat tiles (ScoreRole wordmark, greeting, stats)
    CardFooter.tsx           — "View posting →" button
    TierSection.tsx          — apply/consider tier sections
    SkippedGrid.tsx          — skipped jobs grid

utils/
  colors.ts         — email color tokens (stat tile colors, brand colors)
types.ts            — DigestPayload, Job TypeScript interfaces
```

## Profile location
`~/.job_pipeline/profile.yaml` — owner-only (chmod 600). Contains salary floor, deal-breakers, strengths. Never commit.

`profile.yaml` is the **active profile** — the only file scorerole reads at runtime. `lomis-profile.md` in the same directory is a legacy free-text version predating the YAML wizard; the code falls back to it only if `profile.yaml` is missing. It is not actively maintained.

**`SCOREROLE_PROFILE` env var** overrides the profile path without touching `profile.yaml`. Used by `run_persona_test.py` for persona testing. Never set this in `.env`. Safe to unset anytime: `unset SCOREROLE_PROFILE`.

## Persona data directories

Each persona gets its own fully isolated data directory. Your real pipeline is never touched.

| Persona | Data dir | Profile |
|---|---|---|
| Lomis (PM) | `~/.job_pipeline/` | `~/.job_pipeline/profile.yaml` |
| Designer | `~/.job_pipeline_designer/` | `~/.job_pipeline_designer/profile.yaml` |
| MLE | `~/.job_pipeline_mle/` | `~/.job_pipeline_mle/profile.yaml` |

**Two env vars control everything:**
- `SCOREROLE_PROFILE` — which profile.yaml to use (which persona's preferences/identity)
- `SCOREROLE_DATA_DIR` — where seen_roles, last_run, feedback, runs.jsonl live (state isolation)

Neither is set → real PM pipeline runs as normal.

### Running a persona

```bash
# Designer
SCOREROLE_PROFILE=~/.job_pipeline_designer/profile.yaml \
SCOREROLE_DATA_DIR=~/.job_pipeline_designer \
scorerole --lookback 7d --dry-run

# MLE
SCOREROLE_PROFILE=~/.job_pipeline_mle/profile.yaml \
SCOREROLE_DATA_DIR=~/.job_pipeline_mle \
scorerole --lookback 7d --dry-run
```

### Setting up / refreshing a persona profile

```bash
# Reconfigure designer profile interactively (init2 wizard)
SCOREROLE_PROFILE=~/.job_pipeline_designer/profile.yaml \
SCOREROLE_DATA_DIR=~/.job_pipeline_designer \
scorerole init2

# Same for MLE
SCOREROLE_PROFILE=~/.job_pipeline_mle/profile.yaml \
SCOREROLE_DATA_DIR=~/.job_pipeline_mle \
scorerole init2
```

### Resetting a persona's seen-role state

```bash
# Clear designer dedup (seen_roles.json) — does NOT touch your PM pipeline
SCOREROLE_DATA_DIR=~/.job_pipeline_designer scorerole reset --force

# Clear MLE
SCOREROLE_DATA_DIR=~/.job_pipeline_mle scorerole reset --force
```

### Automated persona test runner

`run_persona_test.py` (repo root) runs the full pipeline for each persona **without modifying `~/.job_pipeline/profile.yaml`**. Sets both env vars per persona — safe to Ctrl-C at any time.

```bash
python run_persona_test.py              # 7-day lookback (default)
python run_persona_test.py --lookback 3
```

## Test strategy

**Tests must run inside the project venv** — `rich`, `InquirerPy`, `anthropic`, etc. are only installed there. Running bare `pytest` outside the venv will exit immediately with a clear error message (see `conftest.py`).

```bash
# Preferred — always correct regardless of active python:
make test            # full suite
make test-fast       # fast pass only

# Manual — only if venv is already active:
source venv/bin/activate
pytest tests/ -q                                        # full pass (~397 tests, ~3s)
pytest tests/test_core.py tests/test_schedule.py -q    # fast pass (~60 tests, <3s)
```

`test_extract.py` is the heavyweight suite (~70+ tests, mocked API). Only run when `extract.py` changes — skip during routine iteration.

## Critical constraints

### -1. Proactive source scrapes always use render.py format and role+location filtering

**Any time a proactive career-page scrape runs** (scheduled, one-off, or test), two invariants must hold:

1. **Filtering:** Use `_build_title_patterns(profile.target.roles)` for title matching and `_detect_country(profile.candidate.location)` for location matching. Only roles that pass both filters are passed to scoring. Do NOT bypass these even in one-off scripts — they prevent irrelevant roles from wasting API calls and polluting the digest.

2. **Output format:** The output email MUST be rendered via `render_html()` from `render.py`. Never use a custom rendering path (paragraph summaries, "X/100" scores, 3+2 bullet format, or any ad-hoc HTML). `render_html()` calls `build_digest_html()` internally and produces the canonical format enforced by `tests/test_render_format.py`. The only acceptable caller is `render_html(scored_jobs, run_date, deal_breaker_count=n)`.

These rules apply to one-off scripts (`proactive_sample.py`, etc.) as well as the main pipeline. A one-off that sends a differently formatted email is non-conformant.

### 0. render.py email format is locked — do not change without explicit request
`build_digest_html()` and `_job_card()` in `render.py` produce the canonical digest format.
The following are **intentional and must not be changed** unless the user explicitly asks to change the format:

**Label text:**
- Stat tile first label: `"Evaluated"` (not "Roles evaluated", not "Total evaluated")
- Legend labels: `"Strengths"`, `"Caution"`, `"Blocker"` — exactly these words, no suffix
- Score breakdown: `render_score_breakdown()` must NOT be called from `_job_card()` — the breakdown must not appear in email cards
- Skipped section: flat 2-column table; role titles are hyperlinked

**Stat tile colors (updated June 27 — Solid match tile changed to green):**
- Evaluated tile border: `#5F5E5A` (dark gray)
- Solid match tile border: `#3B6D11` (green — matches Solid Match section header label color)
- Moderate match tile border: `#854F0B` (amber)
- Green (`#3B6D11`) is also used for section header bars and lever-point text

**Legend dots — MUST use `<span>` wrapper, NOT bare `<td>` background:**
- Gmail strips `border-radius` from `<td>` elements — dots render as squares without the span wrapper
- Correct: `<td><span style="display:inline-block;width:8px;height:8px;background:X;border-radius:4px;font-size:0;line-height:0"></span></td>`
- Wrong: `<td width="8" height="8" style="background:X;border-radius:4px">` — this causes square dots in Gmail

**Section headers:** `font-size:14px` (not 13px) for SOLID MATCH / MODERATE MATCH headings

**Skip rows (Limited Match section):**
- Score pill: always gray (`#f5f5f3` background, `_C_MUTED` text) — never color-coded by score value
- Role title link: `font-weight:400` (not 500 — not bold)

**"View posting →" link in apply/consider job cards:**
- Plain muted text link: `color:{_C_MUTED}`, no background, no border, no padding, no border-radius
- Wrong: styled button with `background:{pill_bg};padding:5px 12px;border-radius:4px`

**Greeting / header:**
- Greeting is rendered as `<h1>` (22px, font-weight:600, color:#1f2118) + `<p>` subtitle (14px, muted)
- There is NO separate `<h1>Personalized job alert digest</h1>` in the template body — that element was removed
- `render_html()` must pass `greeting_sub` to `build_digest_html()`

These constraints are enforced by `tests/test_render_format.py`. Run that test after any render.py change — a failure means the format regressed. Fix the regression, not the test.

**When fixing bugs in render.py:** touch only the broken behavior. Do not "clean up", "improve", or change any string content, labels, or layout while in the file.

### 1. Rich + InquirerPy output ordering
- Use `console.print()` for labels/hints BEFORE each prompt. The `_ask*` helpers do this — don't bypass them.
- Do NOT call `console.print()` from within an active InquirerPy prompt callback — it will corrupt the terminal cursor.
- `Q_STYLE = QUESTIONARY_STYLE` is retained in `run_init()` only to pass to `schedule_cmd.py` via `_run_proactive_sources_wizard()`. It is not used for any prompts in `init_cmd.py` itself.

### 2. Theme is the only place for colors and the shared console
- All colors, styles, and questionary/InquirerPy style objects live in `scorerole/theme.py`.
- `theme.py` exports `console = _BoundedConsole()` — a module-level singleton that clamps terminal width to [80, 100] columns. All modules import it as `from .theme import console`. Do NOT instantiate a local `Console()` anywhere else.
- **Exception:** `schedule_cmd.py` uses `questionary` prompts directly and does not use `console`. This is intentional — leave as-is.
- `init_cmd.py` imports `QUESTIONARY_STYLE`, `INQUIRER_STYLE`, `console`, and print helpers from theme. No hardcoded hex values anywhere else.
- Light/dark detection: `SCOREROLE_THEME=light|dark` env var → `COLORFGBG` fallback → default dark.

### 3. Email is React Email (TSX), not plain HTML
- Rendered via `ts-node` in `render.py:render_html()`. Falls back to `build_digest_html()` Python if ts-node fails.
- Layout is table-based (no CSS grid/flex) for email client compatibility.
- All email color tokens are in `utils/colors.ts`, not in Python.

### 4. Profile YAML schema
The profile has these top-level sections (order matters for display):
`candidate`, `target`, `aspirations`, `preferences`, `scoring`, `experience`, `education`,
`strengths`, `green_flags`, `yellow_flags`, `red_flags`, `deal_breakers`, `salary_floor_usd`, `notes`, `inferred`, `proactive_sources`

### 5. score.py ↔ render.py eval schema is a coupled contract
The eval dict shape that `score.py` emits is consumed directly by `render.py`. These two files are **locked in lockstep** — changing one requires changing the other in the same edit:

- `verdict`: exactly `"apply" | "consider" | "skipped" | "filtered"` — no other strings
- `dimensions`: exactly 6, in this order: `seniority_scope`, `experience_relevance`, `compensation_fit`, `culture_values`, `domain_background`, `company_stage`
- `leveragePoints`: always exactly 2 items (array of strings)
- `frictionPoints`: always exactly 1 item (array of strings) — shown as the skip reason in the skipped section
- `tags`: array of `{text, sentiment}` where sentiment is `"green" | "amber" | "red"`

**Bullet style rule (enforced in score.py prompt, must stay):** No em-dash constructions. One clause only, 15–20 words, no pronouns, no "strong", "proven", "deep", "robust", "at scale", "directly". Violations in output are a prompt regression — fix the prompt, not the downstream rendering.

OSS users who want to customize the schema must change score.py prompt + render.py together. Neither file is standalone.

### 6. state.py `_role_hash()` — treat as stable; document intentional changes here
`_role_hash(title, company)` in `state.py` produces the dedup keys stored in `~/.job_pipeline/seen_roles.json`. Changing the hash function invalidates historical keys — previously seen roles re-process on next run (flood risk). Only change with explicit user instruction and note the change here.

**Current implementation (updated June 27):** `md5(normalize(title + _normalize_company(company)))[:12]`

`_normalize_company()` strips trailing legal/branding suffixes before hashing: ` AI`, ` Inc`, ` Corp`, ` Ltd`, ` LLC`, ` Group`, ` Holdings`, ` Corporation`, ` Technologies`, ` Technology`, ` Co.` — so "NVIDIA AI" and "NVIDIA" produce the same hash.

**One-time impact:** Existing seen_roles entries for companies whose names match a stripped suffix (e.g., "Scale AI") use the old un-normalized hash. Those entries become orphaned on the next run — affected roles may re-surface once, get re-scored and re-stored under the new normalized hash, then correctly deduplicated going forward. Bounded by the 30-day TTL.

### 7. tracker.py column order is a persisted xlsx schema — do not reorder or insert
`_HEADERS` and `_COL_*` constants in `tracker.py` define the column layout of `applications.xlsx`. The file may contain months of history. **Column order must never change** without an explicit migration plan — inserting or reordering columns corrupts existing rows.

Safe to change: column header *text* in `_HEADERS` (row 1 display names), tracker file path (via `TRACKER_PATH` env var).
Not safe without migration: adding a column in the middle, removing a column, reordering.

### 8. pipeline.py stage order is load-bearing — do not reorganize without explicit instruction
`pipeline.py` is the orchestration layer. The stage sequence is:
1. Dedup check (`load_seen_roles`) → before scoring so unseen roles don't waste API calls
2. Score (`_stage_score`) → Haiku pre-screen, then Sonnet full score
3. Deal-breaker split (`_stage_split_filtered`) → **after** `new_role_timestamps` is built, **before** `render_html`
4. Skipped metadata saved → before SMTP so it survives delivery failure
5. Render → `scored_jobs` only (filtered excluded), `deal_breaker_count` passed as footer note
6. Send → SMTP
7. Tracker write → after send

Do not reorder, merge, or add stages without being asked. When fixing a bug in pipeline.py, touch only the broken call — do not restructure the surrounding flow.

## init_cmd.py prompt helpers

All interactive prompts in `init_cmd.py` use five helpers — do not use `questionary` or raw `input()` in that file:

| Helper | InquirerPy call | Usage |
|---|---|---|
| `_ask(label, hint, default)` | `inquirer.text` | Single-line text input |
| `_ask_select(label, choices, hint, default)` | `inquirer.select` | Single-choice list |
| `_ask_checkbox(label, choices, hint)` | `inquirer.checkbox` | Multi-select |
| `_ask_confirm(label, default)` | `inquirer.confirm` | Yes/No |
| `_ask_filepath(label, hint)` | `inquirer.filepath` | Path with tab-complete |

**Spacing rule (enforced by helpers):** 0 blank lines within a question block (label→hint→input are one unit). 1 blank line between blocks (`\n` prefix on each label print). Pointer is always `"  › "` (2 spaces + › + 1 space) for horizontal alignment.

**InquirerPy style:** `INQUIRER_STYLE` in `theme.py` is created via `InquirerPy.utils.get_style({...})` — it's an `InquirerPyStyle` object, NOT a plain dict. Passing a plain dict causes `AttributeError: 'dict' object has no attribute 'dict'`.

`schedule_cmd.py` still uses `questionary` (not migrated — fine to leave as-is).

## Dependencies
- `anthropic` — Claude API (profile extraction + job scoring)
- `rich>=13.0` — terminal formatting
- `questionary>=2.0` — used by `schedule_cmd.py` only
- `InquirerPy>=0.3.4` — prompt library for `init_cmd.py` `_ask*` helpers; in `requirements.txt`
- `requests>=2.32.0` — LinkedIn URL scraping in init
- `pdfplumber`, `python-docx` — resume parsing
- `pyyaml` — profile serialization

## Multiple sessions warning
If running separate Claude Code sessions on design vs. build:
- `theme.py` is design-owned — build session should not edit it
- `init_cmd.py` is a shared boundary — coordinate before editing
- `emails/` and `utils/colors.ts` are design-owned
- `extract.py` is build-owned
- `score.py` is build-owned — eval schema is a locked contract shared with render.py (see constraint #5)
- `render.py` is build-owned for bug fixes and new data wiring only — output format is locked (see constraint #0)
- `pipeline.py` stage order is locked — bug fixes only, no restructuring (see constraint #8)
- `state.py` `_role_hash()` is frozen — do not touch (see constraint #6)
- `tracker.py` column order is frozen — header text and file path are safe to change, order is not (see constraint #7)
