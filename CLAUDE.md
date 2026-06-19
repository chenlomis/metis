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
scorerole feedback                 # show last run summary → prompt for notes → append to feedback.md

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
  feedback_cmd.py  — feedback collection
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

## Critical constraints

### 1. Rich + InquirerPy output ordering
- Use `console.print()` for labels/hints BEFORE each prompt. The `_ask*` helpers do this — don't bypass them.
- Do NOT call `console.print()` from within an active InquirerPy prompt callback — it will corrupt the terminal cursor.
- `Q_STYLE = QUESTIONARY_STYLE` is retained in `run_init()` only to pass to `schedule_cmd.py` via `_run_proactive_sources_wizard()`. It is not used for any prompts in `init_cmd.py` itself.

### 2. Theme is the only place for colors
- All colors, styles, and questionary/InquirerPy style objects live in `scorerole/theme.py`.
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
- `score.py`, `extract.py`, `pipeline.py`, `render.py` are build-owned
