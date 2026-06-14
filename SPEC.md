# scorerole — Product Spec

> Status: v0.1 — retroactively written from build history, updated June 2026.
> For setup instructions see README.md. This document defines what the product
> should do and how we know it's correct.

---

## 1. Problem Statement

Job seekers receive dozens of job alert emails containing hundreds of potentially
relevant roles. Reviewing each individually — going through the JD, comparing it with
background, and deciding if it's worthwhile to apply — not only consumes time but also
limited attention. At 50 roles/week that's 2–4 hours of low-signal scanning before any
actual application work begins.

**scorerole** automates the screening step. It reads your job alert emails, scores
each role against a structured profile of your background and preferences, and delivers
a ranked digest that surfaces only the roles worth your time — with enough context
(match rationale, friction points) to make a quick apply/skip decision.

---

## 2. What It Is / Is Not

| ✅ In scope | ❌ Out of scope |
|---|---|
| Ingesting LinkedIn job alert emails via IMAP | Auto-applying to jobs (HITL required for any submission) |
| Scoring roles against a user-defined profile | Tracking application status post-apply |
| Delivering a ranked HTML digest by email | Multi-user / team / SaaS features |
| Profile setup and editing via interactive CLI | Real-time / streaming alerts |
| Configurable lookback, cap, and score thresholds | |
| Extensible source layer (see §9 Q3 for roadmap) | |

---

## 3. User Persona

**Primary:** Entry-level to mid-senior individual contributors actively job searching,
relying on LinkedIn job alerts as their primary discovery platform, comfortable with
lightweight CLI commands, who find manual screening a time drain.

---

## 4. Core User Flows

---

### Flow 1 — Profile Setup (`scorerole init`)

**Goal:** A new user completes setup in under 5 minutes and has a working profile
ready to score against.

#### 1a — First-time Setup

```
1. Install
     pip install -e .

2. Configure credentials
     Copy .env.example → .env and fill in three values:

     ANTHROPIC_API_KEY      Developer API key (separate from your Claude.ai subscription).
                            Get one at: console.anthropic.com
     GMAIL_ADDRESS          Your Gmail address.
     GMAIL_APP_PASSWORD     A Gmail App Password — NOT your account password.
                            Requires 2FA. Generate at: myaccount.google.com/apppasswords
     RECIPIENT_EMAIL        Where to send the digest (normally your own email).

3. Create your profile
     scorerole init

     Step 1 — Resume
               Provide the path to your resume (PDF, DOCX, or TXT).
               Claude extracts your experience, education, and strengths.

     Step 2 — LinkedIn profile (optional)
               Provide your LinkedIn profile URL or paste your profile text.
               Supplements the resume extraction.

     Step 3 — Career context
               Answer guided questions covering:
               • Work mode (remote / hybrid / on-site) and location preferences
               • Target roles and seniority level
               • Career aspirations and direction
               • Deal breakers (roles that should never appear in your digest)
               • Minimum salary floor

     Step 4 — Review and save
               Review the extracted profile. Edit any section before saving.
               Profile is saved to ~/.job_pipeline/profile.yaml.

4. Run
     scorerole  (see Flow 2)
```

**Exit criteria:**
- [ ] End-to-end first-time setup completes in under 5 minutes
- [ ] `scorerole init` completes without error given a valid PDF, DOCX, or TXT resume
- [ ] Profile is saved to `~/.job_pipeline/profile.yaml` after step 4
- [ ] Missing `.env` key → error names the missing variable and links to where to get it
- [ ] Resume file not found → re-prompts for path; no crash
- [ ] Claude API key invalid → clear error before any wizard steps run

#### 1b — Profile Update (existing profile)

When `scorerole init` detects an existing profile, it shows a mode menu instead of
restarting the full wizard:

```
Quick edit    — jump to section-by-section review; edit any field; no API call
Open in editor — open ~/.job_pipeline/profile.yaml directly in your system editor
Start fresh   — full 4-step wizard with a new resume; overwrites existing profile
```

**Exit criteria:**
- [ ] Mode menu appears automatically when a profile already exists
- [ ] Quick edit does not call Claude or consume API tokens
- [ ] "Start fresh" requires an explicit menu selection; cannot be triggered accidentally
- [ ] The latest saved version always overrides the previous one
- [ ] Profile changes take effect on the very next `scorerole` run

---

### Flow 2 — Running the Pipeline (`scorerole`)

**Goal:** User runs `scorerole` and receives a ranked digest of new roles worth reviewing.

```
scorerole [--lookback DURATION] [--all]
```

**Default behaviour:**
```
→ Fetches LinkedIn alert emails from the past 3 days
  (default; customisable via --lookback flag or DEFAULT_LOOKBACK in .env)
→ Skips roles already evaluated in the past 14 days
→ Evaluates up to 20 new roles
  (default cap; customisable via MAX_JOBS_PER_RUN in .env)
→ Sends HTML digest to RECIPIENT_EMAIL
```

