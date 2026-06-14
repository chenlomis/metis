# scorerole — Product Spec

> Status: v0.1 draft — retroactively written from build history. Items marked
> `[?]` need owner input before they can be used as acceptance criteria.

---

## 1. Problem Statement

Job seekers receive dozens of job alert emails per week. Reviewing each posting
individually — reading the JD, comparing it to your background, deciding if it's
worth applying — takes 2–5 minutes per role. At 50 roles/week that's 2–4 hours of
low-signal scanning before any actual application work begins.

**scorerole** automates the screening step. It reads your job alert emails, scores
each role against a structured profile of your background and preferences, and
delivers a ranked digest that surfaces only the roles worth your time — with enough
context (match rationale, friction points) to make a quick apply/skip decision.

---

## 2. What It Is / Is Not

| ✅ In scope | ❌ Out of scope |
|---|---|
| Ingesting LinkedIn job alert emails via IMAP | Browsing or scraping job boards directly |
| Scoring roles against a user-defined profile | Auto-applying to jobs (HITL required for submission) |
| Delivering a ranked HTML digest by email | Tracking application status post-apply |
| Profile management via interactive CLI wizard | Multi-user / team / SaaS features |
| Configurable lookback window and score thresholds | Real-time / streaming alerts |
| Extensible source layer for future job feeds | `[?]` — specific other sources TBD |

---

## 3. User Persona

**Primary:** A mid-to-senior individual contributor actively job searching, comfortable
with terminal tools, who generates 20–200 LinkedIn job alert emails per week and finds
manual screening a time drain. Values precision over recall — a missed great role is
worse than a cluttered digest.

`[?]` Secondary personas (e.g., recruiters using it for candidate sourcing, teams
sharing an instance) are out of scope for v0.1 but the architecture should not
actively block them.

---

## 4. Core User Flows

### Flow 1 — First-time Setup

**Goal:** A new user goes from zero to receiving their first scored digest.

```
1. Clone repo + install dependencies  (pip install -e .)
2. Copy .env.example → .env; fill in:
     - ANTHROPIC_API_KEY
     - GMAIL_ADDRESS + GMAIL_APP_PASSWORD (App Password, not account password)
     - RECIPIENT_EMAIL
3. Run: scorerole init
     Step 1 — Provide resume (PDF, DOCX, or TXT path)
     Step 2 — Optionally provide LinkedIn export or supplementary bio
     Step 3 — Set preferences: work mode, salary floor, remote policy
     Step 4 — Claude extracts structured profile → user reviews → approves or edits
4. Run: scorerole
     → fetches LinkedIn alert emails from last 3 days
     → scores roles against profile
     → sends HTML digest to RECIPIENT_EMAIL
```

**Exit criteria:**
- [ ] User can complete setup without reading source code
- [ ] `scorerole init` completes without error given a valid PDF resume
- [ ] A digest email is received within 5 minutes of running `scorerole` for the first time
- [ ] Digest contains at least one role if LinkedIn alert emails exist in the lookback window
- [ ] Digest is readable on mobile Gmail (max 600px width, no broken layout)

**Key error paths:**
- Missing `.env` key → clear error message naming the missing variable and where to get it
- No LinkedIn emails in Gmail → "No emails in lookback window" message; no crash
- Resume file not found → prompt to re-enter path; no crash
- Claude API unreachable → error with suggestion to check key + network; exit code 1

---

### Flow 2 — Daily / Recurring Use

**Goal:** User receives a fresh digest each morning (manually or via cron).

```
1. (Optional) Cron/launchd runs: scorerole --lookback 1d
   OR user runs manually: scorerole
2. Pipeline fetches new alert emails since last lookback cutoff
3. Dedup filters roles already scored in the past 14 days
4. Scores remaining new roles (≤ MAX_JOBS_PER_RUN; default 20)
5. Sends digest
```

**Exit criteria:**
- [ ] Roles already seen in a previous digest do not reappear in subsequent runs
  within the 14-day TTL window
- [ ] Roles beyond the cap are NOT written to `seen_roles.json` — they reappear
  in the next run (regression gate for the role-burial bug)
- [ ] Running `scorerole` twice in a row with no new emails produces "No new roles"
  message, not a second empty digest
- [ ] Cron / non-interactive run never blocks on a prompt; caps silently and logs

---

### Flow 3 — Large Catch-Up Run

**Goal:** User hasn't run in several days or resets after a gap; wants to process
a backlog without silently missing roles or spending unexpectedly.

```
1. User runs: scorerole --lookback 14d
   OR: scorerole reset && scorerole --all --lookback 14d
2. Pipeline finds N > MAX_JOBS_PER_RUN new roles
3. Interactive prompt: "Found N roles. Score all? ~$X.XX estimated [y/N]"
4. y → Haiku pre-screen filters obvious mismatches
      → Sonnet scores survivors
      → Digest delivered
   N → Scores first MAX_JOBS_PER_RUN; remainder stay unseen (not buried)
```

