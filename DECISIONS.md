# metis — Decisions Log

Concise record of non-obvious choices. The "why" that doesn't belong in code comments.
See ARCHITECTURE.md for deep technical detail.

---

## Profile & schema

**D-01 · profile.yaml lives outside the repo (`~/.job_pipeline/`)**
Personal data (resume text, salary, location) must never be committed. The repo ships `profile.template.yaml` and `examples/profile_*.yaml` with fake personas. `mode=0600` enforced on write.

**D-02 · Keep user config local; split files only when the boundary is real**
Target mental model: `profile.yaml` (candidate + preferences), `email_sources.yaml` (extra alert senders), `config.yaml` (thresholds/schedule), and `feedback.md` (calibration). Most config still lives in `profile.yaml` for v1 simplicity, but code should treat those concerns separately. Split when there's a concrete reason; MCP/config-as-parameters is the likely trigger.

**D-03 · `experience`, `education`, `strengths` nest under `candidate`**
These are resume-derived facts about the person, not search preferences. Nesting them under `candidate` makes the boundary explicit: `candidate.*` = "who you are", everything else = "what you want". Decided June 2026 during the profile schema simplification.

**D-04 · `deal_breakers` and `salary_floor_usd` are top-level**
They are search-criteria, not identity. Keeping them top-level (not under `preferences`) signals their weight: hard gates, not soft signals. `preferences.base_salary_target_usd` is the aspirational number; `salary_floor_usd` is the filter.

**D-05 · Removed `scoring` block, `green_flags`, `yellow_flags`, `red_flags` from user-facing schema**
`scoring` thresholds (apply_threshold, consider_threshold) are system knobs, not candidate preferences — they belong in `config.yaml` eventually. `green/yellow/red_flags` were scoring-internal tags Claude emits; exposing them in the profile created confusion about whether the user or Claude owned them. Removed in June 2026 simplification.

---

## Onboarding (`metis init`)

**D-06 · `metis init` is the canonical setup wizard**
The current flow (resume import → LLM extraction → preference/deal-breaker questions → review + save) proved lower-friction than the older structured form. As of June 2026, `metis init` is the only public onboarding command. Legacy setup code may remain for reference, but it should not appear in public help or docs.

**D-07 · The init edit menu mirrors wizard steps, not profile fields**
The review menu offers "Step 2 — What you're looking for" and "Step 3 — What you'd pass on" rather than "Roles + level", "Salary floor", etc. Users entered data through steps, so editing should mirror that mental model. Field-level edits available via "Open profile in editor" for power users.

**D-08 · "Re-run extraction" only appears when resume context is available**
In quick-edit mode (existing profile), the wizard has no resume in memory. Showing "Re-run extraction" would be a broken promise (API key present but no resume to re-extract from). The option is conditionally hidden rather than shown-but-disabled to avoid confusion.

**D-09 · Init uses single-line text for Steps 2–3, not multiline textarea**
`inquirer.text(multiline=True)` without `vi_mode=True` submits on `Meta+Enter` (not `Enter`), causing apparent hangs. With `vi_mode=True`, users must press `Esc` then `Enter` — non-obvious and breaks on terminal resize. Single-line with `Enter` to submit is more reliable and sufficient; Claude extracts rich signal from one dense sentence.

**D-10 · `_followups` returned as top-level key in the same YAML block**
Returning two separate YAML blocks from Claude is fragile (parsing boundary is ambiguous). Single YAML with `_followups: []` as a top-level key is unambiguous. Claude is instructed to never include `_followups` in the profile output — it's stripped before saving.

**D-11 · Clarification question *option text* is owned by Python, not Claude**
Controlled vocabulary (`kind` field) lets Claude decide *whether* to ask; Python hard-codes the option labels. Prevents Claude from inventing option text that doesn't map to actionable profile updates.

---

## Scoring pipeline

**D-12 · Three-pass scoring: Haiku pre-screen → Haiku Layer 1 extract → Sonnet Layer 2 score**
Pre-screen (pass 1) activates only when role count exceeds `MAX_JOBS_PER_RUN` — it's a cost gate, not always-on. Layer 1 (pass 2) always runs; extracts 27 structured fields at temperature=0. Layer 2 (pass 3) only runs on roles that passed hard gates. Two hard gates run between passes 2 and 3: `jd_blank` and `salary_floor`. Total cost per typical 10-job run: ~$0.02–0.05.

**D-13 · Verdict re-derived in `rank_jobs()` from score + profile thresholds**
The scoring model is given score thresholds in the prompt but doesn't guarantee compliance. `rank_jobs()` re-derives verdict from the numeric score. This prevents a score-62 role from being surfaced as "apply" due to model drift.

