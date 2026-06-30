# metis

**Spend your time and energy on roles that matter.**

Metis is an AI-powered career agent that automates the first round of job discovery by screening and ranking new opportunities against your profile, experience, career goals, and deal breakers. It consolidates roles from job alerts and company career pages, compares them to your background, and delivers a personalized scored digest on a schedule you control. It can also track applications and recruiter responses in a spreadsheet, then generate summaries that show how your search is progressing over time.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Powered by Claude](https://img.shields.io/badge/powered%20by-Claude%20Sonnet-blueviolet.svg)](https://www.anthropic.com)

![Metis demo preview](docs/assets/metis-demo-thumbnail.png)

<!--
Demo video note:
Upload the local demo MP4 to a GitHub Release or issue comment,
then replace the placeholder below with the generated GitHub asset URL.
Avoid committing the MP4 directly; it is over 100 MB.
-->
<!-- [Watch the demo](https://github.com/chenlomis/metis/assets/REPLACE_ME) -->

---

**Jump to:**
[What it does](#what-it-does) •
[How it works](#how-it-works) •
[Prerequisites](#prerequisites) •
[Quick start](#quick-start) •
[Commands](#commands) •
[Project files](#project-files) •
[Roadmap](#roadmap) •
[Contributing](#contributing)

---

## What it does

**Profile setup (`metis init`).** This is an interactive wizard that builds your profile by reading your resume, optionally enriching from LinkedIn, and asking about your aspirations, preferences, and deal breakers. Claude uses that context to build a `profile.yaml`, which every future scoring run is evaluated against. You can rerun it or edit the file directly at any time.

As part of setup, you can also add company career-page sources with `metis sources add` and enable automated scheduling with `metis schedule`. Both can be edited and reconfigured later.


**Email digest (`metis`).** Each run ingests new roles from all configured sources, deduplicates across runs, extracts relevant info, and scores each role through a multi-stage Claude pipeline. The end result is an HTML email digest with scored roles. Every JD gets a categorical verdict and a 0-100 score:

- Solid Match (75+): roles worth prioritizing
- Moderate Match (55-74): roles worth a closer look
- Limited Match (below 55): roles that do not clear the bar

Each evaluation surfaces two strengths and one potential friction point, alongside normalized tags for a quick scan. Roles in the Solid Match and Moderate Match tiers are automatically written to `applications.xlsx` to kickstart application tracking.

**Application tracking (`metis track`).** After you apply, this command scans your inbox for confirmation, rejection, and recruiter emails and updates `applications.xlsx`.

**Progress reporting (`metis summary`).** High-level insights across score distribution, verdict trends, and application rates. Useful both for tracking how your search has evolved and for spotting patterns that help calibrate your profile over time.

**Feedback tuning (`metis feedback`).** After reviewing the digest, app tracker, or overall summary, you can provide generic or specific feedback via this command, such as "seed-stage roles keep scoring high but I always skip them." The system parses it, asks you to confirm the signal, and injects it into future scoring runs as a high-priority calibration note.

---

## How it works

Metis is designed as a modular, stateful pipeline composed of loosely coupled subsystems connected through persistent artifacts rather than in-memory state. Instead of a single monolithic prompt, each stage has a focused responsibility—from profile construction and source ingestion to lightweight pre-screening, structured extraction, deep semantic evaluation, reporting, and feedback incorporation. This separation keeps the system observable, debuggable, and easily extensible.

The runtime operates as a closed-loop agentic workflow. New roles are continuously discovered, deduplicated, evaluated against a structured user profile, surfaced through personalized digests, tracked through recruiting outcomes, and refined using explicit user feedback. Every run persists metadata and intermediate artifacts (profile.yaml, sources.yaml, runs.jsonl, feedback.md, etc.), enabling reproducibility, debugging, and future iteration without relying on opaque prompt state.

The overall architecture prioritizes modularity, cost-aware inference, privacy-first local state, and continuous learning, allowing individual components to evolve independently as better models, data sources, or evaluation strategies become available.

![Metis architecture diagram](docs/assets/metis-architecture.png)

See [ARCHITECTURE.md](./ARCHITECTURE.md) for data flow diagrams and notes on extending each layer.

### Project files

```
metis/
  cli.py           # CLI parsing and command routing
  pipeline.py      # Digest pipeline orchestration
  score.py         # Claude scoring logic
  extract.py       # Structured JD extraction
  profile.py       # Profile loader
  prompts.py       # Canonical prompt templates
  init_cmd.py      # metis init profile setup wizard
  render.py        # HTML digest renderer
  report_cmd.py    # metis summary report
  feedback.py      # Feedback log and calibration parser
  sources_cmd.py   # metis sources command
  track.py         # metis track email parsing
  xlsx.py          # applications.xlsx read/write
  trace.py         # runs.jsonl telemetry
  schedule_cmd.py  # metis schedule wizard
  state.py         # Dedup state
  theme.py         # Rich and InquirerPy theme
  sources/         # Email and career-page ingestion

emails/            # React Email digest templates
tests/             # pytest suite
profile.template.yaml
.env.example
Makefile
```

The CLI surface is listed in [Commands](#commands).

---

## Prerequisites

Plan for about **5-10 minutes** to get the required prerequisites in place, plus up to 24 hours for the first LinkedIn alert email to arrive if you just created a new alert.

| What | Status | Why | How to get it |
|------|--------|-----|---------------|
| Python 3.11+ | **Required** | metis will not install or run on older Python versions, including the Python 3.9 that ships with macOS. | [python.org/downloads](https://www.python.org/downloads/) or Homebrew: `brew install python@3.11` |
| Node.js 18+ | **Optional** | Enables the React Email digest, which is the polished email layout. Without Node, metis falls back to a simpler Python HTML digest. | [nodejs.org](https://nodejs.org) or Homebrew: `brew install node` |
| Anthropic API key | **Required, save for [`.env`](#env-configuration)** | Claude reads your profile, compares each role against it, and writes the scoring explanations. Without this key, the pipeline cannot score jobs. | [console.anthropic.com](https://console.anthropic.com). Requires an Anthropic developer account, not a regular Claude.ai chat subscription. Keys usually start with `sk-ant-...`. |
| Gmail with IMAP enabled | **Required** | Lets metis scan your Gmail inbox for job alert messages. | Gmail > Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP |
| Gmail App Password | **Required, save for [`.env`](#env-configuration)** | Lets metis log in via IMAP without storing your main Google password. | Requires 2-Step Verification. [Generate one here](https://myaccount.google.com/apppasswords), choose Mail + your device. Google shows this as a 16-character password, often grouped like `abcd efgh ijkl mnop`; save it for `.env` without spaces. |
| LinkedIn job alerts | **Required** | Main listing source today. The source layer is extensible, but other alert providers are not wired up yet. | Set up daily email alerts for your target roles on LinkedIn. See [Setting up LinkedIn alerts](#linkedin-alerts). |
| Your Gmail address | **Required, save for [`.env`](#env-configuration)** | Tells metis which Gmail inbox to scan for LinkedIn alerts. | Use the Gmail address where your LinkedIn job alerts arrive. |
| Your resume (PDF, DOCX, or TXT) | **Required** | The premise for scoring. Use the most complete, detailed version you have. | Any existing file on your machine. During setup, you can paste a path, tab-complete to it, or drag the file into the terminal. |

**Notes**

- **Platform support:** macOS and Linux. Windows via WSL2 should work but is untested.
- **Python versions:** Python 3.11 and 3.12 are tested in CI. Python 3.13 and 3.14 should work if the dependencies support them, but they are not part of the current test matrix yet.
- **Node.js install issues:** Node.js is the only optional prerequisite above. If `brew install node` gives you trouble, you can skip it and still run metis; the digest will use the Python fallback renderer.
- **Anthropic only for now:** OpenAI keys will not work until metis adds an LLM wrapper or provider abstraction. Scoring a 10-job batch usually costs roughly $0.05-0.15.

<a id="linkedin-alerts"></a>

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

<a id="env-configuration"></a>

### `.env` configuration

The Quick start creates your personal config file here:

```bash
~/.job_pipeline/.env
```

You should not need to create folders or copy templates by hand. Follow the **Configure credentials** step in [Quick start](#quick-start), then open this file and replace the placeholder strings.

On macOS, `~` means your home folder, usually `/Users/<your-mac-username>`. To open the config folder in Finder:

1. Click **Go > Go to Folder...**
2. Paste `~/.job_pipeline`
3. Open `.env`

If `.env` is missing, rerun the **Configure credentials** command in [Quick start](#quick-start). If you cloned the repo and want the original template, it lives at `.env.example` in the project root.

These prerequisite values become `.env` entries:

| `.env` value | Required? | Comes from |
|--------------|-----------|------------|
| `ANTHROPIC_API_KEY` | Yes | Your Anthropic developer console API key |
| `GMAIL_ADDRESS` | Yes | The Gmail address that receives LinkedIn job alerts |
| `GMAIL_APP_PASSWORD` | Yes | The 16-character Google App Password generated for Mail |
| `RECIPIENT_EMAIL` | No | Where to send the digest. Defaults to `GMAIL_ADDRESS` if omitted. |

Python, Node.js, LinkedIn alerts, and your resume do not go into `.env`. Python and Node are installed on your machine, LinkedIn alerts arrive in Gmail, and your resume is selected during `metis init`.

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Optional (defaults shown)
RECIPIENT_EMAIL=you@gmail.com       # where to send the digest (defaults to GMAIL_ADDRESS)
MAX_JOBS_PER_RUN=40                 # cap per run to control API cost; 0 = no cap
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
> # Then: metis sources add Stripe
> ```

**Expected output:** metis fetches LinkedIn alert emails from the last 3 days, scores each role, and emails you a ranked digest. On first run this usually takes 30-90 seconds for small batches, and longer if many roles need JD extraction and scoring.

> **No emails found?** LinkedIn alert emails may take up to 24 hours to arrive after setup. Run `metis --lookback 14d` to cast a wider net, or see [Troubleshooting](#troubleshooting).

---

## Commands

### Core workflow

| Command                              | What it does                                                                            |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `metis`                              | Run full pipeline: ingest, dedupe, score, and email digest. Default: last run or 3d.    |
| `metis --lookback 7d`                | Same pipeline with a wider window. Accepts `7d`, `14d`, or ISO date like `2026-05-10`.  |
| `metis --dry-run`                    | Preview a full fetch + score run without sending email or writing state.                |
| `metis --no-limit`                   | Score everything in the window, bypassing the per-run cap. Haiku pre-screens first.     |
| `metis --no-limit --lookback 14d`    | Catch up after a gap by scoring everything from a wider window.                         |
| `metis init`                         | Build your scoring profile from your resume and preferences.                            |

Each digest role gets a 0-100 score, a Solid Match / Moderate Match / Limited Match verdict, two leverage points, one friction point, and scan-friendly tags. Roles are deduplicated across runs, so the same listing should not reappear within 30 days.

`profile.yaml` is the scoring profile used by every future digest. See [profile.template.yaml](./profile.template.yaml) for the full schema with comments.

### Sources and scheduling

| Command                              | What it does                                                                            |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `metis sources [list]`               | Show active email alerts and company career pages.                                      |
| `metis sources add`                  | Pick an alert source or company source interactively.                                   |
| `metis sources add NAME`             | Add a company for proactive scraping. Auto-detects Greenhouse, Lever, or Ashby.         |
| `metis sources add --all`            | Add every company in the built-in pool.                                                 |
| `metis sources remove`               | Remove company sources interactively.                                                   |
| `metis sources on`                   | Turn company career-page scraping on.                                                   |
| `metis sources off`                  | Turn company scraping off without losing your company list.                             |
| `metis sources email`                | Show built-in LinkedIn alerts and any extra email alert sources.                        |
| `metis sources email add`            | Add a non-LinkedIn email alert source interactively.                                    |
| `metis sources email remove`         | Remove a non-LinkedIn email alert source interactively.                                 |
| `metis schedule`                     | Show current digest schedule and OS job status.                                         |
| `metis schedule set`                 | Set up automated daily or weekly digest delivery.                                       |
| `metis schedule pause`               | Pause the schedule without deleting it.                                                 |
| `metis schedule resume`              | Resume a paused schedule.                                                               |
| `metis schedule remove`              | Remove the scheduled job.                                                               |

LinkedIn alert senders are built in. Company sourcing can pull roles directly from Greenhouse, Lever, and Ashby career pages without waiting for a LinkedIn alert email.

### Tracking, reporting, and feedback

| Command                              | What it does                                                                            |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `metis track`                        | Parse inbox for application outcomes and update `applications.xlsx`.                    |
| `metis track --lookback 30d`         | Scan a wider email window.                                                              |
| `metis track --dry-run`              | Print matches without writing to the tracker.                                           |
| `metis summary`                      | Generate and email progress report with score trends and search insights.               |
| `metis summary --lookback 60d`       | Scope market intel to a 60-day window. Default is 30d.                                  |
| `metis summary --output report.html` | Save the report as HTML instead of sending it.                                          |
| `metis summary --output report.pdf`  | Save the report as PDF instead of sending it.                                           |
| `metis summary --preview`            | Send the report with a `[DRAFT PREVIEW]` subject prefix.                                |
| `metis feedback`                     | Add calibration notes that improve future scoring runs.                                 |
| `metis feedback add`                 | Same as `metis feedback`.                                                               |
| `metis feedback list`                | Show recent feedback entries.                                                           |

`metis track` recognizes confirmations, rejections, and recruiter-screen emails. Feedback is parsed by Claude, confirmed before saving, and injected into future scoring runs.

### State and debugging

| Command                              | What it does                                                                            |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `metis reset`                        | Clear dedup state so old roles can appear again. Keeps your profile.                    |
| `metis reset --force`                | Clear dedup state without asking for confirmation.                                      |
| `metis reset --profile`              | Also delete your scoring profile. Run `metis init` before the next digest.              |
| `metis reset --profile --force`      | Delete dedup state and profile without asking for confirmation.                         |
| `metis debug`                        | Save the most recent LinkedIn alert email to `~/.job_pipeline/debug_email.txt`.         |

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
| `role_queue.json`     | Pre-screened roles waiting for the next capped scoring run | 600 (owner-readable) |
| `logs/YYYY-MM-DD.log` | Pipeline run logs (may contain job titles)       | default                 |

---

## Cost

Scoring a typical 10-job batch costs roughly $0.05-0.15 with `claude-sonnet-4-6`. Running `metis` daily on a typical alert volume (20-30 roles/week) runs about $0.50-2.00/month.

Runtime depends on how many roles survive deduplication and pre-screening. A larger run may make several model calls: Haiku pre-screening, structured JD extraction, then Sonnet scoring. metis logs chunk progress while it works so long runs do not look frozen.

When more than `MAX_JOBS_PER_RUN` new roles appear (default: 40), metis pauses and shows the count and estimated cost before proceeding. If you choose fewer than the available roles, metis pre-screens the full batch, scores the freshest roles up to your chosen count, and stores the remaining pre-screen survivors in `role_queue.json` for the next run. They are never silently discarded or marked seen before scoring.

Set `MAX_JOBS_PER_RUN=0` in `.env` to remove the cap.

---

## Roadmap

- [ ] MCP server so metis can be queried from Claude Code and other local agents
- [ ] Importable core API, with config passed as parameters instead of read at import time
- [ ] PyPI publish (`pip install metis-job`) for a cleaner install path
- [ ] Outlook / Microsoft 365 support
- [ ] More alert sources and smarter parsing for new job-alert formats
- [ ] LLM provider abstraction so Anthropic is not the only scoring backend
- [ ] Resume tailoring and application-assist workflows, with human approval before anything is submitted
- [ ] Docker packaging for users who want to avoid local Python setup
- [ ] Web UI only if there is clear demand from non-CLI users

See [open issues](https://github.com/chenlomis/metis/issues) for the full list.

---

## Troubleshooting

Start with the earliest step that matches what you are seeing. Most issues are setup, Gmail access, alert delivery, or local state.

**`metis: command not found` after install**

Run `pipx ensurepath`, open a new terminal, and try `metis --help`. If that works, your shell can find the CLI.

**`Gmail login failed` / IMAP auth error**

Two things must both be true: 2-Step Verification is on, and `GMAIL_APP_PASSWORD` is a Google App Password, not your normal Google password.

Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords). Also confirm IMAP is enabled: Gmail > Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP.

**`No scoring profile found. Run metis init`**

This happens when metis reaches scoring but cannot find your profile file. Run `metis init` first, then run `metis` again.

If you already ran setup, confirm that `~/.job_pipeline/profile.yaml` exists. If you are using persona testing, also check whether `METIS_PROFILE` points somewhere else.

**"No emails in lookback window. Done." on first run**

metis connected to Gmail, but did not find LinkedIn alert emails in the lookback window. Try:

- Run `metis --lookback 14d` to check a wider window.
- Make sure at least one LinkedIn alert email has arrived. New alerts can take up to 24 hours.
- Check that alerts land in INBOX. Gmail filters that archive or "Skip Inbox" will hide them from metis.
- Run `metis debug` to dump the most recent LinkedIn alert email and inspect what metis is seeing.

`metis debug` writes `~/.job_pipeline/debug_email.txt` and prints the first chunk in the terminal. It is useful when you want to confirm whether Gmail contains a real job-alert email or just a promotional/recommendation email that metis cannot parse.

**"No roles to evaluate" despite having alert emails**

Run `metis debug` and open `~/.job_pipeline/debug_email.txt`. If the email is not a job alert with role links, metis may not be able to parse it.

Good signs: subject lines like "Your job alert for X" or "Job recommendations," with a list of roles in the body.

Less useful signs: promotional emails, in-app notification summaries, or generic "Company is hiring" messages without role links.

**A company appeared in my LinkedIn notifications but not in my digest**

LinkedIn has two separate channels: **email job alerts** (what metis reads) and **in-app push notifications** (what you see in the LinkedIn app's notification bell). These are different systems. Push notification types that do NOT produce emails:
- "Company X is hiring. Apply today.", which is a company page hiring announcement
- "Results from the new AI-powered job search," which is LinkedIn's in-app AI recommender
- "Jobs similar to one you recently viewed," which comes from LinkedIn's recommendation engine

Only saved job search alerts (set to Daily frequency from a search results page) reliably produce emails. If a company you care about isn't generating email alerts, add it to proactive sources: `metis sources add <name>`.

**A specific role is missing, but I know it exists**

Roles that were processed (even if filtered or skipped) are recorded in `~/.job_pipeline/seen_roles.json` with a 30-day TTL. Once a role is in that file it won't reappear regardless of verdict. Common causes:
- The role is outside your lookback window. Try `metis --lookback 14d --dry-run`.
- The role was already processed in the last 30 days.
- The role was filtered by a hard gate (`jd_blank` means the ATS returned an empty job description; `salary_floor` means the disclosed salary was below your floor).
- Your deal-breaker list had a mismatch when the role was first processed.

If you want metis to reconsider everything, run `metis reset`. That clears dedup state, so previously seen roles can appear again.

**`metis summary` is empty or less useful than expected**

`metis summary` reads your tracker and run history. It gets better after you have at least one digest and a few tracker updates.

Try:
- Run `metis` first so there is scored-role history.
- Run `metis track` after applying so `applications.xlsx` has outcomes.
- Use `metis summary --lookback 60d` if your recent window is too quiet.

**Contributor-only install issue: `ERROR: Invalid requirement: '#'` during `pip install -e .`**

This usually means a stale `metis.egg-info/` directory is confusing editable install. From a local clone, delete it and reinstall:

```bash
rm -rf metis.egg-info && pip install -e .
```

---

## Contributing

Issues, ideas, docs fixes, and PRs are welcome. Small improvements can go straight to PR; for larger changes, open an issue first so we can align before you spend time building.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup and PR guidance. For private vulnerability reports, see [SECURITY.md](./SECURITY.md).

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
- Email parsing edge cases and new alert formats
- Outlook / Microsoft 365 support
- New job sources and company/ATS adapters
- Output targets beyond email, such as chat or local agent surfaces
- LLM provider abstraction
- Resume tailoring and application-assist workflows with human approval
- Globalization: non-English alerts, international salary/location handling, and regional job boards
- Tests around state safety, dry-run behavior, scoring contracts, and scheduling

For deeper roadmap context, see [Roadmap](#roadmap). For engineering boundaries and privacy rules, see [CONTRIBUTING.md](./CONTRIBUTING.md).

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
