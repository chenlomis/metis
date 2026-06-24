# scorerole — Decisions Log

Concise record of non-obvious choices. The "why" that doesn't belong in code comments.
See ARCHITECTURE.md for deep technical detail.

---

## Profile & schema

**D-01 · profile.yaml lives outside the repo (`~/.job_pipeline/`)**
Personal data (resume text, salary, location) must never be committed. The repo ships `profile.template.yaml` and `examples/profile_*.yaml` with fake personas. `mode=0600` enforced on write.

**D-02 · Single profile.yaml for v1; conceptually four files**
Target mental model: `profile.yaml` (candidate), `sources.yaml` (job sources), `config.yaml` (thresholds/schedule), `feedback.md` (calibration). They stay in one file for v1 simplicity but code should treat them as separate concerns — no cross-concern reads in the same function. Split when there's a concrete reason (MCP config-as-parameters refactor is the likely trigger).

**D-03 · `experience`, `education`, `strengths` nest under `candidate`**
These are resume-derived facts about the person, not search preferences. Nesting them under `candidate` makes the boundary explicit: `candidate.*` = "who you are", everything else = "what you want". Decided June 2026 with init2 schema simplification.

**D-04 · `deal_breakers` and `salary_floor_usd` are top-level**
They are search-criteria, not identity. Keeping them top-level (not under `preferences`) signals their weight: hard gates, not soft signals. `preferences.base_salary_target_usd` is the aspirational number; `salary_floor_usd` is the filter.

**D-05 · Removed `scoring` block, `green_flags`, `yellow_flags`, `red_flags` from user-facing schema**
`scoring` thresholds (apply_threshold, consider_threshold) are system knobs, not candidate preferences — they belong in `config.yaml` eventually. `green/yellow/red_flags` were scoring-internal tags Claude emits; exposing them in the profile created confusion about whether the user or Claude owned them. Removed in June 2026 simplification.

---

## Onboarding (init / init2)

**D-06 · `scorerole init` is now the conversational wizard (formerly init2); init2_cmd.py kept as reference**
The conversational flow (freeform resume paste → Claude extract → clarification → review) proved lower-friction and higher completion rate than the structured 8-step form. As of June 2026, `scorerole init` routes to the conversational wizard. `init_cmd.py` (structured form) and `init2_cmd.py` are retained as reference implementations. They write to the same `profile.yaml` and are interchangeable.

**D-07 · init2 edit menu mirrors wizard steps, not profile fields**
The review menu offers "Step 2 — What you're looking for" and "Step 3 — What you'd pass on" rather than "Roles + level", "Salary floor", etc. Users entered data through steps, so editing should mirror that mental model. Field-level edits available via "Open profile in editor" for power users.

**D-08 · "Re-run extraction" only appears when resume context is available**
In quick-edit mode (existing profile), the wizard has no resume in memory. Showing "Re-run extraction" would be a broken promise (API key present but no resume to re-extract from). The option is conditionally hidden rather than shown-but-disabled to avoid confusion.

**D-09 · init2 uses single-line text for Steps 2–3 (not multiline textarea)**
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
Claude is given score thresholds in the prompt but doesn't guarantee compliance. `rank_jobs()` re-derives verdict from the numeric score. This prevents a score-62 role from being surfaced as "apply" due to Claude drift.

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

---

## Interface & extensibility

**D-19 · Interface roadmap: CLI → MCP → PyPI package → Docker → Web app**
Each stage gates on the previous. MCP server is next and requires a "config as parameters" refactor so core functions don't read from `.env` at import time. Web app is not planned speculatively — only if OSS adoption demonstrates demand.

**D-20 · No auto-calibration; all feedback is explicit and user-initiated**
Score drift signals (applied to roles scored "skip") are surfaced as digest footer nudges, not auto-adjustments. User decides whether to act via `scorerole init → Quick edits` or `scorerole feedback`. Rationale: auto-calibration on thin behavioral signals produces confident wrong answers.

**D-27 · Single shared identity in `prompts.py`; no hardcoded candidate names (OSS-safe)**
All LLM calls share a common identity anchored in the headhunter framing: scorerole is the advisor, the candidate is the client, `profile.yaml` is the client brief. `prompts.py` is the single source of truth for identity templates and system prompt assembly — same principle as `theme.py` for colors. `SCORING_IDENTITY` and `FEEDBACK_IDENTITY` contain `{candidate_name}` format slots; no literal names are hardcoded. `build_candidate_context(profile)` synthesizes a terse brief from the profile dict that orients Claude before it reads the full `render_profile` detail. This separation (brief → detail) prevents Claude from pattern-matching keywords before it has a coherent perspective on the candidate.