**D-14 · `seen_roles.json` uses `md5(title+company)[:12]` as key, not email Message-ID**
Same job can appear in multiple LinkedIn alert emails. Deduping on Message-ID would miss cross-email duplicates. Title+company key survives across email campaigns. 30-day TTL means a role that's still open after a month reappears — intended behavior for a passive job seeker who may want to reconsider. MD5 is intentional: this is a dedup key, not a security primitive. `[:12]` = 48 bits of entropy; collision probability is negligible at personal-use scale.

**D-15 · Only scored roles are written to `seen_roles.json`**
Capped/pre-screened-out roles remain unseen. They reappear in future runs and get another chance once the cap clears. This is intentional: don't permanently suppress roles just because a single run was expensive.

**D-16 · `save_seen_roles()` called after `send_digest()`, not before**
If SMTP fails, the roles aren't marked seen. They'll be re-scored on the next run. Acceptable: re-scoring costs a few cents; losing a digest delivery is worse than paying twice. See T-07 in ARCHITECTURE.md.

---

## Sources

**D-17 · Proactive source decoupled from LinkedIn pipeline via `source: "proactive"` tag**
Proactive jobs (Greenhouse/Lever API) arrive with JD pre-fetched. `enrich_jobs()` skips them to avoid redundant HTTP fetches. Scoring, ranking, and rendering layers are unchanged — they only see the job dict, not the source.

**D-18 · Company list (companies.yml) is role-agnostic; title/location filtering derived from profile**
The company list is a curated set of employers worth watching. Which roles to surface is derived from `profile.target.roles` (title patterns) and `profile.candidate.location` (country-level filter). This means the same companies.yml works for any job function — PM, SWE, design, etc.

**D-48 · No tier system — all companies in companies.yml are active by default (June 2026)**
The original S/A/B tier annotation limited runs to 15 of 50 companies. Tiers were removed: all companies in companies.yml are active unless explicitly excluded via `proactive_sources.exclude_companies` in `profile.yaml`. This gives full coverage across all 53 companies (46 Greenhouse, 6 Ashby, 4 Playwright) on every run. The tier field no longer exists in companies.yml or in profile.proactive_sources. Individual companies can still be excluded via `metis sources remove`.

**D-49 · Title filter strips level prefix for broader recall (June 2026)**
`_build_title_patterns()` in `proactive.py` generates two tiers: level-qualified patterns (e.g. "staff product manager") and base-role patterns without the level prefix (e.g. "product manager"). This ensures companies that don't level-prefix their titles (Anthropic, OpenAI, GitHub, etc.) still surface matching roles. The level-fit evaluation happens downstream in scoring, not at the title-filter stage.

**D-50 · playwright_companies section for proprietary-ATS companies (June 2026)**
Companies that don't expose a public Greenhouse/Lever/Ashby API are scraped via Playwright headless Chromium. They live in a `playwright_companies:` block in companies.yml with a `careers_url` field (the job listings or search page). Initial entries: Atlassian, Apple, Netflix, Google. The same title and location filters apply; location is extracted from the JD body by the scoring layer since Playwright scrapes don't return structured location fields.

---

## Interface & extensibility

**D-55 · OAuth-first email access uses local tokens, active-provider state, state validation, and PKCE**
Gmail and Outlook OAuth are local desktop/browser flows, not hosted web sessions. Tokens are
stored under `~/.job_pipeline/` with `0600` permissions and never belong in the repo. The latest
successful OAuth connection writes `email_provider.json`; `METIS_EMAIL_PROVIDER` remains an
explicit override; newest-token fallback exists only for old dual-token states.

Security baseline: every auth URL includes random `state` and PKCE `code_challenge` (`S256`);
callbacks must reject missing/mismatched `state`; token exchange must include the matching
`code_verifier`. This follows Microsoft auth-code guidance for desktop/mobile apps and
Google installed-app guidance. Do not remove these to simplify tests.

Reconnect flows force account selection so users can replace one Gmail/Outlook account with
another. Current implementation scope is provider-neutral non-LinkedIn email alert fetching;
legacy Gmail paths remain in LinkedIn alert fetching, tracker/backfill, and main digest
delivery until the email-provider abstraction is wired through those surfaces.

**D-19 · Interface roadmap: CLI → MCP → PyPI package → Docker → Web app**
Each stage gates on the previous. MCP server is next and requires a "config as parameters" refactor so core functions don't read from `.env` at import time. Web app is not planned speculatively — only if OSS adoption demonstrates demand.