**When more than 20 new roles are found:**
```
→ User is notified interactively:
    "Found 47 new roles. Evaluating first 20.
     Remaining 27 will appear in your next run.
     To evaluate all now: scorerole --all  (~$0.24–$0.71 estimated)"
→ Roles beyond the cap are NOT marked as seen — they appear in the next run
```

**With `--all` flag:**
```
scorerole --all [--lookback DURATION]
→ Bypasses the per-run cap (does NOT bypass the 14-day dedup gate)
→ Haiku pre-screens all roles on title+company to filter obvious mismatches cheaply
→ Estimated API cost is shown before scoring begins
→ Sonnet scores the survivors; digest is delivered
```

**When no new roles are found:**
```
→ "No new roles to evaluate — all already seen within the past 14 days."
→ No digest sent; no error; exit 0
```

**Exit criteria:**
- [ ] Roles seen in a previous digest do not reappear within the 14-day window
- [ ] Roles beyond the cap are NOT written to `seen_roles.json`; they reappear next run
- [ ] Running `scorerole` twice with no new emails shows "no new roles", not empty digest
- [ ] User is notified when the role count exceeds the cap (interactive runs)
- [ ] `--all` shows a cost estimate before making any scoring API calls
- [ ] Cron / non-interactive runs never block on a prompt; they cap silently and log
- [ ] scorerole does not re-evaluate previously seen roles unless `scorerole reset` is run
- [ ] Digest has a default visual style; visual customisation requires editing `render.py`

---

### Flow 3 — Reset & Troubleshoot (`scorerole reset`, `scorerole debug`)

**Goal:** User needs to inspect what's happening or clear state after a gap or issue.

```
scorerole debug
  → Fetches and dumps the most recent LinkedIn alert email body
  → Saves to ~/.job_pipeline/debug_email.txt; prints first 2000 chars to terminal
  → Useful for diagnosing why expected roles aren't appearing

scorerole reset
  → Clears seen_roles.json (dedup state only; profile is preserved)
  → Prompts for confirmation before deleting
  → Next run re-evaluates all roles within the lookback window

scorerole reset --profile
  → Also deletes ~/.job_pipeline/profile.yaml
  → scorerole init must be run before the next pipeline run
```

**Exit criteria:**
- [ ] `scorerole debug` produces output regardless of whether jobs were found; never crashes
- [ ] `scorerole reset` prompts for confirmation; does not delete without it
- [ ] After `scorerole reset`, a same-day run re-evaluates previously seen roles
- [ ] `scorerole reset --profile` + `scorerole` without init → clear error pointing to
  `scorerole init`, not a Python traceback

---

## 5. Profile Schema

All fields are user-editable — either through `scorerole init` or by editing
`~/.job_pipeline/profile.yaml` directly.

| Section | What it captures |
|---|---|
| `candidate` | Name, current title, location, work mode, seniority |
| `target` | Desired roles, target level, industries |
| `aspirations` | Career track (IC vs. management), direction, company types to seek or avoid |
| `preferences` | Company stage, industry targets, aspirational base salary |
| `scoring` | Apply / consider thresholds; level-mismatch score deduction |
| `experience` | Role-by-role history with achievement highlights |
| `education` | Degrees and institutions |
| `strengths` | 3–6 differentiating capabilities |
| `green_flags` | Signals that boost a role's score |
| `yellow_flags` | Cautions worth surfacing but not disqualifying |
| `red_flags` | Soft negatives |
| `deal_breakers` | Hard disqualifiers — roles violating these are filtered from the digest entirely and shown only as a footer count |
| `salary_floor_usd` | Minimum acceptable base salary (hard gate — see §9 Q4) |
| `notes` | Free-text context passed verbatim to Claude at scoring time |

**Notes:**
- Missing optional sections are silently omitted; they do not crash scoring
- A minimal profile with only `candidate` + `experience` still produces meaningful scores
- Per-criterion score weighting is not yet implemented; all criteria are currently
  weighted equally by Claude. The `notes` field can be used to emphasise priorities
  in natural language until explicit weighting is added.

---

## 6. Digest Output

The digest is an HTML email. The default style is fixed; visual customisation
requires editing `render.py` or the React Email template (`render.ts`).

**Structure:**
```
Header:   "Personalized Job Alert Digest — [date]"
Stat row: [N roles evaluated]  [N apply]  [N consider]
Legend:   green = strength match · amber = proceed with awareness · red = real concern

── Apply  (score ≥ 75) ──────────────────────────────────────────────────
  Title                                                          [score%]
  Company · Location
  ↑ Leverage:  topic — evidence clause (1–2 points)
  ↓ Friction:  topic — concern clause  (0–1; omitted if none)
  [tag]  [tag]  [tag]
                                                       [View posting →]

── Consider  (score 55–74) ──────────────────────────────────────────────
  [same card layout]

── Skipped  (score < 55) ────────────────────────────────────────────────
  [compact 2-column grid: title · company · location · top friction tag]

── Footer ───────────────────────────────────────────────────────────────
  scorerole · powered by Claude · N roles evaluated
  [· N filtered by deal-breaker]  ← shown only when roles were filtered
```