**D-28 · Identity sits above profile in the system prompt; feedback parser uses system/user split**
Scoring system prompt order: identity → candidate brief → full rendered profile → feedback → bullet rules → rubric. The identity anchors Claude's perspective before it reads data. For feedback parsing (`_claude_process`), grounding rules go in the `system=` parameter and the analysis format goes in the user turn — this prevents the analysis task from overriding the identity constraints.

**D-23 · `scorerole feedback` flow: collect → Haiku parse → confirm → save; no auto-weight changes**
Free-form text input (blank line to finish). Haiku parses the text into structured metadata (roles, dims, conflicts, profile-level items). Conflicts require user resolution before saving. Confirmed entries are appended to `feedback.md` with an HTML comment header (`<!-- id:... | run:... | roles:... | dims:... -->`) for traceability. A separate `feedback_log.jsonl` records audit metadata (never injected into prompts). Consistent with D-20: no weights are modified automatically.

**D-24 · `feedback.md` has no TTL — all entries injected**
Feedback represents calibrated user intent, not time-bounded state. Unlike `seen_roles.json` (30-day TTL), there is no expiry. If the file grows impractically large in the future, add a summarisation step inside `load_feedback_text()` before the content is returned to `score.py`. Do not add TTL logic elsewhere.

**D-25 · Feedback conflicts: only flag real contradictions with existing text**
`_claude_process()` is instructed to populate `conflicts[]` only when an actual quoted statement in the existing `feedback.md` directly contradicts the new feedback. Empty prior feedback → empty conflicts list. This prevents hallucinated conflicts against non-existent prior state (observed bug: Claude flagging "conflict" when existing feedback was empty).

**D-26 · `profile_items` detection: only explicit blanket rules, not inferred preferences**
The Haiku processing step may detect feedback that belongs permanently in `profile.yaml` (e.g. "I never want B2C roles") and route it to `scorerole init → Quick edits`. It must only flag statements the user *explicitly wrote* as a blanket rule. Inferring preferences from company names or role titles (e.g. "Workday HCM is foreign") is prohibited — this caused spurious profile flags on first-run feedback.

---

## UI / theme

