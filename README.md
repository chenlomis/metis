<!-- logo or demo gif goes here -->

# metis

**Spend your time and energy on roles that matter.**

metis is an AI-powered career agent that automates the first round of job discovery: it screens and ranks new opportunities against your profile, experience, career goals, and deal-breakers. It consolidates roles from LinkedIn job alerts and company career pages, semantically compares them to your background, and delivers a personalized scored digest on a schedule you control. Beyond the digest, it automatically tracks applications and recruiter responses in a spreadsheet and generates summaries to help you understand how your search is progressing over time.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Powered by Claude](https://img.shields.io/badge/powered%20by-Claude%20Sonnet-blueviolet.svg)](https://www.anthropic.com)

---

**Jump to:**
[What it does](#what-it-does) •
[How it works](#how-it-works) •
[Prerequisites](#prerequisites) •
[Quick start](#quick-start) •
[Commands](#commands) •
[Architecture](#architecture) •
[Roadmap](#roadmap) •
[Contributing](#contributing)

---

## What it does

**Profile setup (`metis init`).** An interactive wizard that reads your resume and LinkedIn profile and asks what you are targeting, what you want to exclude, and any other criteria. Claude uses your answers to build `profile.yaml`, which every future scoring run is evaluated against. You can re-run it or edit the file directly at any time. As part of setup you can also add company career-page sources (`metis companies add`) and configure automated scheduling (`metis schedule`).

**Scored digest (`metis`).** Each run ingests new roles from all configured sources, deduplicates across runs, scores each role through a multi-stage Claude pipeline, and emails you a ranked HTML digest. Every JD gets a categorical verdict and a 0-100 score:

- Solid Match (75+): roles worth prioritizing
- Moderate Match (55-74): roles worth a closer look
- Limited Match (below 55): roles that do not clear the bar

Each evaluation surfaces two strengths and one potential friction point, plus normalized tags for a quick scan. Roles in the Solid Match and Moderate Match tiers are automatically written to `applications.xlsx` to start your tracking.

**Application tracking (`metis track`).** After you apply, metis scans your inbox for confirmation, rejection, and recruiter emails and updates `applications.xlsx` automatically.

**Progress reporting (`metis summary`).** High-level insights across score distribution, verdict trends, and application rates. Useful both for tracking how your search has evolved and for spotting patterns that help calibrate your profile over time.

**Feedback tuning (`metis feedback`).** After reviewing a digest or summary, you can write plain-English feedback ("seed-stage roles keep scoring high but I always skip them"). The system parses it, asks you to confirm the signal, and injects it into all future scoring runs as a high-priority calibration note. Conflicting signals are handled gracefully.

---

## How it works

```
LinkedIn job alert emails          Company career pages
  (3 sender types, 2 formats)        (Greenhouse / Lever / Ashby)
         |                                     |
         +-------------+  +------------------+
                       |  |
                  Gmail (IMAP)
                       |
              sources/linkedin.py
              sources/companies.py
                       |
               3-layer dedup
          (job_id / title+company / 30-day hash)
                       |
           cap check + cost estimate
                       |
         Haiku pre-screen (when over cap)
                       |
         Sonnet full scoring (27 fields)
                       |
          Ranked HTML digest (React Email / Python fallback)
                       |
                 Gmail (SMTP)
```

The pipeline has three stages: **ingest**, **score**, and **render**. Every job at every stage writes to `runs.jsonl` for full traceability. See [ARCHITECTURE.md](./ARCHITECTURE.md) for data flow diagrams and notes on extending each layer.

### Setup: `metis init`

`metis init` runs a conversational wizard. You paste your resume (PDF, DOCX, or TXT), answer a few questions about what you are looking for, and Claude builds `~/.job_pipeline/profile.yaml` from your answers. You can edit this file directly at any time:

```yaml
candidate:
  name: "Your Name"
  location: "San Francisco, CA"
  open_to_remote: true

target:
  roles: ["Staff PM", "Director of Product"]
  level: staff

strengths:
  - "0-to-1 product builds"
  - "ML/data background"

deal_breakers:
  - "no equity"
  - "on-site 5 days/week"
```

See [profile.template.yaml](./profile.template.yaml) for the full schema with comments.

Pass `--resume path/to/file` to skip the file prompt. Pass `--supplement path/to/linkedin-export.pdf` to augment with your LinkedIn profile export (see [Ingesting your LinkedIn profile](#ingesting-your-linkedin-profile)).

### Email digest: `metis`

Runs the full pipeline: ingest from all sources, deduplicate, score, render, send. Each role in the digest gets:

- **Score (0-100)** weighted by your priorities
- **Verdict**: Solid Match, Moderate Match, or Limited Match
- **Lever and friction points** explaining the score
- **Tags**: remote, equity, stage, team size, etc.

Roles are deduplicated across runs. You will not see the same listing twice within 30 days.

**Two-model pipeline.** When you are over the per-run cap, Haiku handles fast title/company pre-screening; Sonnet does full structured scoring across 27 fields. This keeps costs low without sacrificing quality on roles that make it through.

Here is what a Solid Match card looks like in the digest:

```
SOLID MATCH   91 / 100
Staff PM, Data Platform — Stripe
Remote (US)  •  Series B+  •  $175-225k + equity

Strengths
  Your 0-to-1 ML infrastructure work maps directly to their data platform roadmap
  Three prior data-adjacent PM roles show domain depth the JD explicitly asks for

Watch out for
  JD mentions 5-day onsite preference for the first 90 days, which may conflict
  with your location flexibility requirement

Tags: remote-flexible  fintech  infra-pm  equity-confirmed  201-1000

[View role]  [Already applied]
```

### Company sourcing: `metis companies`

metis can pull roles directly from company ATSs without needing a LinkedIn alert. You manage a list of target companies:

```bash
metis companies              # show active sources
metis companies add Stripe   # add by name; metis probes Greenhouse, Lever, and Ashby automatically
metis companies remove       # interactive removal
metis companies off          # disable without losing your list
```

### Application tracking: `metis track`

`metis track` reads your inbox for confirmation and rejection emails and writes the status to `applications.xlsx`. Run it after a batch of applications to keep your tracker current without touching a spreadsheet.

```bash
metis track                  # default 7-day lookback
metis track --lookback 30d   # wider window
metis track --dry-run        # print matches without writing
```

### Reporting: `metis summary`

Score distribution, verdict breakdown, and run history pulled from `~/.job_pipeline/runs.jsonl`.

```bash
metis summary
metis summary --lookback 90d
```

### Feedback loop: `metis feedback`

Write plain-English calibration notes after reviewing a digest. Claude parses each note into structured metadata, you confirm before anything saves, and all future scoring runs inject your feedback as a high-priority section in the Sonnet prompt.

```bash
metis feedback               # add a calibration note
metis feedback list          # show recent entries
```

Notes accumulate permanently with no TTL. If a note is no longer accurate, add a correction; contradictions are flagged before saving.

---

## Prerequisites

| What | Why | How to get it |
|------|-----|---------------|
| Python 3.11+ | Runtime | [python.org/downloads](https://www.python.org/downloads/). macOS ships with 3.9, which is too old. Install via Homebrew: `brew install python@3.11` |
| Node.js 18+ | Email rendering | [nodejs.org](https://nodejs.org). Required for the React Email digest renderer. Install via Homebrew: `brew install node` |
| Anthropic API key | Powers role scoring | [console.anthropic.com](https://console.anthropic.com). Separate from your Claude.ai subscription. Scoring a 10-job batch costs roughly $0.05-0.15. |
| Gmail with IMAP | Source of job alert emails | Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP |
| Gmail App Password | Lets metis read your inbox without your main password | Requires 2FA. [Generate one here](https://myaccount.google.com/apppasswords), choose Mail + your device. Never use your main account password. |
| LinkedIn job alerts | Source of job listings | Set up daily email alerts for your target roles on LinkedIn (see below) |
| Your resume (PDF, DOCX, or TXT) | Used by `metis init` to build your scoring profile | Your existing file |

**Platform support:** macOS and Linux. Windows via WSL2 should work but is untested.

### Setting up LinkedIn alerts

metis reads emails that LinkedIn sends you. It does not scrape LinkedIn. You need at least one alert email in your inbox before the first run.

1. Go to [LinkedIn Jobs](https://www.linkedin.com/jobs/) and search for your target role and location
2. Click **Set alert** (the bell icon near the search bar)
3. Set frequency to **Daily**
4. Repeat for each search you want to track

LinkedIn sends one email per saved search per day, listing 5-10 new roles. metis reads all of them.

metis reads emails from these three LinkedIn senders:
- `jobalerts-noreply@linkedin.com` - "Your job alert for X" digests
- `jobs-noreply@linkedin.com` - "Company is hiring" / "Jobs similar to X" recommendations
- `jobs-listings@linkedin.com` - "Jobs you might like" (JYMBII) digests

Both multi-job digests and single-role notifications are supported.

### `.env` configuration

Copy `.env.example` to `~/.job_pipeline/.env` and fill in your values. metis looks for credentials there first, then falls back to a `.env` file in the project root (for contributors running from a clone).

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Optional (defaults shown)
RECIPIENT_EMAIL=you@gmail.com       # where to send the digest (defaults to GMAIL_ADDRESS)
MAX_JOBS_PER_RUN=20                 # cap per run to control API cost; 0 = no cap
DEFAULT_LOOKBACK=3d                 # how far back to fetch on each run
MODEL=claude-sonnet-4-6             # Claude model for full scoring
PRESCREEN_MODEL=claude-haiku-4-5    # model for quick title/company pre-screening
```

---

## Quick start

```bash
# 1. Install (no clone needed)
brew install pipx && pipx ensurepath
pipx install git+https://github.com/chenlomis/metis.git

# Or clone if you want to contribute or edit locally
# git clone https://github.com/chenlomis/metis && cd metis && pipx install -e ".[dev]"

# 2. Configure credentials
mkdir -p ~/.job_pipeline
curl -fsSL https://raw.githubusercontent.com/chenlomis/metis/main/.env.example \
  -o ~/.job_pipeline/.env
# Edit ~/.job_pipeline/.env — fill in ANTHROPIC_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD

# 3. Build your scoring profile from your resume
metis init

# 4. Run
metis
```

> **Optional — Playwright-powered company sourcing:** metis can also pull roles directly from company career pages (Greenhouse, Lever, Ashby). This requires Playwright and is disabled by default. To enable:
> ```bash
> uv tool run "metis-job[browser]" -- playwright install chromium
> # Then: metis companies add Stripe
> ```

**Expected output:** metis fetches LinkedIn alert emails from the last 3 days, scores each role, and emails you a ranked digest. On first run this takes 30-90 seconds depending on how many roles it finds.

> **No emails found?** LinkedIn alert emails may take up to 24 hours to arrive after setup. Run `metis --lookback 14d` to cast a wider net, or see [Troubleshooting](#troubleshooting).

---

## Commands

### Digest

| Command                         | What it does                                                                 |
|---------------------------------|------------------------------------------------------------------------------|
| `metis`                         | Fetch from all sources, score, send digest (default: last 3 days)           |
| `metis --lookback 7d`           | Same, wider window. Accepts `7d`, `14d`, `yesterday`, or `2025-01-15`       |
| `metis --no-limit`              | Score everything in the window, bypassing the per-run cap (prompts for confirmation) |
| `metis --no-limit --lookback 14d` | Catch-up run after a gap (prompts for confirmation)                       |

### Profile setup

| Command                                  | What it does                                                        |
|------------------------------------------|---------------------------------------------------------------------|
| `metis init`                             | Conversational wizard: paste resume, answer questions, build profile |
| `metis init --resume path/to/resume.pdf` | Skip the file path prompt                                           |
| `metis init --supplement file.pdf`       | Augment with LinkedIn profile export                                |

### Company sourcing

| Command                      | What it does                                               |
|------------------------------|------------------------------------------------------------|
| `metis companies`            | Show active sources                                        |
| `metis companies add NAME`   | Add a company; auto-detects Greenhouse / Lever / Ashby ATS |
| `metis companies remove`     | Interactive removal                                        |
| `metis companies on`         | Enable curated sources                                     |
| `metis companies off`        | Disable without losing your list                           |

### Tracking

| Command                          | What it does                                            |
|----------------------------------|---------------------------------------------------------|
| `metis track`                    | Parse inbox for confirmations and rejections, update `applications.xlsx` |
| `metis track --lookback 30d`     | Wider lookback window                                   |
| `metis track --dry-run`          | Print matches without writing                           |

### Reporting

| Command                            | What it does                                                          |
|------------------------------------|-----------------------------------------------------------------------|
| `metis summary`                    | Generate and email the progress report (DRAFT PREVIEW prefix)         |
| `metis summary --lookback 60d`     | Scope all market intel to 60-day window (default: 30d)                |
| `metis summary --output report.html` | Save as HTML instead of sending                                     |
| `metis summary --output report.pdf`  | Save as PDF instead of sending                                      |
| `metis summary --send`             | Send as real email (removes [DRAFT PREVIEW] prefix)                   |

### Feedback

| Command                | What it does                                            |
|------------------------|---------------------------------------------------------|
| `metis feedback`       | Add a calibration note that shapes future scoring       |
| `metis feedback list`  | Show recent calibration entries                         |

### Scheduling

| Command                 | What it does                                               |
|-------------------------|------------------------------------------------------------|
| `metis schedule`        | Show current schedule and OS job status                    |
| `metis schedule set`    | Interactive wizard: choose frequency and time              |
| `metis schedule remove` | Remove the scheduled job                                   |

### State and debugging

| Command                   | What it does                                                            |
|---------------------------|-------------------------------------------------------------------------|
| `metis reset`             | Clear dedup state so all roles reprocess next run (profile kept)        |
| `metis reset --force`     | Same, no confirmation                                                   |
| `metis reset --profile`   | Also delete your scoring profile (requires `metis init` before next run) |
| `metis debug`             | Dump the most recent LinkedIn alert email to `~/.job_pipeline/debug_email.txt` |

---

## Architecture

```
metis/
  pipeline.py      # CLI entry point and orchestration
  score.py         # Claude scoring logic (Layer 2 - Sonnet, 27 fields)
  extract.py       # Structured extraction (Layer 1 - Haiku)
  profile.py       # Profile loader (YAML to scoring prompt)
  prompts.py       # Canonical prompt templates
  init_cmd.py      # metis init - conversational profile setup wizard
  render.py        # HTML digest renderer (React Email / Python fallback)
  report_cmd.py    # metis summary - score distribution and run history
  feedback.py      # Feedback log: JSONL store and calibration parser
  sources_cmd.py   # metis companies - manage proactive company list
  track.py         # metis track - confirmation and rejection email parsing
  xlsx.py          # applications.xlsx read/write
  trace.py         # runs.jsonl telemetry (every job at every pipeline stage)
  schedule_cmd.py  # metis schedule - launchd/cron wizard
  state.py         # Dedup state (seen_roles.json, 30-day TTL)
  theme.py         # Rich and InquirerPy theme (single source of truth)
  sources/         # Email ingestion (IMAP, LinkedIn parser, proactive ATS)

emails/            # React Email digest templates (TypeScript)
tests/             # pytest suite
profile.template.yaml
.env.example
Makefile           # make test, make lint
```

See [ARCHITECTURE.md](./ARCHITECTURE.md) for data flow diagrams and notes on extending each layer.

### Data stores

Three separate stores power different parts of the system. Do not conflate them — they have different write paths and power different report sections:

| Store | Written by | Contents | Powers |
|---|---|---|---|
| `~/.job_pipeline/applications.xlsx` | Digest delivery (apply+consider only) + `metis track` | Apply and Consider rows; application status updates | §2 ROI banner, §3 Solid/Moderate tiles, §4 Pipeline, Alignment |
| `~/.job_pipeline/runs.jsonl` | Scorer — every verdict | All verdicts with full extraction + eval JSON | §5 Core Strengths, §6 Market Landscape, §7 Level Distribution, §8 Comp Snapshot |
| `~/.job_pipeline/skipped_roles.json` | Scoring pipeline, pre-delivery | Skipped role metadata; written before SMTP so it survives send failures | §3 Partial Match tile |

**Critical:** `Partial Match` must always be read from `skipped_roles.json`, never from xlsx. Skipped roles are never written to xlsx — reading xlsx for Partial always returns 0.

---

## Privacy

metis runs entirely on your machine. Here is exactly what leaves it:

| What                                                              | Sent where       | When                    |
|-------------------------------------------------------------------|------------------|-------------------------|
| Resume text (up to 12,000 chars)                                  | Anthropic API    | During `metis init` only |
| Your scoring profile (career history, strengths, deal-breakers)   | Anthropic API    | Every `metis` run       |
| Job titles, company names, JD text (up to 1,500 chars per role)   | Anthropic API    | Every `metis` run       |
| IMAP login                                                        | Gmail only (SSL) | Every run               |
| SMTP login and digest HTML                                        | Gmail only (SSL) | Every run               |

Nothing goes to any other third party. Your Gmail App Password and Anthropic API key never leave your machine. See [anthropic.com/privacy](https://www.anthropic.com/privacy) for Anthropic's data-handling policies.

Local data stored in `~/.job_pipeline/` (outside the repo, never committed):

| File                  | Contents                                         | Permissions             |
|-----------------------|--------------------------------------------------|-------------------------|
| `profile.yaml`        | Your extracted profile                           | 600 (owner-readable)    |
| `seen_roles.json`     | MD5 hashes of scored roles and timestamps, 30-day TTL | 600 (owner-readable) |
| `logs/YYYY-MM-DD.log` | Pipeline run logs (may contain job titles)       | default                 |

---

## Cost

Scoring a typical 10-job batch costs roughly $0.05-0.15 with `claude-sonnet-4-6`. Running `metis` daily on a typical alert volume (20-30 roles/week) runs about $0.50-2.00/month.

When more than `MAX_JOBS_PER_RUN` new roles appear (default: 20), metis pauses and shows the count and estimated cost before proceeding. Roles beyond the cap stay unseen and reappear next run. They are never silently discarded.

Set `MAX_JOBS_PER_RUN=0` in `.env` to remove the cap.

---

## Roadmap

- [ ] MCP server wrapper so metis is queryable from Claude Code directly
- [ ] Web UI for digest review and feedback (local, no server required)
- [ ] Outlook / Microsoft 365 support
- [ ] PyPI publish (`pip install metis-job`) for one-command install without cloning

See [open issues](https://github.com/chenlomis/metis/issues) for the full list.

---

## Troubleshooting

**"No emails in lookback window. Done." on first run**

metis connected to Gmail but found no LinkedIn alert emails in the last 3 days. Try:
- `metis --lookback 14d` to widen the window
- Check that alerts deliver to INBOX. Gmail filters that archive or label emails skip the INBOX search. Temporarily remove the "Skip Inbox" action from any LinkedIn filter.
- Run `metis debug` to see the raw email body and confirm the format is parseable.

**`Gmail login failed` / IMAP auth error**

Two things must both be true: 2-Step Verification is on, and you are using a Gmail App Password, not your account password. Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Also confirm IMAP is enabled: Gmail > Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP.

**"No roles to evaluate" despite having alert emails**

Run `metis debug`. It writes the raw email body to `~/.job_pipeline/debug_email.txt`. If it looks like a promotional email rather than a job alert, metis cannot parse it. Make sure your LinkedIn alert type is "Your job alert for X" or "Job recommendations."

**`No scoring profile found. Run metis init`**

Run `metis init` to create your profile before running the digest.

**`ERROR: Invalid requirement: '#'` during `pip install -e .`**

A stale `metis.egg-info/` directory from a previous install. Delete it and reinstall:

```bash
rm -rf metis.egg-info && pip install -e .
```

**A company appeared in my LinkedIn notifications but not in my digest**

LinkedIn has two separate channels: **email job alerts** (what metis reads) and **in-app push notifications** (what you see in the LinkedIn app's notification bell). These are different systems. Push notification types that do NOT produce emails:
- "Company X is hiring. Apply today." — company page hiring announcements
- "Results from the new AI-powered job search" — LinkedIn's in-app AI recommender
- "Jobs similar to one you recently viewed" — recommendation engine

Only saved job search alerts (set to Daily frequency from a search results page) reliably produce emails. If a company you care about isn't generating email alerts, add it to proactive sources: `metis companies add <name>`.

**A specific role seems to be missing — I know it exists**

Roles that were processed (even if filtered or skipped) are recorded in `~/.job_pipeline/seen_roles.json` with a 30-day TTL. Once a role is in that file it won't reappear regardless of verdict. Common causes:
- The role was filtered by a hard gate (`jd_blank` — empty job description from the ATS API; `salary_floor` — disclosed salary below your floor). Run `metis --lookback 14d --dry-run` and check logs for `Gate filtered:` lines.
- Your deal-breaker list had a mismatch when the role was first processed.

To re-evaluate a specific role without resetting everything, remove its hash from `seen_roles.json`:

```python
import re, hashlib, json
from pathlib import Path

_CO_VARIANT = re.compile(
    r"\s+(?:AI|Labs?|Technologies|Tech|Software|Systems|Solutions|Platforms?|"
    r"Inc\.?|LLC|Corp\.?|Ltd\.?|Co\.?)$", re.IGNORECASE,
)

def _canonical_company(name):
    prev, result = None, name.strip()
    while result != prev:
        prev = result
        result = _CO_VARIANT.sub("", result).strip()
    return result

def role_hash(title, company):
    key = re.sub(r"[^a-z0-9]", "", (title + _canonical_company(company)).lower())
    return hashlib.md5(key.encode()).hexdigest()[:12]

p = Path.home() / ".job_pipeline/seen_roles.json"
data = json.loads(p.read_text())
data.pop(role_hash("Staff Product Manager", "Acme"), None)
p.write_text(json.dumps(data, indent=2))
```

The role will reappear on the next run if it's within the lookback window or in a proactive source company.

**`metis: command not found` after pipx install**

Run `pipx ensurepath` and open a new terminal to add `~/.local/bin` to your PATH.

---

## Ingesting your LinkedIn profile

The `--supplement` flag in `metis init` accepts any file that adds context about you. To export your LinkedIn profile:

1. LinkedIn > Me > Settings & Privacy > Data Privacy > **Get a copy of your data**
2. Select **Profile** only and request the archive
3. Download, unzip, and pass the file to: `metis init --supplement ~/Downloads/Profile.pdf`

---

## Contributing

Bug reports and PRs welcome. Open an issue before large changes so we can align on approach. Small fixes and documentation improvements can go straight to PR.

**Dev setup:**

```bash
git clone https://github.com/chenlomis/metis && cd metis
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm install
make test
make lint
```

**React Email digest:** The digest is rendered with React Email. On your first `metis` run, the template source is bootstrapped to `~/.job_pipeline/email_templates/` and `npm install` runs automatically (one-time, ~30 seconds). Subsequent runs use the cached `node_modules`. If Node is not available, metis falls back to a Python HTML renderer and logs a warning.

```bash
# To iterate on the React Email template locally (dev only):
npm install           # install at project root
npm run email:dev     # live preview at localhost:3000
```

**Good areas to contribute:**
- Additional email providers (Outlook / IMAP beyond Gmail)
- Job board sources beyond LinkedIn alerts
- Alternative digest render targets (Slack DM, Telegram)
- Expanded test coverage for email parsing edge cases
- Translations for non-English alert emails

**Touching `metis summary` or `report_cmd.py`:**

The canonical spec for every section's layout, data source, column widths, and color rules lives in `~/.job_pipeline/report_identity.md`. Update that file first — it is the single source of truth and the only persistent record of what each section is supposed to look like. Changes to render code without updating the spec will cause regressions.

All colors are defined as constants at the top of `report_cmd.py`. Do not hardcode hex values elsewhere:

```python
APPLY_BG  = "#eef2ee";  APPLY_NUM  = "#2d5a2d"  # green  — Solid Match
CONSID_BG = "#faeeda";  CONSID_NUM = "#854f0b"  # amber  — Moderate Match, Pending
TOTAL_BG  = "#f5f5f3";  TOTAL_NUM  = "#1f2118"  # neutral — totals, bar chart bars
RED_BG    = "#f2eeee";  RED_NUM    = "#8b2e2e"  # red    — Rejections
```

Bar chart bars use `TOTAL_NUM` (#1f2118), not green. Trend pills in §6 have three distinct styles: **Trending ↑** = green · **Established** = blue (#e8f0f8 / #185FA5) · **Niche** = gray. Do not collapse Established and Niche to the same style.

Sections §6A, §6B, and §7 all share the `_COLGROUP_4` constant (40/15/25/20%). §5 uses 28/44/28. Use `table-layout:fixed` on all data tables. Changing one table's widths without updating the others causes visual misalignment that is easy to miss in preview.

**Market intel extension point:**

`load_market_intel()` in `report_cmd.py` accepts a `normalize_fn` parameter:

```python
def load_market_intel(
    runs_path: Path | None = None,
    lookback_days: int = 30,
    normalize_fn=None,  # Callable[[list[str]], list[str]] | None
) -> dict:
```

When `None`, leveragePoints are bucketed via keyword matching. Pass a callable to add LLM normalization — it receives a list of raw leveragePoint strings and returns canonical labels. No other code changes required. The intended first use is proper industry vertical classification (FinTech, HealthTech, DevInfra, GovTech) from company names in `runs.jsonl`; the current `company_tier × customer_type` proxy in Table B is a stopgap until that is implemented.

**Digest vs. report templates:**

These are two separate rendering pipelines — do not expect them to match visually. The digest (`metis` / `metis schedule`) uses React Email via `render.ts` / `ts-node`, with a Python `build_digest_html()` fallback. The report (`metis summary`) is pure Python HTML in `report_cmd.py`. Different format, different purpose — the visual difference is intentional.

---

## License

MIT. See [LICENSE](./LICENSE).

---

## Notes for AI agents working in this repo

> For Claude Code and other AI coding agents. Human contributors can skip this.

**Read `pipeline.py` before touching anything else.** The orchestration logic there is the source of truth for how all modules connect. Do not modify inter-module interfaces without tracing all callers first.

**Prompt templates are contracts.** All Claude prompts live in `prompts.py`. Do not inline prompts in other files. When modifying a prompt, update `prompts.py` only, check that all callers still pass the required variables, and note what changed and why.

**The two-model architecture is load-bearing.** Haiku runs fast pre-screening. Sonnet runs full structured scoring. Do not collapse them into a single call. The cost and latency tradeoffs are intentional.

**State files have strict schemas.** `seen_roles.json`, `runs.jsonl`, `feedback.md`, and `feedback_log.jsonl` all have documented formats. If you add a field, add a migration path in `state.py` and document the change in `CHANGELOG.md`.

**`profile.yaml` is user-editable.** Do not add machine-generated fields that would confuse a human reading it. Computed fields belong in `score.py`.

**Privacy boundary is absolute.** The only external services that should ever receive user data are the Anthropic API and Gmail. If you add a new integration, document exactly what data it receives in the Privacy section of the README and in a comment in the relevant module.

**Run `make test` before any commit.** If a change breaks tests and the tests are wrong, fix the tests too and explain why in the PR.

**Do not add dependencies without justification.** Every new package in `pyproject.toml` increases install time and attack surface. Add a comment explaining what it is for and what the alternative was.
