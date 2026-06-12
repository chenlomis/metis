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

Each role gets a **score (0–100)**, a **verdict** (apply / consider / skip), lever and friction points, and highlight tags. Roles are deduplicated across runs — you won't see the same listing twice within 14 days.

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
# 1. Clone and install
git clone https://github.com/YOUR_USERNAME/scorerole
cd scorerole
python3.11 -m venv venv && source venv/bin/activate   # macOS ships 3.9; use 3.11+
pip install --upgrade pip
pip install -e .

# 2. Configure credentials
cp .env.example .env
# Edit .env with your keys (see .env.example for all fields)

# 3. Create your scoring profile from your resume
scorerole init

# 4. Run
scorerole
```

That's it. After step 4, a digest email lands in your inbox within a minute.

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
MAX_JOBS_PER_RUN=20             # cap per run to control API cost
DEFAULT_LOOKBACK=3d             # how far back to fetch on each run
MODEL=claude-sonnet-4-6         # Claude model to use for scoring
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

| Command | What it does |
|---|---|
| `scorerole` | Fetch alerts → score → send digest (default: last 3 days) |
| `scorerole --lookback 7d` | Same, but look back further. Accepts `7d`, `2026-05-10`, `yesterday` |
| `scorerole init` | Interactive wizard: parse your resume, generate `~/.job_pipeline/profile.yaml` |
| `scorerole init --resume path/to/resume.pdf` | Skip the resume prompt |
| `scorerole init --supplement path/to/linkedin.pdf` | Add a LinkedIn export or supplementary file |
| `scorerole reset` | Clear dedup state so all roles reprocess on next run |
| `scorerole reset --force` | Same, no confirmation prompt |
| `scorerole debug` | Dump the most recent LinkedIn alert email to `~/.job_pipeline/debug_email.txt` |

---

## Setting up LinkedIn job alerts

scorerole reads emails that LinkedIn sends you — it doesn't scrape LinkedIn directly. You need at least a few alert emails in your inbox before the first run.

1. Go to [LinkedIn Jobs](https://www.linkedin.com/jobs/) and search for your target role and location
2. Click **"Set alert"** (or the bell icon near the search bar)
3. Set frequency to **Daily**
4. Repeat for any other searches you want to track

LinkedIn sends one email per saved search per day, listing 5–10 new roles. scorerole reads all of them.

> **Note:** scorerole only reads emails from `jobalerts-noreply@linkedin.com`. It does not access any other emails.

---

## Ingesting your LinkedIn profile

The optional `--supplement` flag in `scorerole init` accepts any file that adds context about you. The easiest way to export your LinkedIn profile:

1. LinkedIn → Me → Settings & Privacy → Data Privacy → **Get a copy of your data**
2. Select **Profile** only → Request archive
3. LinkedIn emails you a download link (usually within minutes to a few hours)
4. Download, unzip, and pass the PDF or CSV to: `scorerole init --supplement ~/Downloads/Profile.pdf`

---

## Privacy

- **Your data stays local.** scorerole runs entirely on your machine. Nothing is stored on a server.
- **Only job listings are sent to Claude.** Your resume and profile stay local; only job titles, company names, and JD text are included in scoring API calls.
- **Your Gmail credentials never leave your machine.** IMAP login happens locally; no credentials are sent to any third party.

---

## Cost

Scoring a typical 10-job batch uses roughly 8,000–15,000 tokens and costs approximately **$0.05–0.15** with `claude-sonnet-4-6`. Running `scorerole` daily on a typical alert volume (20–30 roles/week) costs roughly **$0.50–2.00/month** at current pricing.

Set `MAX_JOBS_PER_RUN` in `.env` to cap spend on any single run.

---

## Project layout

```
scorerole/
  pipeline.py      # CLI entry point and orchestration
  score.py         # Claude scoring logic
  profile.py       # Profile loader (YAML → scoring prompt)
  init_cmd.py      # scorerole init wizard
  render.py        # HTML digest renderer
  sources/         # Email ingestion (IMAP + LinkedIn parser)
  state.py         # Dedup state (seen_roles.json, 14-day TTL)

profile.template.yaml   # Starter profile template
.env.example            # Credentials template
```

---

## Troubleshooting

**`ERROR: Invalid requirement: '#'` during `pip install -e .`**

This happens when a stale `scorerole.egg-info/` directory exists from a previous install (e.g. after `git pull` updating the dependency list). Delete it and reinstall:

```bash
rm -rf scorerole.egg-info
pip install -e .
```

**`scorerole: command not found` after install**

Make sure your virtualenv is activated: `source venv/bin/activate`. Then try `pip install -e .` again.

**`FileNotFoundError: No profile found — run scorerole init`**

Run `scorerole init` to create your scoring profile before running the digest.

---

## Contributing

Bug reports and PRs welcome. Please open an issue before large changes.

---

## License

MIT
