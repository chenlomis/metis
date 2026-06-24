# scorerole

An AI-powered job alert digest that reads your LinkedIn job alert emails, scores each role against your background and preferences, and delivers a ranked HTML digest — so you only spend time on roles that actually fit.

Built for senior individual contributors and managers who get flooded with alerts and need a faster way to triage.

---

## How it works

```
LinkedIn job alert emails
        ↓
  Gmail (IMAP)               ← scorerole reads your inbox
        ↓
  Claude (Sonnet)            ← scores each role against your profile
        ↓
  Ranked HTML digest         → delivered to your email
```

Each role gets a **score (0–100)**, a **verdict** (apply / consider / skip), lever and friction points, and highlight tags. Roles are deduplicated across runs — you won't see the same listing twice within 30 days.

---

## Prerequisites

| What | Why you need it | How to get it |
|---|---|---|
| **Python 3.11+** | Runtime | [python.org/downloads](https://www.python.org/downloads/) |
| **Anthropic API key** | Powers the scoring model | Create a developer account at [console.anthropic.com](https://console.anthropic.com) — **this is separate from your Claude.ai subscription**. The API is pay-per-use (not included in any monthly plan). Scoring a typical 10-job batch costs roughly $0.05–0.15. |
| **Gmail account** | Source of job alert emails | Must have IMAP enabled (Settings → See all settings → Forwarding and POP/IMAP → Enable IMAP) |
| **Gmail App Password** | Lets scorerole read your inbox without your main password | Requires 2-Step Verification to be turned on. [Generate one here](https://myaccount.google.com/apppasswords) — choose "Mail" and your device. Store this in `.env`, never your main account password. |
| **LinkedIn job alerts** | Source of job listings | Set up daily email alerts for your target role on LinkedIn (see setup below) |
| **Your resume** (PDF, DOCX, or TXT) | Used by `scorerole init` to build your profile | Your existing resume file |

**Operating environment:** macOS or Linux recommended. **Python 3.11+ required** — macOS ships with Python 3.9 which is too old. Install 3.11+ via [Homebrew](https://brew.sh): `brew install python@3.11`. Windows (WSL2) should work but is untested.

---

## Quickstart

```bash
# Clone and install
git clone https://github.com/YOUR_USERNAME/scorerole && cd scorerole
python3.11 -m venv venv && source venv/bin/activate
pip install --upgrade pip && pip install -e .

# (Optional) Rich React Email template — skip if you don't have Node installed
# A Python fallback renders the digest automatically if Node isn't available.
npm install

# Configure credentials
cp .env.example .env        # then edit .env with your API key and Gmail credentials

# Build your scoring profile from your resume
scorerole init

# Run
scorerole
```

> **First run?** A digest is only sent when LinkedIn alert emails exist in your lookback window. If you just set up alerts, wait for the first daily email, then re-run — or try `scorerole --lookback 14d` to cast a wider net.

**Python 3.11+ required.** macOS ships with 3.9, which is too old. Install via Homebrew: `brew install python@3.11`, then use `python3.11` above.

---

## Configuration

### `.env` file

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx

# Optional — defaults shown
RECIPIENT_EMAIL=you@gmail.com   # where to send the digest (defaults to GMAIL_ADDRESS)
MAX_JOBS_PER_RUN=20             # cap per run to control API cost; 0 = no cap
DEFAULT_LOOKBACK=3d             # how far back to fetch on each run
MODEL=claude-sonnet-4-6         # Claude model for full scoring
PRESCREEN_MODEL=claude-haiku-4-5  # model for the quick title/company pre-screen
                                   # (only used when role count exceeds MAX_JOBS_PER_RUN)
```

### Scoring profile

`scorerole init` creates `~/.job_pipeline/profile.yaml` from your resume. You can edit it any time — it's just a YAML file:

```yaml
candidate:
  name: "Your Name"
  location: "San Francisco, CA"
  open_to_remote: true

target:
  roles: ["Staff PM", "Director of Product"]
  level: staff

strengths:
  - "0-1 product builds — launched X from zero"
  - "ML/data background — [your experience]"

deal_breakers:
  - "no equity"
  - "on-site 5 days/week"
```

See [`profile.template.yaml`](./profile.template.yaml) for the full schema with comments.

---

## Commands

**Running the digest:**

| Command | What it does |
|---|---|
| `scorerole` | Fetch alerts → score → send digest (default: last 3 days) |
| `scorerole --lookback 7d` | Same, wider window. Accepts `7d`, `14d`, `yesterday`, or an ISO date like `2025-01-15` |
| `scorerole --no-limit` | Score everything in the lookback window, bypassing the per-run cap. Shows a cost estimate; Haiku pre-screens first to keep costs down. |
| `scorerole --no-limit --lookback 14d` | Catch-up run — useful after a gap or after `scorerole reset` |

**Profile setup:**

| Command | What it does |
|---|---|
| `scorerole init` | Conversational wizard: paste your resume + describe what you're looking for → Claude extracts and builds your profile. Re-run any time to update. |
| `scorerole init --resume path/to/resume.pdf` | Skip the resume path prompt |

**Reports:**

| Command | What it does |
|---|---|
| `scorerole report` | Score distribution, verdict breakdown, and run history from `~/.job_pipeline/runs.jsonl` |

**Automated scheduling:**

| Command | What it does |
|---|---|
| `scorerole schedule` | Show current schedule and OS job status |
| `scorerole schedule set` | Interactive wizard: choose frequency (daily / Mon+Thu / weekly) and time |
| `scorerole schedule remove` | Remove the scheduled job and clear `schedule.json` |

**Tracker and feedback:**

| Command | What it does |
|---|---|
| `scorerole track` | Parse confirmation and rejection emails → update `applications.xlsx` with status |
| `scorerole track --lookback 30d` | Extend the look-back window for email parsing |
| `scorerole track --dry-run` | Parse and classify emails, print matches, no xlsx write |
| `scorerole feedback` | Add calibration notes that shape future scoring (e.g. "deprioritize seed-stage roles") |
| `scorerole feedback list` | Show recent calibration entries |

**State and debugging:**

| Command | What it does |
|---|---|
| `scorerole reset` | Clear dedup state so all roles reprocess on next run (profile kept) |
| `scorerole reset --force` | Same, no confirmation prompt |
| `scorerole reset --profile` | Also delete your scoring profile — requires `scorerole init` before next run |
| `scorerole reset --profile --force` | Same, no confirmation |
| `scorerole debug` | Dump the most recent LinkedIn alert email to `~/.job_pipeline/debug_email.txt` |

---

## Setting up LinkedIn job alerts

scorerole reads emails that LinkedIn sends you — it doesn't scrape LinkedIn directly. You need at least a few alert emails in your inbox before the first run.

1. Go to [LinkedIn Jobs](https://www.linkedin.com/jobs/) and search for your target role and location
2. Click **"Set alert"** (or the bell icon near the search bar)
3. Set frequency to **Daily**
4. Repeat for any other searches you want to track

LinkedIn sends one email per saved search per day, listing 5–10 new roles. scorerole reads all of them.

> **Note:** scorerole reads emails from three LinkedIn senders:
> - `jobalerts-noreply@linkedin.com` — standard "Your job alert for X" digests
> - `jobs-noreply@linkedin.com` — "Company is hiring" / "Jobs similar to X" recommendation emails
> - `jobs-listings@linkedin.com` — "Jobs you might like" (JYMBII) digests
>
> It does not access any other emails. Only the INBOX is searched.
>
> **Two LinkedIn email formats are supported:**
> - **Multi-job digest** — subject "Lomis: your job alert for [Role]…" — lists several roles, each with a "View job: URL" line
> - **Individual job notification** — subject "[Job Title] at [Company] – Your job alert" — a single role per email, in the same format
>
> Both formats are parsed by the same logic: scorerole finds each "View job:" anchor and reads the title, company, and location from the lines immediately above it. Titles with em-dashes (e.g. "Lead PM – Risk Platform") are handled correctly.

---

## Ingesting your LinkedIn profile

The optional `--supplement` flag in `scorerole init` accepts any file that adds context about you. The easiest way to export your LinkedIn profile:

1. LinkedIn → Me → Settings & Privacy → Data Privacy → **Get a copy of your data**
2. Select **Profile** only → Request archive
3. LinkedIn emails you a download link (usually within minutes to a few hours)
4. Download, unzip, and pass the PDF or CSV to: `scorerole init --supplement ~/Downloads/Profile.pdf`

---

## Privacy

scorerole runs entirely on your machine. Here is exactly what leaves it:

| What | Sent where | When |
|---|---|---|
| Resume text (up to 12,000 chars) | Anthropic API | During `scorerole init` only — to extract your profile |
| Your scoring profile (career history, strengths, deal-breakers, salary floor) | Anthropic API | Every `scorerole` run — used as the scoring system prompt |
| Job titles, company names, JD text (≤1,500 chars per role) | Anthropic API | Every `scorerole` run — the roles being scored |
| IMAP login | Gmail only (SSL) | Every run — to fetch LinkedIn emails |
| SMTP login + digest HTML | Gmail only (SSL) | Every run — to deliver the digest email |

**Nothing is sent to any other third party.** Your Gmail App Password and Anthropic API key never leave your machine. Anthropic's API data-handling policies apply to content sent to the scoring API — see [anthropic.com/privacy](https://www.anthropic.com/privacy).

Data stored locally in `~/.job_pipeline/` (outside the repo, never committed):
- `profile.yaml` — your extracted profile (permissions: 600, owner-readable only)
- `seen_roles.json` — opaque MD5 hashes of scored roles + timestamps, 30-day TTL (permissions: 600)
- `logs/YYYY-MM-DD.log` — pipeline run logs (may contain job titles/companies from warning messages)

---

## Cost

Scoring a typical 10-job batch costs approximately **$0.05–0.15** with `claude-sonnet-4-6`. Running `scorerole` daily on a typical alert volume (20–30 roles/week) costs roughly **$0.50–2.00/month** at current pricing.

**When more than `MAX_JOBS_PER_RUN` new roles are found** (default: 20), scorerole pauses and shows the count and estimated API cost before proceeding. You can score everything (Haiku pre-screens first to cut cost ~40–60%), or let it cap. Roles beyond the cap stay unseen and reappear next run — they are never silently discarded.

Set `MAX_JOBS_PER_RUN=0` in `.env` to remove the cap entirely, or lower it to bound spend on any single run. A catch-up run after a gap: `scorerole --no-limit --lookback 14d`.

---

## Project layout

```
scorerole/
  pipeline.py      # CLI entry point and orchestration
  score.py         # Claude scoring logic (Layer 2 — Sonnet)
  extract.py       # Structured extraction (Layer 1 — Haiku, 27 fields)
  profile.py       # Profile loader (YAML → scoring prompt)
  prompts.py       # Canonical prompt templates (OSS-safe, no hardcoded names)
  init_cmd.py      # scorerole init — conversational profile setup wizard
  init2_cmd.py     # scorerole init2 — legacy structured form (kept for reference)
  render.py        # HTML digest renderer (React Email / Python fallback)
  report_cmd.py    # scorerole report — score distribution + run history
  feedback.py      # Feedback log: JSONL store + calibration parser
  sources_cmd.py   # scorerole sources — manage proactive company list
  track.py         # scorerole track — apply/rejection email parsing
  tracker.py       # applications.xlsx read/write
  trace.py         # runs.jsonl telemetry (every job at every pipeline stage)
  schedule_cmd.py  # scorerole schedule — launchd/cron wizard
  state.py         # Dedup state (seen_roles.json, 30-day TTL)
  theme.py         # Rich + InquirerPy theme (single source of truth)
  sources/         # Email ingestion (IMAP + LinkedIn parser + proactive ATS)

emails/            # React Email digest templates (TypeScript)
tests/             # pytest suite (397 tests)
profile.template.yaml   # Starter profile template
.env.example            # Credentials template
Makefile           # make test, make lint
```

---

## Troubleshooting

**"No emails in lookback window. Done." on first run**

This is the most common first-run experience. It means scorerole connected to Gmail successfully but found no LinkedIn alert emails in the last 3 days.
- If you just set up LinkedIn alerts, wait for the first daily email, then re-run.
- Widen the search: `scorerole --lookback 14d`
- Check that your alerts deliver to INBOX (not a label). Gmail filters that archive or label emails skip the INBOX search. Temporarily remove the "Skip Inbox" action from your LinkedIn filter.
- Run `scorerole debug` to see the most recent LinkedIn email raw body — useful for confirming the email format is parseable.

**`❌  Gmail login failed` / IMAP auth error**

Two things must be true: (1) your Gmail account has 2-Step Verification turned on, and (2) you're using a Gmail App Password (not your account password). Generate one at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) — choose "Mail" and your device. Paste the 16-character password (spaces optional) into `.env` as `GMAIL_APP_PASSWORD`.

Also verify IMAP is enabled: Gmail → Settings (gear icon) → See all settings → Forwarding and POP/IMAP tab → Enable IMAP → Save changes.

**"No roles to evaluate" despite having alert emails**

Run `scorerole debug` — it writes the raw email body to `~/.job_pipeline/debug_email.txt`. If the file is empty or the body looks different from a standard LinkedIn alert (e.g., it's a promotional email, not a job alert), scorerole can't parse it. Make sure your LinkedIn alert type is "Job recommendations" or "Your job alert for X" — not marketing emails.

**`❌  No scoring profile found. Run scorerole init`**

Run `scorerole init` to create your profile before running the digest.

**`ERROR: Invalid requirement: '#'` during `pip install -e .`**

This happens when a stale `scorerole.egg-info/` directory exists from a previous install (e.g. after `git pull` updating the dependency list). Delete it and reinstall:

```bash
rm -rf scorerole.egg-info
pip install -e .
```

**`scorerole: command not found` after install**

Make sure your virtualenv is activated: `source venv/bin/activate`. Then try `pip install -e .` again.

---

## Contributing

Bug reports and PRs welcome. Please open an issue before large changes.

---

## License

MIT