**Exit criteria:**
- [ ] Every "Apply" role has score ≥ 75; every "Consider" role has score 55–74
  (re-validated in code before rendering — Claude's verdict is not trusted directly)
- [ ] Roles violating a deal_breaker appear only in the footer count, never in sections
- [ ] Leverage points use conclusion-first format: `"topic — evidence clause"`
  Never: `"JD needs X → candidate has Y"`
- [ ] Friction is a specific, honest concern or absent; never `"none"`, `"n/a"`, etc.
- [ ] Digest renders at 600px max width with no overflow or broken layout
- [ ] "View posting →" links resolve to the correct LinkedIn job URL
- [ ] Digest delivered within a few minutes of running `scorerole`
  (target: 60–90 seconds for a 20-role run)

---

## 7. Configurability

All user-facing configuration lives in two places: `.env` (runtime and secrets) and
`profile.yaml` (scoring criteria). No code changes required for the settings below.

| What to configure | How | Default | Notes |
|---|---|---|---|
| Lookback window | `--lookback` flag or `DEFAULT_LOOKBACK` in `.env` | `3d` | Accepts `7d`, `14d`, `2026-06-01`, `yesterday` |
| Max roles per run | `MAX_JOBS_PER_RUN` in `.env` | `20` | Set to `0` for no cap |
| Score all roles (bypass cap) | `--all` flag | off | Bypasses the per-run cap only. Does NOT bypass the 14-day dedup gate. Use `scorerole reset` to clear dedup state. |
| Apply threshold | `scoring.apply_threshold` in `profile.yaml` | `75` | Roles at or above → "Apply" |
| Consider threshold | `scoring.consider_threshold` in `profile.yaml` | `55` | Roles between thresholds → "Consider"; below → "Skipped" |
| Level-mismatch penalty | `scoring.level_mismatch_deduction` in `profile.yaml` | `10` | Deducted when job title lacks a seniority signal (Staff / Lead / Director / VP / etc.) |

**Planned but not yet implemented:**
- Per-criterion score weighting (e.g., weight remote policy 2×)
- `--max N` flag for a one-off cap override without editing `.env`
- Salary floor hard-filter (Q4): filter roles whose listed salary is clearly below
  `salary_floor_usd`; add amber tag when within 10% buffer

---

## 8. Non-Functional Requirements

| Requirement | Target |
|---|---|
| First-time setup | < 5 minutes from install to first digest |
| Per-run time | 60–90s for 20 roles. > 10 min warrants investigation. |
| API cost (20 roles) | < $0.30 |
| API cost (100 roles with Haiku pre-screen) | < $1.50 |
| Sensitive files | `.env` and `~/.job_pipeline/profile.yaml` are never committed to git |
| Errors | Config errors exit with a clear message and a specific fix instruction. SMTP delivery failures exit with code 1 and a log message. Parse failures fall back gracefully (partial JSON recovery; no silent data loss). |

---

## 9. Decisions Log

Rationale for non-obvious product decisions:

| # | Decision | Rationale |
|---|---|---|
| Q1 | Run latency: 60–90s for 20 roles is acceptable. > 10 min = investigate. | Dominated by sequential JD HTTP fetches; parallelisation is possible but not needed yet. |
| Q2 | `deal_breakers` are hard filters, not score penalties. | A deal-breaker violation means the role should never appear in the digest, period. Future: opt-in soft-filter mode for users who prefer a penalty. |
| Q3 | Future sources: Tier A (IMAP email — Indeed, Glassdoor); Tier B (HTTP/RSS — VC boards like a16z). | Tier A reuses the existing IMAP parser. Tier B is a separate engineering track with different auth and scraping concerns. |
| Q4 | `salary_floor_usd` is a hard gate with a 10% negotiation buffer. | If listed salary < 90% of floor → filter. If 90–99% of floor → score normally, add amber "salary near floor" tag. If no salary listed → score normally. |
| Q5 | Haiku pre-screen precision bar is acceptable as-is. | User's LinkedIn alerts are already filtered to senior+ roles; Haiku mainly catches wrong-function mismatches. Missing 1 in 10 is acceptable since uncapped roles remain in the dedup pool. |
| Q6 | `scorerole config` not re-added. | `scorerole init` and `.env` cover all configuration surfaces. No meaningful third category. |
| Q7 | Single-user only for v0.1. | No secondary persona. Senior Companion is a separate unrelated project. |