**D-21 · `theme.py` is the single source of truth for all colors**
No color hex values anywhere else in the codebase. Rich `style=` parameters must use `Style(color=THEME["key"])` objects — not f-string `"color:#hex"` (Rich's string parser rejects that syntax). `INQUIRER_STYLE` must be created via `get_style()` not a plain dict.

**D-22 · Editor label in init2 is dynamically resolved at runtime**
`open_in_editor()` respects `$VISUAL`/`$EDITOR`, then tries `code` → `cursor` → `zed` → `nano`/`vi`. The menu item reflects what will actually open: "Open profile in VS Code" vs "Open profile in your default editor". Avoids surprising users who expect their configured editor.

---

## Traceability & observability

**D-32 · `trace.py` writes append-only JSONL per job, every run**
`~/.job_pipeline/runs.jsonl` gets one record per job regardless of verdict (prescreened, filtered, or scored). This is the raw material for `scorerole report`. Decision: write at scoring time, not at digest delivery time — traceability shouldn't depend on SMTP succeeding.

**D-33 · Scoring traceability shown in digest, not a separate command (intent)**
The email digest should include score breakdowns (dimension scores, leverage/friction points, gate reason if filtered). The raw data is in `runs.jsonl` and the eval dict already carries it. `scorerole report` reads `runs.jsonl` for trends; the digest surfaces per-role traceability inline.

---

## Email parsing

**D-34 · LLM fallback for email parsing: LLM first, regex safety net (planned)**
Current flow: regex primary, fails silently on novel LinkedIn email formats. Planned: try LLM extraction (Haiku) first, fall back to regex if LLM returns malformed JSON or zero results. Module should be an optional import with try/except so the core works without an API key. Same pattern as the careerops.py integration.

---

## Reporting

**D-35 · `scorerole report` reads `runs.jsonl` — no separate DB needed**
Score distribution, apply rate, and score-vs-interest correlation can all be computed from `runs.jsonl` + `applications.xlsx` join on `role_hash`. No separate analytics DB. Report generates in-terminal summary + optional HTML export.

---

## Library / MCP design

**D-36 · Config-as-parameters before MCP or PyPI**
Several modules call `os.getenv()` at import time (score.py, extract.py, linkedin.py). A library must not do this — it hijacks the caller's environment. Fix: pass `api_key=`, `model=`, `profile_path=` explicitly into core functions. The CLI wrapper reads from `.env` and passes through. This is a prerequisite for both the MCP server (different working dir) and PyPI publish (clean importable API).

**D-37 · MCP surface: 5 tools, wrapping existing functions**
`score_jobs`, `get_last_digest`, `check_tracker`, `run_track`, `get_profile`. Thin wrapper — no rewrite. Prerequisite: D-36 + stable core functions (finish `scorerole report` and feedback loop first so the MCP surface doesn't change signatures mid-flight).

**D-29 · Employer-lens scoring: deferred**
Rejection patterns (applied to roles → got rejected) could indicate a mismatch between self-assessment and employer assessment. Adding an employer lens to scoring is a later concern — it requires enough track data to be meaningful and risks producing confident wrong signals on thin data. Address after `scorerole report` surfaces the pattern clearly.

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
LinkedIn's positional parser (`before_lines[-3/-2/-1]`) can shift when trailing lines vary, writing company-name-as-title or location-as-company into the spreadsheet. `_is_plausible_job_row(title, company)` in `tracker.py` blocks writes where the title lacks job-role keywords or the company field looks like a bare location string. Uses anchored regex to avoid false positives ("San Francisco Health" passes; "San Francisco" alone is blocked).

**D-40 · LinkedIn positional parsing: 3-case runtime shift detection in `extract_jobs()`**
When `before_lines[-2]` (expected company slot) matches `_LOCATION_LIKE`, the parser shifts all three fields up one position (Case A). When `before_lines[-3]` (expected title slot) contains a company-name suffix (Inc/Corp/Ltd) without job-role keywords, it also shifts (Cases B and C). This runtime correction is per-email and never mutates the source data. Both D-39 and D-40 are needed: D-40 fixes parsing at source; D-39 catches any that slip through at the write layer.

**D-41 · launchd retry: `KeepAlive.SuccessfulExit=false` + `ThrottleInterval=900`**
Scheduled runs sometimes fire at Mac wake-from-sleep before DNS resolves, causing `[Errno 8] nodename nor servname provided`. Code-level retry (3 attempts, 30s backoff) handles transient DNS. launchd retry (`ThrottleInterval=900`) handles the case where all 3 code retries fail — launchd restarts the job 15 minutes later, at which point network is reliably available. `SuccessfulExit=false` tells launchd to retry only on non-zero exit, so a clean run is never re-triggered.

**D-42 · score.py ↔ render.py eval schema treated as a coupled contract**
The eval dict shape that `score.py` emits (verdict enum, 6 named dimensions, exactly 2 leveragePoints, exactly 1 frictionPoint) is consumed directly by `render.py`. Changing either file's expectations without updating the other causes silent rendering errors or missing data. Documented in CLAUDE.md constraint #5. OSS users who customize the schema must change both files in the same edit.

**D-43 · `_role_hash()` implementation frozen; `_HEADERS` column order frozen**
`_role_hash()` in `state.py` produces keys persisted in `seen_roles.json`. Changing the hash function (normalization, algorithm, slice length) invalidates all historical dedup state — every previously seen role re-processes, causing a flood email. Similarly, `_HEADERS` in `tracker.py` defines the xlsx column layout. Existing `applications.xlsx` files contain months of data; reordering or inserting columns corrupts existing rows. Column *header text* is safe to rename; column *order* is not. Both frozen in CLAUDE.md constraints #6 and #7.

---

## Interface & distribution (June 2026)

**D-44 · Target persona is passive job seekers, not active ones**
Passive seekers (biweekly/weekly cadence, selective, won't mass-apply) are the better fit for scoring-first design. Active seekers want volume tools; they churn from anything that adds friction before the application. Passive seekers value signal quality over throughput — scorerole's 2-layer AI pipeline is the right investment for that persona.

**D-45 · Interface roadmap: CLI → MCP server → PyPI → Docker → web app (on demand only)**
Stage 0 (done): local CLI. Stage 1 (next): MCP server — local subprocess, no hosting, Claude Code users can `claude mcp add scorerole`. Stage 2: PyPI package after stable public API. Stage 3: Docker for users who skip the venv setup. Stage 4: web app only if demonstrated demand from non-technical users. Prereq for Stage 1: config-as-parameters refactor (no `os.getenv()` at module import time).

**D-46 · prompts.py as canonical, OSS-safe identity layer**
All LLM prompt templates live in `prompts.py`. Candidate name and profile are injected dynamically — no personal details hardcoded. This makes the repo safe to publish as OSS without scrubbing. Call sites (`score.py`, `feedback.py`) import from here; no duplicate prompt strings anywhere in the codebase.

**D-47 · Scoring voice: second-person ("You/Your"), past tense for candidate actions, present for JD requirements**
Bullet guide rule added to `score.py`: "You led" (past, candidate action) vs. "The role requires" (present, JD requirement). Third-person "{first_name}" removed — second-person reads as direct coaching, not a report about the candidate. This is a prompt constraint, not a post-processing step, so Claude enforces it at generation time.