**Exit criteria:**
- [ ] User sees role count + cost estimate before any API call is made
- [ ] Choosing `n` at the prompt scores exactly `MAX_JOBS_PER_RUN` roles; the rest
  remain available in future runs
- [ ] Choosing `y` scores more than `MAX_JOBS_PER_RUN` roles in a single digest
- [ ] `--all` flag bypasses the prompt entirely (for scripting / confidence)
- [ ] Non-TTY run (cron) never shows the prompt; caps and logs a warning

---

### Flow 4 — Profile Update

**Goal:** User's situation has changed (new job preference, different salary floor,
updated resume). They update their profile without losing previously scored data.

```
Option A — Quick edits (no re-extraction):
  scorerole init → "Quick edit" → review/edit menu
  → edit individual sections (strengths, deal breakers, etc.)

Option B — Preference refresh only:
  scorerole init → "Update prefs" → re-answer Step 3 only
  → applied on top of existing extracted profile

Option C — Direct YAML edit:
  scorerole init → "Open in editor"
  OR: open ~/.job_pipeline/profile.yaml in any editor

Option D — Full restart (new resume):
  scorerole init → "Start fresh"
  → full 4-step wizard; overwrites existing profile
```

**Exit criteria:**
- [ ] Profile changes take effect on the very next `scorerole` run
- [ ] Quick edit does not re-call Claude or consume API tokens
- [ ] "Start fresh" requires explicit choice; cannot be triggered accidentally
- [ ] After any profile update, `scorerole` uses the new profile (not a cached version)

---

### Flow 5 — Troubleshooting / Reset

**Goal:** User notices missing roles, stale state, or unexpected dedup behaviour.
They want to inspect what's happening and reset cleanly.

```
Inspect email parsing:
  scorerole debug → dumps most recent LinkedIn email body to
  ~/.job_pipeline/debug_email.txt; prints first 2000 chars to terminal

Clear dedup state (keep profile):
  scorerole reset → clears seen_roles.json
  → next run re-evaluates all roles in the lookback window

Nuclear reset (clear everything):
  scorerole reset --profile
  → clears seen_roles.json + profile.yaml
  → user must run scorerole init before next run
```

**Exit criteria:**
- [ ] `scorerole debug` produces output even if no jobs were found; never crashes
- [ ] `scorerole reset` prompts for confirmation before deleting anything
- [ ] After `scorerole reset`, a same-day `scorerole` run re-scores roles that were
  previously skipped due to the TTL gate
- [ ] `scorerole reset --profile` followed by `scorerole` (without init) prints a
  clear error pointing to `scorerole init`, not a stack trace

---

## 5. Profile Schema — What the Profile Captures

The profile (`~/.job_pipeline/profile.yaml`) is the central artifact. It informs
every scoring decision.

| Section | What it captures | Source |
|---|---|---|
| `candidate` | Name, current title, location, work mode, seniority | Resume + wizard |
| `target` | Desired roles, level, industries | Wizard Step 3 |
| `aspirations` | Career track (IC/mgmt), direction, company types to seek/avoid | Wizard Step 3 |
| `preferences` | Company stage, industry targets, base salary target | Wizard Step 3 |
| `scoring` | Apply/consider thresholds, level-mismatch deduction | Auto-set; editable |
| `experience` | Role-by-role history with highlights | Extracted from resume |
| `education` | Degrees, institutions | Extracted from resume |
| `strengths` | 3–6 differentiating capabilities | Extracted + user-editable |
| `green_flags` | Things that boost a role's score | User-defined |
| `yellow_flags` | Cautions worth noting but not disqualifying | User-defined |
| `red_flags` | Soft negatives | User-defined |
| `deal_breakers` | Hard disqualifiers (auto-filters or heavy score penalty) | User-defined |
| `salary_floor_usd` | Minimum acceptable base salary | Wizard Step 3 |
| `notes` | Free-text context sent verbatim to Claude | User-defined |

**Exit criteria for profile quality:**
- [ ] A profile extracted from a real resume produces a scoring prompt that a human
  would recognize as an accurate summary of that person's background
- [ ] Missing optional sections do not crash scoring; they are silently omitted
- [ ] A profile with only `candidate` + `experience` still produces meaningful scores
  (graceful degradation)

---

## 6. Digest Output — What "Good" Looks Like

The digest is the primary user-facing output. It is an HTML email.

**Structure:**
```
Header: "Personalized Job Alert Digest — [date]"
Stat row: [N roles evaluated] [N apply] [N consider]
Legend: green=strength match, amber=caution, red=concern

Section: Apply   (score ≥ 75)
  [Job card × N]
    Title + score pill
    Company · Location
    ↑ Leverage: [1–2 match points, conclusion-first format]
    ↓ Friction: [0–1 honest concern; empty if none]
    Tags: up to 4 colour-coded highlight tags
    [View posting →]

Section: Consider   (score 55–74)
  [Job card × N]

Section: Skipped   (score < 55)
  [2-column compact grid, title + top friction tag only]

Footer: "scorerole · powered by Claude · N roles evaluated"
```