**D-20 · No auto-calibration; all feedback is explicit and user-initiated**
Score drift signals (applied to roles scored "skip") are surfaced as digest footer nudges, not auto-adjustments. User decides whether to act via `metis init → Quick edits` or `metis feedback`. Rationale: auto-calibration on thin behavioral signals produces confident wrong answers.

**D-27 · Single shared identity in `prompts.py`; no hardcoded candidate names (OSS-safe)**
All LLM calls share a common identity anchored in the headhunter framing: metis is the advisor, the candidate is the client, `profile.yaml` is the client brief. `prompts.py` is the single source of truth for identity templates and system prompt assembly — same principle as `theme.py` for colors. `SCORING_IDENTITY` and `FEEDBACK_IDENTITY` contain `{candidate_name}` format slots; no literal names are hardcoded. `build_candidate_context(profile)` synthesizes a terse brief from the profile dict that orients Claude before it reads the full `render_profile` detail. This separation (brief → detail) prevents Claude from pattern-matching keywords before it has a coherent perspective on the candidate.

**D-28 · Identity sits above profile in the system prompt; feedback parser uses system/user split**
Scoring system prompt order: identity → candidate brief → full rendered profile → feedback → bullet rules → rubric. The identity anchors Claude's perspective before it reads data. For feedback parsing (`_claude_process`), grounding rules go in the `system=` parameter and the analysis format goes in the user turn — this prevents the analysis task from overriding the identity constraints.

**D-23 · `metis feedback` flow: collect → Haiku parse → confirm → save; no auto-weight changes**
Free-form text input (blank line to finish). Haiku parses the text into structured metadata (roles, dims, conflicts, profile-level items). Conflicts require user resolution before saving. Confirmed entries are appended to `feedback.md` with an HTML comment header (`<!-- id:... | run:... | roles:... | dims:... -->`) for traceability. A separate `feedback_log.jsonl` records audit metadata (never injected into prompts). Consistent with D-20: no weights are modified automatically.

**D-24 · `feedback.md` has no TTL — all entries injected**
Feedback represents calibrated user intent, not time-bounded state. Unlike `seen_roles.json` (30-day TTL), there is no expiry. If the file grows impractically large in the future, add a summarisation step inside `load_feedback_text()` before the content is returned to `score.py`. Do not add TTL logic elsewhere.

**D-25 · Feedback conflicts: only flag real contradictions with existing text**
`_claude_process()` is instructed to populate `conflicts[]` only when an actual quoted statement in the existing `feedback.md` directly contradicts the new feedback. Empty prior feedback → empty conflicts list. This prevents hallucinated conflicts against non-existent prior state (observed bug: Claude flagging "conflict" when existing feedback was empty).

**D-26 · `profile_items` detection: only explicit blanket rules, not inferred preferences**
The Haiku processing step may detect feedback that belongs permanently in `profile.yaml` (e.g. "I never want B2C roles") and route it to `metis init → Quick edits`. It must only flag statements the user *explicitly wrote* as a blanket rule. Inferring preferences from company names or role titles (e.g. "Workday HCM is foreign") is prohibited — this caused spurious profile flags on first-run feedback.

---

## UI / theme

