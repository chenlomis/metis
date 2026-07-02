# metis

**Spend your time and energy on roles that matter.**

Metis is an AI-powered career agent that automates the first round of job discovery by screening and ranking new opportunities against your profile, experience, career goals, and deal breakers. It consolidates roles from job alerts and company career pages, compares them to your background, and delivers a personalized scored digest on a schedule you control. It can also track applications and recruiter responses in a spreadsheet, then generate summaries that show how your search is progressing over time.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![LLM providers](https://img.shields.io/badge/LLM-Anthropic%20%7C%20OpenAI-blueviolet.svg)](#env-configuration)

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

**Profile setup (`metis init`).** This interactive wizard builds your profile by reading your resume and LinkedIn profile, then asking about your aspirations, preferences, and deal breakers. Claude uses that context to generate `profile.yaml`, which every future scoring run is evaluated against. You can rerun the wizard or edit the file directly at any time.

On first setup, metis prompts you to connect Gmail or Outlook in the browser so it can read job-alert emails and send digests from your own account. You can also add company career-page sources and enable automated delivery during setup, or reconfigure them later with `metis config access`, `metis sources add`, and `metis schedule set`.

**Email digest (`metis`).** Each run ingests new roles from all configured sources, deduplicates across runs, extracts relevant info, and scores each role through a multi-stage LLM pipeline. The end result is an HTML email digest with scored roles. Every JD gets a categorical verdict and a 0-100 score:

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

The runtime operates as a closed-loop agentic workflow. New roles are continuously discovered, deduplicated, evaluated against a structured user profile, surfaced through personalized digests, tracked through recruiting outcomes, and refined using explicit user feedback. Every run persists metadata and intermediate artifacts (`profile.yaml`, `email_sources.yaml`, `runs.jsonl`, `feedback.md`, etc.), enabling reproducibility, debugging, and future iteration without relying on opaque prompt state.

The overall architecture prioritizes modularity, cost-aware inference, privacy-first local state, and continuous learning, allowing individual components to evolve independently as better models, data sources, or evaluation strategies become available.

![Metis architecture diagram](docs/assets/metis-architecture.png)

See [ARCHITECTURE.md](./ARCHITECTURE.md) for data flow diagrams and notes on extending each layer.

### Project files

```
metis/
  cli.py           # CLI parsing and command routing
  pipeline.py      # Digest pipeline orchestration
  score.py         # LLM scoring logic
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

### Local runtime files

metis keeps personal runtime state under `~/.job_pipeline/`:

| File | Purpose |
|------|---------|
| `profile.yaml` | Your active scoring profile from `metis init`. |
| `.env` | Local runtime config, including LLM provider keys and OAuth app credentials. |
| `gmail_token.json` | Local Gmail OAuth token cache, created after browser login. |
| `outlook_token.json` | Local Outlook OAuth token cache, created after browser login. |
| `email_provider.json` | Active inbox provider marker; the latest successful OAuth connection wins. |
| `seen_roles.json` | Dedup state so recently seen roles are not resent. |
| `feedback.md` / `feedback_log.jsonl` | Calibration notes from `metis feedback`. |
| `runs.jsonl` | Local scoring traces and run metadata for debugging. |
| `applications.xlsx` | Local application tracker. |

These files are personal and should not be committed. OAuth token files stay local to your machine.

---

## Prerequisites

Plan for about **10-15 minutes** to get the required prerequisites in place, plus up to 24 hours for the first LinkedIn alert email to arrive if you just created a new alert.

| What | Status | Why | How to get it |
|------|--------|-----|---------------|
| Python 3.11+ | **Required** | metis will not install or run on older Python versions, including the Python 3.9 that ships with macOS. | [python.org/downloads](https://www.python.org/downloads/) or Homebrew: `brew install python@3.11` |
| Node.js 18+ | **Optional** | Enables the React Email digest, which is the polished email layout. Without Node, metis falls back to a simpler Python HTML digest. | [nodejs.org](https://nodejs.org) or Homebrew: `brew install node` |
| LLM API key | **Required, save for [`.env`](#env-configuration)** | The configured provider reads your profile, compares each role against it, and writes the scoring explanations. Without this key, the digest pipeline cannot score jobs. | Anthropic: [console.anthropic.com](https://console.anthropic.com), keys usually start with `sk-ant-...`. OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys), keys usually start with `sk-...`. |
| Gmail or Outlook account | **Required** | Gives metis an inbox to read job-alert emails from and an account to send digests from. | Connect in the browser during `metis init` or later with `metis config access`. |
| OAuth app credentials | **Required for browser login, save for [`.env`](#env-configuration)** | Lets the local CLI complete Gmail or Outlook OAuth without storing your account password. | Gmail: create a Desktop OAuth client in Google Cloud. Outlook: create a Microsoft app registration with redirect URI `http://127.0.0.1:8766/oauth/callback`. |
| Gmail with IMAP enabled | **Optional fallback** | Legacy Gmail-only access if you skip OAuth. | Gmail > Settings > See all settings > Forwarding and POP/IMAP > Enable IMAP |
| Gmail App Password | **Optional fallback, save for [`.env`](#env-configuration)** | Legacy Gmail-only access if you skip OAuth. Use an App Password, not your Google password. | Requires 2-Step Verification. [Generate one here](https://myaccount.google.com/apppasswords), choose Mail + your device. |
| LinkedIn job alerts | **Recommended first source** | The most tested source today. metis can also watch other job-alert senders and company career pages, but LinkedIn saved alerts are the quickest path to a useful first digest. | Set up daily email alerts for your target roles on LinkedIn. See [Setting up LinkedIn alerts](#linkedin-alerts). |
| Your resume (PDF, DOCX, or TXT) | **Required** | The premise for scoring. Use the most complete, detailed version you have. | Any existing file on your machine. During setup, you can paste a path, tab-complete to it, or drag the file into the terminal. |

**Notes**

- **Platform support:** macOS and Linux. Windows via WSL2 should work but is untested.
- **Python versions:** Python 3.11 and 3.12 are tested in CI. Python 3.13 and 3.14 should work if the dependencies support them, but they are not part of the current test matrix yet.
- **Node.js install issues:** Node.js is the only optional prerequisite above. If `brew install node` gives you trouble, you can skip it and still run metis; the digest will use the Python fallback renderer.
- **LLM provider support:** Anthropic is the default and best-tested provider. OpenAI is supported across the public AI tasks (`metis`, `metis init`, `metis feedback`, and tracker LLM fallback), but still needs quality calibration for score parity. Gemini and Grok/XAI keys are reserved for future adapters and are not active yet.

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
| `METIS_LLM_PROVIDER` | No | Which provider to use. Defaults to `anthropic`; accepts `anthropic`/`claude` or `openai`/`open_ai`/`oai`/`chatgpt`, case-insensitive. |
| `ANTHROPIC_API_KEY` | Yes, when using Anthropic | Your Anthropic developer console API key |
| `OPENAI_API_KEY` | Yes, when using OpenAI | Your OpenAI project API key |
| `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` | Yes, for Gmail OAuth | Google Desktop OAuth client credentials |
| `OUTLOOK_CLIENT_ID` | Yes, for Outlook OAuth | Microsoft public-client app ID |
| `METIS_EMAIL_PROVIDER` | No | Optional override: `gmail_oauth`, `outlook_oauth`, or `imap`. Usually leave unset so the latest browser login wins. |
| `GMAIL_ADDRESS` | Only for IMAP fallback | Gmail address used by the legacy IMAP path |
| `GMAIL_APP_PASSWORD` | Only for IMAP fallback | The 16-character Google App Password generated for Mail |
| `RECIPIENT_EMAIL` | No | Where to send the digest. Defaults to the connected account when OAuth is used, or `GMAIL_ADDRESS` for IMAP fallback. |

Python, Node.js, job alerts, and your resume do not go into `.env`. Python and Node are installed on your machine, alerts arrive in your connected inbox, and your resume is selected during `metis init`.

```env
# Required
METIS_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...

# Inbox OAuth (recommended)
GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your-client-secret
OUTLOOK_CLIENT_ID=your-azure-app-client-id

# Legacy Gmail IMAP fallback only
# GMAIL_ADDRESS=you@gmail.com
# GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Optional (defaults shown)
RECIPIENT_EMAIL=you@gmail.com       # where to send the digest
MAX_JOBS_PER_RUN=40                 # cap per run to control API cost; 0 = no cap
DEFAULT_LOOKBACK=3d                 # how far back to fetch on each run
ANTHROPIC_MODEL=claude-sonnet-4-6
ANTHROPIC_PRESCREEN_MODEL=claude-haiku-4-5
ANTHROPIC_EXTRACT_MODEL=claude-haiku-4-5
OPENAI_MODEL=gpt-4.1
OPENAI_PRESCREEN_MODEL=gpt-4.1-mini
OPENAI_EXTRACT_MODEL=gpt-4.1-mini
```

Provider-specific model variables are preferred because they let you keep Anthropic and OpenAI settings side by side. The older generic `MODEL`, `PRESCREEN_MODEL`, and `EXTRACT_MODEL` variables still work for backward compatibility.

---

## Quick start

**Step 1 — Install**

```bash
brew install pipx && pipx ensurepath
pipx install git+https://github.com/chenlomis/metis.git
```

> Already installed? Run `pipx upgrade metis-job` instead.

**Step 2 — Configure credentials**

```bash
mkdir -p ~/.job_pipeline
cat > ~/.job_pipeline/.env << 'EOF'
METIS_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_CLIENT_ID=your-client-id.apps.googleusercontent.com
GMAIL_CLIENT_SECRET=your-client-secret
OUTLOOK_CLIENT_ID=your-azure-app-client-id
RECIPIENT_EMAIL=you@gmail.com
EOF
```

Then open `~/.job_pipeline/.env` and replace the placeholder values. See [`.env` configuration](#env-configuration) for field-by-field guidance.

**Step 3 — Build your scoring profile**

```bash
metis init
```

On first setup, metis prompts you to choose Gmail or Outlook access. Browser login grants metis permission to read job-alert emails and send digests from your own account. You can switch or reconnect later with `metis config access`.

**Step 4 — Run**

```bash
metis
```

> **Optional — Playwright-powered company sourcing:** metis can also pull roles directly from company career pages (Greenhouse, Lever, Ashby). This requires Playwright and is disabled by default. To enable:
> ```bash
> pipx inject metis-job playwright --include-apps
> playwright install chromium
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
| `metis --no-limit`                   | Score everything in the window, bypassing the per-run cap. The fast pre-screen model runs first. |
| `metis --no-limit --lookback 14d`    | Catch up after a gap by scoring everything from a wider window.                         |
| `metis init`                         | Build your scoring profile from your resume and preferences.                            |
| `metis config access`                | Connect, inspect, switch, or reconnect Gmail/Outlook access so metis can read job-alert emails and send digests from your own account. |

Each digest role gets a 0-100 score, a Solid Match / Moderate Match / Limited Match verdict, two leverage points, one friction point, and scan-friendly tags. Roles are deduplicated across runs, so the same listing should not reappear within 30 days.

`profile.yaml` is the scoring profile used by every future digest. See [profile.template.yaml](./profile.template.yaml) for the full schema with comments.

### Sources

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
| `metis sources email add`            | Add an email alert source interactively, or pass a sender address to skip the wizard.   |
| `metis sources email add <sender>`   | Fetch a recent email from that sender, preview parsed jobs, confirm in one step.        |
| `metis sources email remove`         | Remove a non-LinkedIn email alert source interactively.                                 |

LinkedIn alert senders are built in. Company sourcing can pull roles directly from Greenhouse, Lever, and Ashby career pages without waiting for a LinkedIn alert email.

### Scheduling

| Command                              | What it does                                                                            |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `metis schedule`                     | Show current digest schedule and OS job status.                                         |
| `metis schedule set`                 | Set up automated daily or weekly digest delivery.                                       |
| `metis schedule pause`               | Pause the schedule without deleting it.                                                 |
| `metis schedule resume`              | Resume a paused schedule.                                                               |
| `metis schedule remove`              | Remove the scheduled job.                                                               |

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

`metis track` recognizes confirmations, rejections, and recruiter-screen emails. Feedback is parsed by the configured LLM provider, confirmed before saving, and injected into future scoring runs.

### State and debugging

| Command                              | What it does                                                                            |
|--------------------------------------|-----------------------------------------------------------------------------------------|
| `metis reset`                        | Clear dedup state so old roles can appear again. Keeps your profile.                    |
| `metis reset --force`                | Clear dedup state without asking for confirmation.                                      |
| `metis reset --profile`              | Also delete your scoring profile. Run `metis init` before the next digest.              |
| `metis reset --profile --force`      | Delete dedup state and profile without asking for confirmation.                         |
| `metis debug`                        | Save the most recent LinkedIn alert email to `~/.job_pipeline/debug_email.txt`.         |

For explainability, metis keeps more detail locally than it shows in the email. The digest stays intentionally compact, but `~/.job_pipeline/runs.jsonl` records each scored role with model inputs, verdict, dimension scores, tags, leverage points, friction points, and gate/filter reasons. If a score feels off, inspect `runs.jsonl`, then use `metis feedback` to calibrate future runs.

---

## Privacy

metis runs entirely on your machine. Here is exactly what leaves it:

| What                                                              | Sent where       | When                    |
|-------------------------------------------------------------------|------------------|-------------------------|
| Resume text (up to 12,000 chars)                                  | Selected LLM provider | During `metis init` only |
| Feedback notes                                                    | Selected LLM provider | During `metis feedback` |
| Your scoring profile (career history, strengths, deal-breakers)   | Selected LLM provider | Every `metis` run       |
| Job titles, company names, JD text (up to 1,500 chars per role)   | Selected LLM provider | Every `metis` run       |
| IMAP login                                                        | Gmail only (SSL) | Every run               |
| SMTP login and digest HTML                                        | Gmail only (SSL) | Every run               |

Nothing goes to unconfigured LLM providers. Your Gmail App Password and provider API keys never leave your machine except when used to authenticate with Gmail or the selected LLM provider. Review the selected provider's data-handling policy before running: [Anthropic privacy](https://www.anthropic.com/privacy) or [OpenAI privacy](https://openai.com/policies/privacy-policy).

Local data stored in `~/.job_pipeline/` (outside the repo, never committed):

| File                  | Contents                                         | Permissions             |
|-----------------------|--------------------------------------------------|-------------------------|
| `profile.yaml`        | Your extracted profile                           | 600 (owner-readable)    |
| `seen_roles.json`     | MD5 hashes of scored roles and timestamps, 30-day TTL | 600 (owner-readable) |
| `role_queue.json`     | Pre-screened roles waiting for the next capped scoring run | 600 (owner-readable) |
| `runs.jsonl`          | Append-only scoring trace for debugging and summaries | 600 (owner-readable) |
| `feedback.md`         | Confirmed calibration notes injected into future scoring | 600 (owner-readable) |
| `email_sources.yaml`  | Extra non-LinkedIn alert sender rules                 | 600 (owner-readable) |
| `logs/YYYY-MM-DD.log` | Pipeline run logs (may contain job titles)       | default                 |

---

## Cost

Scoring a typical 10-job batch costs roughly $0.05-0.15 with `claude-sonnet-4-6`. Running `metis` daily on a typical alert volume (20-30 roles/week) runs about $0.50-2.00/month. OpenAI cost depends on the `OPENAI_MODEL`, `OPENAI_PRESCREEN_MODEL`, and `OPENAI_EXTRACT_MODEL` choices in `.env`.

Runtime depends on how many roles survive deduplication and pre-screening. A larger run may make several model calls: fast pre-screening, structured JD extraction, then full scoring. metis logs chunk progress while it works so long runs do not look frozen.

When more than `MAX_JOBS_PER_RUN` new roles appear (default: 40), metis pauses and shows the count and estimated cost before proceeding. If you choose fewer than the available roles, metis pre-screens the full batch, scores the freshest roles up to your chosen count, and stores the remaining pre-screen survivors in `role_queue.json` for the next run. They are never silently discarded or marked seen before scoring.

Set `MAX_JOBS_PER_RUN=0` in `.env` to remove the cap.

---

<a id="roadmap"></a>

## Current limits and roadmap

metis is intentionally a local, CLI-first v0. It works best today if you receive job-alert emails in Gmail or Outlook and are comfortable using either an Anthropic API key or an OpenAI key while provider quality is calibrated. Those are real constraints, not things the project tries to hide. The tradeoff is that the first version stays cheap, inspectable, and easy to run without hosting your career data somewhere else.

The source layer is broader than LinkedIn-only: LinkedIn saved alerts are the best-tested default, but `metis sources email add` can watch other job-alert senders. Wellfound (`team@hi.wellfound.com`) and Ladders (`jobs@my.theladders.com`) have dedicated parsers; any other sender falls back to LLM extraction automatically — no code change needed for new sources. `metis sources add` can also pull directly from company career pages on Greenhouse, Lever, and Ashby.

- [ ] More alert sources: Indeed, Wellfound/AngelList, Otta, RSS feeds, regional boards, and more non-LinkedIn email formats
- [ ] More company/ATS adapters and stronger browser-based scraping where APIs are unavailable
- [x] OAuth-based Gmail access for simpler browser login
- [x] Outlook / Microsoft 365 inbox support so Gmail is not the only option
- [x] Account switching and reconnect flow via `metis config access`
- [ ] Provider-neutral delivery and tracker/backfill paths
- [ ] Broaden LLM provider abstraction beyond Anthropic and OpenAI, including Gemini and Grok/XAI adapters
- [ ] Importable core API, with config passed as parameters instead of read at import time
- [ ] MCP server so metis can be queried from Claude Code and other local agents
- [ ] PyPI publish (`pip install metis-job`) for a cleaner install path
- [ ] Output targets beyond email, such as Markdown, Slack, Notion, or webhooks
- [ ] Deeper analytics over `runs.jsonl`, tracker outcomes, score trends, and market signals
- [ ] Resume tailoring and application-assist workflows, with human approval before anything is submitted
- [ ] Docker packaging for users who want to avoid local Python setup
- [ ] Web UI or local dashboard only if there is clear demand from non-CLI users

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
- LLM provider abstraction and score-parity tests across providers
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

**Prompt templates are contracts.** All LLM prompts live in `prompts.py`. Do not inline prompts in other files. When modifying a prompt, update `prompts.py` only, check that all callers still pass the required variables, and note what changed and why.

**The two-model architecture is load-bearing.** A fast model runs pre-screening. A stronger model runs full structured scoring. Do not collapse them into a single call. The cost and latency tradeoffs are intentional.

**State files have strict schemas.** `seen_roles.json`, `runs.jsonl`, `feedback.md`, and `feedback_log.jsonl` all have documented formats. If you add a field, add a migration path in `state.py` and document the change in `CHANGELOG.md`.

**`profile.yaml` is user-editable.** Do not add machine-generated fields that would confuse a human reading it. Computed fields belong in `score.py`.

**Privacy boundary is absolute.** The only external services that should ever receive user data are Gmail and the user-selected LLM provider. If you add a new integration, document exactly what data it receives in the Privacy section of the README and in a comment in the relevant module.

**Run `make test` before any commit.** If a change breaks tests and the tests are wrong, fix the tests too and explain why in the PR.

**Do not add dependencies without justification.** Every new package in `pyproject.toml` increases install time and attack surface. Add a comment explaining what it is for and what the alternative was.