**Exit criteria for digest quality:**
- [ ] Every role in "Apply" has score ≥ 75; every role in "Consider" has score 55–74
  (verdict drift from Claude is corrected before rendering)
- [ ] Roles violating a deal_breaker do not appear in Apply, Consider, or Skipped sections
- [ ] Roles filtered by deal_breaker or salary_floor appear only as a footer count
  ("X roles filtered — deal_breaker or salary mismatch")
- [ ] Leverage points follow conclusion-first format: `"topic — evidence clause"`;
  no "JD needs X → candidate has Y" phrasing
- [ ] Friction is either a real concern or an empty array; never placeholder text
  ("none", "n/a", "no concerns")
- [ ] Digest renders correctly at 600px width (no overflow, no broken table layout)
- [ ] "View posting →" links resolve to the correct LinkedIn job page
- [ ] Digest is delivered within `[?]` minutes of running `scorerole`

---

## 7. Configurability

Users can tune scorerole's behaviour without touching code.

| What | How | Default | Notes |
|---|---|---|---|
| How far back to fetch emails | `--lookback 3d` or `DEFAULT_LOOKBACK` in `.env` | `3d` | Also accepts `7d`, `2026-05-10`, `yesterday` |
| Max roles per run | `MAX_JOBS_PER_RUN` in `.env` | `20` | `0` = no cap |
| Score everything (bypass cap) | `--all` flag | off | Triggers Haiku pre-screen |
| Apply threshold | `profile.yaml → scoring.apply_threshold` | `75` | Roles at or above this score → "Apply" |
| Consider threshold | `profile.yaml → scoring.consider_threshold` | `55` | Roles between this and apply → "Consider" |
| Level-mismatch penalty | `profile.yaml → scoring.level_mismatch_deduction` | `10` | Deducted when title lacks seniority signal |
| Scoring model | `MODEL` in `.env` | `claude-sonnet-4-6` | Full scoring |
| Pre-screen model | `PRESCREEN_MODEL` in `.env` | `claude-haiku-4-5` | Haiku filter pass |

`[?]` Future: per-criterion weighting (e.g., "weight remote policy 2x").
`[?]` Future: minimum salary filter applied before scoring (not just as a score signal).

---

## 8. Non-Functional Requirements

| Requirement | Target | Notes |
|---|---|---|
| Setup time (new user) | < 15 minutes | From clone to first digest |
| Run time (20 roles) | < 3 minutes (target 60–90s) | Dominated by JD enrichment (HTTP) + Sonnet call. >10 min = investigate. |
| API cost (20-role run) | < $0.30 | ~$0.05–0.15 per 10 roles with claude-sonnet-4-6 |
| API cost (catch-up, 100 roles with pre-screen) | < $1.50 | Haiku pre-screen cuts ~50% before Sonnet |
| Secrets | Never committed | `.env` always in `.gitignore`; profile in `~/.job_pipeline/` |
| Failure mode | Loud + recoverable | SMTP failure exits code 1; parse failures fall back gracefully |
| Test coverage | Core logic only | Email parsing, dedup, ranking, JSON recovery, profile rendering |

---

## 9. Open Questions — Resolved

| # | Question | Decision |
|---|---|---|
| Q1 | Acceptable run latency? | 60–90s is fine for 20 roles. >10 min = investigate. No optimization needed now. |
| Q2 | deal_breakers: filter or penalise? | **Hard filter.** Roles violating a deal_breaker must not appear in the digest. Claude assigns `verdict="filtered"` when a deal_breaker is clearly violated; filtered roles are excluded before rendering. Future: configurable penalty weight for users who prefer soft filtering. |
| Q3 | Scope of extensible sources? | **Tier A (email-based, same IMAP approach):** Indeed, Glassdoor alerts. **Tier B (HTTP scraping / RSS):** VC portfolio boards (a16z Jobs, etc.), Greenhouse RSS. Tier A is a near-term extension; Tier B is a separate engineering track. |
| Q4 | salary_floor_usd: hard gate or soft signal? | **Hard gate with negotiation buffer.** If JD states a salary clearly below `salary_floor_usd` (< 90% of floor), filter the role. If salary is within 10% below floor, score normally but add an amber "salary near floor" tag. If JD doesn't mention salary, proceed to scoring. |
| Q5 | Pre-screen precision bar? | Acceptable. User's LinkedIn alerts are already filtered to senior+ roles, so Haiku's main job is catching wrong-function mismatches. Missing 1 in 10 is acceptable given the fallback (uncapped `seen_roles` keeps unscored roles available). |
| Q6 | Re-add `scorerole config`? | **No.** `init` covers all user-facing configuration; `.env` covers secrets/runtime tuning. No meaningful third category exists. Companion was correct to remove it. |
| Q7 | Second persona? | **No.** Senior Companion is a separate project with no intended overlap. scorerole is single-user only for v0.1. |