**D-21 · `theme.py` is the single source of truth for all colors**
No color hex values anywhere else in the codebase. Rich `style=` parameters must use `Style(color=THEME["key"])` objects — not f-string `"color:#hex"` (Rich's string parser rejects that syntax). `INQUIRER_STYLE` must be created via `get_style()` not a plain dict.

**D-22 · Editor label in init is dynamically resolved at runtime**
`open_in_editor()` respects `$VISUAL`/`$EDITOR`, then tries `code` → `cursor` → `zed` → `nano`/`vi`. The menu item reflects what will actually open: "Open profile in VS Code" vs "Open profile in your default editor". Avoids surprising users who expect their configured editor.

---

## Traceability & observability

**D-32 · `trace.py` writes append-only JSONL per job, every run**
`~/.job_pipeline/runs.jsonl` gets one record per job regardless of verdict (prescreened, filtered, or scored). This is the raw material for `metis summary`. Decision: write at scoring time, not at digest delivery time — traceability shouldn't depend on SMTP succeeding.

**D-33 · Scoring traceability shown in digest, not a separate command (intent)**
The email digest should include score breakdowns (dimension scores, leverage/friction points, gate reason if filtered). The raw data is in `runs.jsonl` and the eval dict already carries it. `metis summary` reads `runs.jsonl` for trends; the digest surfaces per-role traceability inline.

---

## Email parsing

**D-34 · LLM fallback for email parsing: LLM first, regex safety net (planned)**
Current flow: regex primary, fails silently on novel LinkedIn email formats. Planned: try LLM extraction (Haiku) first, fall back to regex if LLM returns malformed JSON or zero results. Module should be an optional import with try/except so the core works without an API key. Same pattern as the careerops.py integration.

---

## Reporting

**D-35 · `metis summary` reads `runs.jsonl` — no separate DB needed**
Score distribution, apply rate, and score-vs-interest correlation can all be computed from `runs.jsonl` + `applications.xlsx` join on `role_hash`. No separate analytics DB. Report generates in-terminal summary + optional HTML export.

---

## Library / MCP design

**D-36 · Config-as-parameters before MCP or PyPI**
Several modules call `os.getenv()` at import time (score.py, extract.py, linkedin.py). A library must not do this — it hijacks the caller's environment. Fix: pass `api_key=`, `model=`, `profile_path=` explicitly into core functions. The CLI wrapper reads from `.env` and passes through. This is a prerequisite for both the MCP server (different working dir) and PyPI publish (clean importable API).

**D-37 · MCP surface: 5 tools, wrapping existing functions**
`score_jobs`, `get_last_digest`, `check_tracker`, `run_track`, `get_profile`. Thin wrapper — no rewrite. Prerequisite: D-36 + stable core functions (finish `metis summary` and feedback loop first so the MCP surface doesn't change signatures mid-flight).

**D-29 · Employer-lens scoring: deferred**
Rejection patterns (applied to roles → got rejected) could indicate a mismatch between self-assessment and employer assessment. Adding an employer lens to scoring is a later concern — it requires enough track data to be meaningful and risks producing confident wrong signals on thin data. Address after `metis summary` surfaces the pattern clearly.

---

## System diagram & documentation

**D-51 · System overview diagram uses a 5-color visual language (June 2026)**
The Mermaid flowchart in `ARCHITECTURE.md § 1. System Overview` uses a consistent semantic color system so component roles are scannable without reading labels:
- **Blue** = user-provided data (resume, profile answers, feedback text the user types)
- **Purple** = external sources being fetched from (LinkedIn alerts, career site APIs)
- **Amber + ✦** = AI processing nodes (AI Scorer, `metis feedback` which calls Haiku)
- **Gray** = file artifacts (profile.yaml, seen_roles.json, runs.jsonl, etc.)
- **Green** = deliverables/outputs that reach the user (email digest, summary report)
- **Commands** = monospace `[metis ...]` text labels on arrows, NOT boxes — commands are verbs (actions), not nouns (components), so they annotate the edge that performs them.

`metis summary` does NOT get the ✦ marker — it reads files and aggregates stats but makes no AI calls.

**D-52 · System overview diagram abstracts away model names; keeps 5-zone structure**
The overview uses "AI Scorer" as the unit, not "Haiku" / "Sonnet". The two-pass architecture (Haiku extract → Sonnet score) is detailed in `§ 3. Scoring Pipeline (deep dive)`. Exposing model names in the overview would couple the diagram to the current model selection, which may change without changing the architecture.

The five zones (SETUP / INPUTS / PIPELINE / OUTPUTS / USER LOOP) map 1:1 to the five `### N.` subsections that follow the diagram, giving readers a clear navigation path from overview to detail.

**D-53 · Artifact map is the canonical reference for file read/write relationships**
`ARCHITECTURE.md § Data Files` contains a "What writes what" table mapping each runtime file to its writer and reader. Key non-obvious entries:
- `seen_roles.json` is bidirectional with AI Scorer: read first (dedup gate), written after scoring
- `runs.jsonl` is the data source for `metis summary` (NOT `applications.xlsx`)
- `last_run.json` is written by AI Scorer and read by `metis feedback` (for run context display)
- `feedback_log.jsonl` is written by `metis feedback` but never read back (audit trail only)
- Capped roles (pre-scoring cap, staged to `role_queue.json`) are NOT written to `seen_roles.json` — they reappear next run
- Hard-gate filtered roles (`jd_blank`, `salary_floor`, deal-breakers) ARE written to `seen_roles.json` (same as scored roles) — they will NOT reappear unless manually removed

**D-54 · `jd_quality: "extraction_failed"` must NOT trigger the `jd_blank` hard gate**
`check_hard_gates()` in `extract.py` fires `jd_blank` only when `jd_quality == "blank"`. There are three distinct values:
- `"blank"` — Haiku received empty JD text; truly no content to score. Gate fires → `verdict="filtered"`.
- `"low"` — Haiku received JD text but rated it low quality (e.g., stub or duplicate boilerplate). Gate does not fire; Sonnet scores with caution.
- `"extraction_failed"` — Haiku received a full JD but its output failed JSON parse. Gate must NOT fire — the JD content exists and Sonnet can score directly from the raw text without extraction grounding.

Root cause this fixed (June 28): `_extract_chunk()` fell back to `dict(_BLANK_STRUCT)` on JSON parse errors. `_BLANK_STRUCT` has `jd_quality: "blank"` hardcoded → gate fired even for 15K-char JDs. Fix: fallback now returns `{**_BLANK_STRUCT, "jd_quality": "extraction_failed"}`.
Enforced by: `TestHardGates` in `tests/test_core.py`.

---

## Cross-platform

**D-30 · Scheduling: launchd (macOS) + cron (Linux) implemented; Windows deferred**
`schedule_cmd.py` handles macOS (launchd plist) and Linux (crontab). Windows Task Scheduler is unsupported — not planned unless there's demand. `setup_cron.sh` is a bash fallback for users who skip the wizard. Cross-platform install (one-command setup) is a backlog item.

---

## Feedback integration

**D-31 · Feedback injected as a distinct high-priority section in scoring prompt**
`feedback.md` content is not blended into the profile — it's injected as a separate `USER CALIBRATION FROM PRIOR RUNS:` section with high priority weight. This ensures Claude treats it as override signal, not background context. Example entries: "User skipped several seed-stage roles; treat seed-stage as soft negative unless explicitly AI infra."

---

## Reliability & regression prevention (June 2026)

**D-38 · Email digest format locked via CLAUDE.md constraint + test_render_format.py**
After silent format regressions (legend labels, stat tile label, score breakdown visibility) caused by agents making unsolicited "improvements" to `render.py`, the canonical format was locked at two layers: (1) CLAUDE.md constraint #0 names specific strings that must not change without explicit request, and (2) `tests/test_render_format.py` asserts 18 output-level facts against `build_digest_html()`. CLAUDE.md prevents changes proactively; the test catches any that slip through. Both layers are required — instructions alone are not enforced across sessions.

**D-39 · Tracker input validation gate: `_is_plausible_job_row()` before xlsx write**
LinkedIn's positional parser (`before_lines[-3/-2/-1]`) can shift when trailing lines vary, writing company-name-as-title or location-as-company into the spreadsheet. `_is_plausible_job_row(title, company)` in `xlsx.py` blocks writes where the title lacks job-role keywords or the company field looks like a bare location string. Uses anchored regex to avoid false positives ("San Francisco Health" passes; "San Francisco" alone is blocked).

**D-40 · LinkedIn positional parsing: 3-case runtime shift detection in `extract_jobs()`**
When `before_lines[-2]` (expected company slot) matches `_LOCATION_LIKE`, the parser shifts all three fields up one position (Case A). When `before_lines[-3]` (expected title slot) contains a company-name suffix (Inc/Corp/Ltd) without job-role keywords, it also shifts (Cases B and C). This runtime correction is per-email and never mutates the source data. Both D-39 and D-40 are needed: D-40 fixes parsing at source; D-39 catches any that slip through at the write layer.

**D-41 · launchd retry: `KeepAlive.SuccessfulExit=false` + `ThrottleInterval=900`**
Scheduled runs sometimes fire at Mac wake-from-sleep before DNS resolves, causing `[Errno 8] nodename nor servname provided`. Code-level retry (3 attempts, 30s backoff) handles transient DNS. launchd retry (`ThrottleInterval=900`) handles the case where all 3 code retries fail — launchd restarts the job 15 minutes later, at which point network is reliably available. `SuccessfulExit=false` tells launchd to retry only on non-zero exit, so a clean run is never re-triggered.

**D-42 · score.py ↔ render.py eval schema treated as a coupled contract**
The eval dict shape that `score.py` emits is consumed directly by `render.py`: verdict enum, 6 named dimensions for scored roles, exactly 2 leveragePoints, 0-1 frictionPoints for apply/consider, and one frictionPoints skip reason for skipped roles. Deal-breaker filtered roles use score 0 with empty dimensions/leverage/friction and are split out before rendering. The shared validator lives in `metis/contracts.py`, the type hints live in `metis/types.py`, and `tests/test_score_render_contract.py` locks the boundary. Changing either file's expectations without updating the other causes silent rendering errors or missing data. Documented in CLAUDE.md constraint #5. OSS users who customize the schema must change both files in the same edit.

**D-43 · `_role_hash()` implementation frozen; `_HEADERS` column order frozen**
`_role_hash()` in `state.py` produces keys persisted in `seen_roles.json`. Changing the hash function (normalization, algorithm, slice length) invalidates all historical dedup state — every previously seen role re-processes, causing a flood email. Similarly, `_HEADERS` in `xlsx.py` defines the xlsx column layout. Existing `applications.xlsx` files contain months of data; reordering or inserting columns corrupts existing rows. Column *header text* is safe to rename; column *order* is not. Both frozen in CLAUDE.md constraints #6 and #7.

---

## Interface & distribution (June 2026)

**D-44 · Target persona is passive job seekers, not active ones**
Passive seekers (biweekly/weekly cadence, selective, won't mass-apply) are the better fit for scoring-first design. Active seekers want volume tools; they churn from anything that adds friction before the application. Passive seekers value signal quality over throughput — metis's 2-layer AI pipeline is the right investment for that persona.

**D-45 · Interface roadmap: CLI → MCP server → PyPI → Docker → web app (on demand only)**
Stage 0 (done): local CLI. Stage 1 (next): MCP server — local subprocess, no hosting, Claude Code users can `claude mcp add metis`. Stage 2: PyPI package after stable public API. Stage 3: Docker for users who skip the venv setup. Stage 4: web app only if demonstrated demand from non-technical users. Prereq for Stage 1: config-as-parameters refactor (no `os.getenv()` at module import time).

**D-46 · prompts.py as canonical, OSS-safe identity layer**
All LLM prompt templates live in `prompts.py`. Candidate name and profile are injected dynamically — no personal details hardcoded. This makes the repo safe to publish as OSS without scrubbing. Call sites (`score.py`, `feedback.py`) import from here; no duplicate prompt strings anywhere in the codebase.

**D-47 · Scoring voice: second-person ("You/Your"), past tense for candidate actions, present for JD requirements**
Bullet guide rule added to `score.py`: "You led" (past, candidate action) vs. "The role requires" (present, JD requirement). Third-person "{first_name}" removed — second-person reads as direct coaching, not a report about the candidate. This is a prompt constraint, not a post-processing step, so Claude enforces it at generation time.

---

## Naming

**D-48 · Package and CLI renamed from `scorerole` to `metis` (June 2026)**
The original name `scorerole` was functional but forgettable and described only one feature (scoring roles). `Metis` is the Greek Titaness of wisdom and counsel — fitting for a tool that acts as a discerning advisor on job fit rather than a mechanical scorer. All references updated: Python package (`metis/`), CLI entry point (`metis`), pyproject.toml, tests, and documentation. The launchd plist identifier is `com.metis.digest`. Schedule install/remove also cleans up the legacy `com.scorerole.digest` LaunchAgent and `# scorerole-digest` cron marker so old installs do not send duplicate digests. If you encounter the name `scorerole` anywhere else in the codebase it is a bug — update it to `metis`.

**D-49 · Title filter strips level prefix for broader recall (June 2026)**
`_build_title_patterns()` in `proactive.py` generates two tiers per target role: level-qualified patterns (e.g. "staff product manager") and base-role patterns without the level prefix (e.g. "product manager"). This ensures companies that don't level-prefix their titles (Anthropic, OpenAI, GitHub, etc.) still surface matching roles. The level-fit evaluation happens downstream in scoring, not at the title-filter stage.

**D-50 · playwright_companies section for proprietary-ATS companies (June 2026)**
Companies that don't expose a public Greenhouse/Lever/Ashby API are scraped via Playwright headless Chromium. They live in a `playwright_companies:` block in `companies.yml` with a `careers_url` field. The same title and location filters apply; location is extracted from the JD body by the scoring layer. Initial entries: Atlassian, Apple, Netflix, Google.

**D-51 · System overview diagram uses a 5-color visual language (June 2026)**
`ARCHITECTURE.md § System Overview` Mermaid diagram uses colors semantically, not decoratively:
- Blue (`#dbeafe`/`#1d4ed8`) — user-provided data (profile, resume)
- Purple (`#e9d5ff`/`#7e22ce`) — external sources (LinkedIn, career pages)
- Amber+✦ (`#fef3c7`/`#92400e`) — AI-driven steps (fast extraction, full scoring)
- Gray — runtime artifacts (files written/read during a run)
- Green (`#dcfce7`/`#166534`) — output deliverables (digest email, xlsx, runs.jsonl)
Arrow labels (→ with short verbs) are the command names — not boxed nodes.

**D-52 · System overview diagram abstracts away model names; 5-zone structure maps to 5 doc sections**
The overview uses "AI Scorer" not "Haiku/Sonnet" — model names change, the two-layer extraction+scoring architecture doesn't. The five diagram zones (SETUP / INPUTS / PIPELINE / OUTPUTS / USER LOOP) map 1:1 to the five `### N.` subsections that follow, giving readers a navigation path from overview to detail.

**D-53 · Artifact map is the canonical reference for file read/write relationships**
`ARCHITECTURE.md § Data Files` contains a "What writes what" table mapping each runtime file to its writer and reader. Key non-obvious entries:
- `seen_roles.json` is bidirectional with AI Scorer: read first (dedup gate), written after scoring
- `runs.jsonl` is the data source for `metis summary` (NOT `applications.xlsx`)
- `last_run.json` is written by AI Scorer and read by `metis feedback` (for run context display)
- `feedback_log.jsonl` is written by `metis feedback` but never read back (audit trail only)
- Capped roles (pre-scoring cap, staged to `role_queue.json`) are NOT written to `seen_roles.json` — they reappear next run
- Hard-gate filtered roles (`jd_blank`, `salary_floor`, deal-breakers) ARE written to `seen_roles.json` — they will NOT reappear unless manually removed

**D-54 · `jd_quality: "extraction_failed"` must NOT trigger the `jd_blank` hard gate**
`check_hard_gates()` in `extract.py` fires `jd_blank` only when `jd_quality == "blank"`. Three distinct values:
- `"blank"` — Haiku received empty JD text; gate fires → `verdict="filtered"`.
- `"low"` — JD present but rated low quality. Gate does not fire; Sonnet scores with caution.
- `"extraction_failed"` — JD present but Haiku output failed JSON parse. Gate must NOT fire — content exists and Sonnet scores from raw text without extraction grounding.

Root cause (June 28): `_extract_chunk()` fell back to `dict(_BLANK_STRUCT)` on JSON parse errors. `_BLANK_STRUCT` has `jd_quality: "blank"` → gate fired on 15K-char JDs. Fix: all three fallback sites in `extract.py` now return `{**_BLANK_STRUCT, "jd_quality": "extraction_failed"}`.
Enforced by: `TestHardGates` in `tests/test_core.py`.

**D-55 · PyPI package name is `metis-job`, not `metis`**
`metis` on PyPI is taken by a METIS ctypes wrapper (unrelated). Package name is `metis-job`; the CLI command and Python import path remain `metis` throughout. Users install with `pipx install git+.../metis.git` and run `metis`. The discrepancy (install name ≠ import name) is intentional and documented in README quickstart.

**D-56 · `.env` uses a fallback chain; `~/.job_pipeline/.env` is the canonical installed path**
`pipeline.py` tries `.env` candidates in order: (1) `<project_root>/.env` (dev: works in a clone), (2) `~/.job_pipeline/.env` (installed via pipx; no project root exists). `load_dotenv()` stops at the first file that exists. This means contributors get project-root behaviour automatically; pipx users put credentials in `~/.job_pipeline/.env` and never touch the package directory.

**D-58 · `_canonical_company()` normalizes company names before hashing, not inside `_role_hash()`**
`_role_hash()` is frozen (D-43) — changing it invalidates all historical seen_roles keys. Company name variants ("NVIDIA" vs "NVIDIA AI", "Stripe" vs "Stripe Inc.") produce different hashes under the raw function. Fix: `_canonical_company()` in `state.py` strips trailing branding/legal suffixes (AI, Labs, Technologies, Inc, Corp, Ltd, etc.) via regex before the hash is computed. `_role_hash()` itself is untouched. Iterative stripping handles stacked suffixes ("Acme Labs Inc." → "Acme Labs" → "Acme"). Applied everywhere `_role_hash()` is called.

**D-59 · `domain_background` dimension scores "foreign" only for niche domain-gatekeeping, not cross-industry presence**
Original prompt allowed Claude to score "foreign" (10) whenever a role was outside the candidate's stated `industry_targets`. This penalized legitimate AI/ML roles at companies in tangential industries (fintech, health-tech) even when `industry_avoid` was empty and the role required no domain-specific expertise. Rule: "foreign" applies only when the JD explicitly requires regulatory, compliance, or sector-specific expertise the candidate lacks. An empty `industry_avoid` means no industry is a default penalty. Candidate's AI/ML, B2B SaaS, and developer-tools strengths are noted as broadly transferable.

**D-60 · Digest tier labels are display aliases; internal verdict strings are unchanged**
Email digest sections show "Solid Match / Moderate Match / Limited Match" (in `TierSection.tsx`). Internal verdict strings remain `"apply" / "consider" / "skipped"` throughout `score.py`, `pipeline.py`, `state.py`, and `xlsx.py`. The TSX template maps verdicts to display labels at render time. This separation means: (1) the score contract is stable, (2) labels can be changed without touching business logic, (3) tests assert on internal strings, not display text.

**D-61 · `suggestion_status` values renamed: "Apply" → "Solid Match", "Consider" → "Moderate Match", "Skipped" → "Limited Match", "Pre-tracker" → "External"**
Tracker (`applications.xlsx`) column E now shows the same friendly labels as the email digest. "External" replaces "Pre-tracker" — "External" is more accurate (these roles were applied to outside the metis digest flow, not necessarily before metis existed). Cell fill: Solid Match = green, Moderate Match = yellow, Limited Match = grey, External = no fill (white).

**D-62 · LLM fallback for role title extraction in `track.py`**
Regex patterns in `extract_role()` only match when emails use standard phrasing ("applying for the X role at Y"). Many ATS confirmation templates (RealReal, Synopsys, Instacart) mention the title somewhere in the body without using canonical phrasing — regex returns `None`. When `llm_client` is available, `_extract_role_llm()` sends the subject + first 1,500 chars of body to the configured extraction model and asks for the role title only. Returns `None` if the model responds "NONE" or the result fails `_clean_role()` validation. Only fires when regex fails — not a replacement for regex.

**D-63 · `--no-limit` requires explicit confirmation; blocked silently in cron**
`--no-limit` bypasses `MAX_JOBS_PER_RUN` and previously triggered unbounded full-model scoring with no guard — confirmed to exhaust API credits in production. Fix: when `--no-limit` is passed and `n_found > MAX_JOBS_PER_RUN`: (a) interactive TTY → print cost estimate + prompt `[y/N]`; only proceeds on `y`. (b) non-TTY (cron/scheduled) → log warning and cap to `MAX_JOBS_PER_RUN` automatically. The `--no-limit` flag remains useful for one-off catch-up runs; it just requires intent confirmation.

**D-64 · LLM provider boundary is adapter-owned; normalization is provider-agnostic**
Anthropic and OpenAI are both supported through `metis.llm`, which exposes `complete_text()`,
`create_llm_client()`, provider normalization, usage metadata, and per-stage model resolution.
Scoring, extraction, feedback, init, and tracker fallback should consume provider-neutral
`LLMResponse.text` instead of SDK-specific response objects. Provider-specific response quirks
belong in the adapter or in narrow parser recovery functions.

`metis init` follows a two-step contract: raw extraction first, deterministic normalization
second. `normalization.py` maps free-text evidence into canonical fields such as
`role_family`, `target.level`, `aspirations.track`, `company_stage`, `company_scale`,
`team_environment`, `location_preference`, and `customer_types`. This prevents model style
differences from becoming profile schema drift. `unknown` means no usable signal; `other`
means the user gave a signal outside the current taxonomy.

**D-57 · React Email templates bundled in the Python package; `npm install` runs on first digest**
React Email requires Node + `node_modules`, which can't be pip-installed. Resolution: ship `render.ts`, `package.json`, `tsconfig.json`, and all `.tsx` files inside `metis/email_templates/` as setuptools package-data. On first `metis` run, `render.py:_resolve_react_dir()` copies these to `~/.job_pipeline/email_templates/` and runs `npm install --prefer-offline` there (one-time, ~30s). Subsequent runs reuse the cached `node_modules`. If Node is absent the Python fallback (`build_digest_html()`) is used silently. Dev workflow unchanged: project-root `node_modules` takes priority when present.

**D-65 · Scheduled jobs pin state paths and remove legacy Scorerole**
July 2 RCA found two active launchd jobs: `com.metis.digest` and stale `com.scorerole.digest`.
One used the OpenAI/React path and another used the old Claude/Python fallback path. Current
schedule install/remove owns both labels and both cron markers. It also writes `METIS_DATA_DIR`
and `METIS_PROFILE` into the OS job environment so a schedule configured under one data directory
cannot read profile, dedup, or `.env` state from another.

**D-66 · Incomplete scorer output is retried before any parse-error placeholder**
Provider JSON can be syntactically valid but shorter than the input job batch. Previously Metis
filled the missing positions with `Scoring parse error`, which leaked diagnostics into the digest.
Now missing jobs are retried individually before falling back. Parse-error placeholders remain only
as a final diagnostic, not normal digest content.

**D-67 · Undisclosed compensation is neutral unless salary is a hard floor**
The scoring prompt already treats aspirational salary as soft, but provider drift could still emit
`comp: undisclosed` as amber. Post-processing now drops that tag unless `salary_is_hard_floor` is
true. When a hard floor exists, undisclosed comp remains amber because the role cannot be verified.

**D-68 · Adjacent domain stays soft; hard technical niche prerequisites stay hard**
Cross-industry PM work should not be filtered simply because it is outside preferred industries.
However, a role that explicitly requires niche domain credibility such as RDMA/InfiniBand
datacenter networking, kernel/driver work, hardware architecture, GPU scheduling, regulated
credentials, or similar mandatory expertise should create real friction. The scorer should not
promote such roles solely because company and level look attractive.
