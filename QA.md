# scorerole — QA Framework

> This document defines *how* scorerole is tested — the test strategy, pass definitions,
> and the checklist to run for new features. It is generic: project-specific flows and
> exit criteria live in `SPEC.md`; open issues live in the `Known Issues & Tech Debt`
> section of `ARCHITECTURE.md`.
>
> Once written, this file should rarely change. Add a new pass definition only when
> introducing a fundamentally new concern (e.g., a multi-user mode would add a Tenant
> Isolation pass). Otherwise, work happens in `SPEC.md` and `ARCHITECTURE.md`.

---

## Test pyramid

| Level | What it checks | Tooling | When to run |
|---|---|---|---|
| **Unit** | Individual functions work in isolation | `pytest` | On every change to that function |
| **Integration** | Components hand off data correctly at seams | `pytest` | When an interface between components changes |
| **Regression** | Previously passing behavior still passes | Run all `pytest` tests | Before every merge / release |
| **Persona / QA pass** | Implementation matches intent from a real user's vantage point | Manual / Claude agent | New feature or before release |

Regression is not a separate test type — it's re-running unit + integration tests after a change.
Persona passes are the judgment layer that automated tests can't replace: they check whether the
system does the *right thing*, not just whether it does the *specified thing*.

---

## Automated test suite

```
tests/test_core.py   — unit + integration tests (pytest)
```

Run:
```bash
python -m pytest tests/ -q
```

All tests must pass before merging. The suite covers:

- **Parsing:** `extract_jobs()`, `extract_jobs_html()` — job extraction from raw email bodies
- **Dedup:** `_role_hash()`, seen-roles gate, 14-day TTL pruning
- **Pre-screen:** Haiku pass result parsing; function-only filtering; no seniority filtering
- **Scoring:** `score_jobs_batch()` chunk sizes; merge of chunk results; truncation fallback
- **Rank:** verdict re-derivation from score + thresholds; sort order within tiers
- **Profile / init:** salary floor single-source-of-truth; salary cleanup from deal_breakers; domain flex note injection
- **State:** `save_seen_roles()` write + prune; file permission (0o600)

When adding a new function or behavior, write a unit test *before* shipping. If the behavior
touches a component boundary (e.g., scorer output → renderer input), write an integration test.

---

## Persona passes

A persona pass runs the full pipeline (or the relevant sub-pipeline) against real or mock input,
from the perspective of a realistic user, and checks whether the *output* matches what that user
would expect. It requires judgment — a pytest can't answer "is this digest useful?".

Each pass has:
- **Profile:** who is the user and what do they care about
- **Checks:** the specific things to verify from that perspective
- **Tools:** what to look at (digest email, log output, profile.yaml, etc.)

### Engineer pass
**Who:** A developer reading the codebase cold — no prior context.
**Checks:**
- Can they run `scorerole init` and get a working profile without reading docs?
- Do log messages make sense without knowing internal terminology?
- Are component boundaries (IMAP → parser → pre-screen → scorer → renderer → SMTP) clearly logged?
- Are error messages actionable (tell you what to do, not just what failed)?
- Is the cost estimate surfaced before a large run?

**Signs of failure:** Cryptic errors; silent failures; logs that only make sense if you wrote the code.

### Security pass
**Who:** A reviewer looking for data leaks, path traversal, injection, or credential exposure.
**Checks:**
- Is `profile.yaml` and `seen_roles.json` created at 0o600? `~/.job_pipeline/` at 0o700?
- Are temp files created with `mkstemp` (not `NamedTemporaryFile`) and chmod'd before write?
- Are `.env` secrets never logged, even at DEBUG level?
- Does `scorerole debug` output redact email addresses and credential fields?
- Are there any `os.system()` or `subprocess` calls that could be injected via profile fields?

**Signs of failure:** Credential appears in any log line; temp file visible world-readable during write; `profile.yaml` created at 0o644.

### User pass
**Who:** The actual intended user (Lomis) running a real weekly digest.
**Checks:**
- Does the digest surface roles that feel relevant to the stated profile?
- Are verdict reasons (leverage/friction points) specific to *this* role — not generic filler?
- Are hardware-adjacent or domain-gap roles handled correctly (friction, not disqualifier)?
- Does the deal-breaker section in the digest accurately reflect actual hard-no criteria?
- Would the top 3 "apply" roles be ones the user would actually apply to?

**Synthesis prompt (run at end of User pass):**
> "What are the 3 things most likely to make a real user give up on scorerole in the first 10 minutes?"

**Signs of failure:** Generic rationale ("good cultural fit"); deal-breaker is over-broad and filters legitimate roles; top-scored roles are obviously wrong.

**Developer lens (apply within this same pass):** Also walk the flow as a developer who wants to extend or contribute — where would they get stuck, what's undocumented, what's hard to change? Check: is `sources/__init__.py` the obvious place to add a new source? Does `ARCHITECTURE.md § Extensibility` cover the steps they'd need? Are extension points in the code obvious without reading every file? Signs of failure: a contributor would need to read `pipeline.py` end-to-end to figure out where to plug in a new source; extension steps are undocumented or contradict what's in the code.

### Documentation pass
**Who:** A new user following README.md from scratch.
**Checks:**
- Does the Quickstart produce a working `scorerole run` without any undocumented steps?
- Are all three LinkedIn sender addresses documented?
- Is the privacy / data-flow table accurate with current code?
- Are all env variables in `.env.example` documented in the README?
- Are troubleshooting entries accurate and actionable?

**Signs of failure:** Step fails silently; README mentions a feature that no longer exists (or vice versa); privacy section describes stale behavior.

### PM / spec pass
**Who:** The product owner checking that what shipped matches what was specified.
**Checks:**
- Does the implementation match every exit criterion in SPEC.md §7?
- Are any new behaviors documented in SPEC.md (via the delta process in §8)?
- Are known limitations documented in ARCHITECTURE.md (not papered over)?
- Is there any behavior in the code that has no spec entry — i.e., implicit product decisions?

**Signs of failure:** Behavior that isn't in SPEC.md; spec entry that the code doesn't implement; a "known issue" that's actually a silent breaking bug.

---

## New feature checklist

When adding a feature, work through this in order:

1. **Spec first:** Add the new flow + exit criteria to `SPEC.md`. Requires `SPEC_UPDATE=1` env var on commit.
2. **Unit tests:** Write `pytest` tests for the new logic before shipping it.
3. **Architecture delta:** If the new feature adds or changes a data handoff between components, update the pipeline diagram and Module Map in `ARCHITECTURE.md`. If it introduces a new known limitation, add a Tech Debt entry.
4. **Integration test:** If a new component boundary exists, add an integration test covering the contract.
5. **Regression:** Run `python -m pytest tests/ -q`. All tests must pass.
6. **Engineer pass:** Read the new log output cold. Is it interpretable?
7. **User pass:** Run `scorerole run` (or `run_persona_test.py` for cross-profile validation). Is the digest correct?
8. **Triage:** Classify any findings (below).
9. **Spec sync:** Commit any SPEC.md / ARCHITECTURE.md updates.

---

## Triage categories

After a pass, classify every finding:

| Category | Definition | Action |
|---|---|---|
| **Blocks** | User-visible wrong behavior; silent failure; data loss or leak | Fix before shipping |
| **Known limitation** | Real gap but acceptable for current scope; user won't be surprised if documented | Add to `ARCHITECTURE.md` Known Issues; reference in `SPEC.md` §9 if relevant |
| **Backlog** | Nice-to-have; no current user impact | Add to `ARCHITECTURE.md` Known Issues at P3; revisit on next major pass |

Do not paper over a "Blocks" finding as a "Known limitation." The test is: would a real user, running the tool for the first time, notice this and lose trust? If yes, it blocks.

---

## Persona test tooling

`run_persona_test.py` (repo root) runs the full pipeline against real Gmail input under a mock profile:

```bash
python run_persona_test.py
```

It:
1. Backs up `~/.job_pipeline/profile.yaml` and `seen_roles.json`
2. Swaps in each persona profile in turn
3. Runs `pipeline.run_pipeline()` with `score_all=True` and 7-day lookback
4. Sends a labelled digest: `[Persona Name] Personalized Job Alert Digest — <date>`
5. Restores original state

Add new personas to the `personas` list in `run_persona_test.py`. Persona profiles live at
`~/.job_pipeline/profile_<slug>.yaml` (outside the repo — never commit real profiles).

Example personas already created:
- `profile_ml_eng.yaml` — Alex Rivera, Senior ML Engineer (Anthropic → DeepMind → Lyft)
- `profile_designer.yaml` — Jordan Lee, Senior Product Designer (Figma → Stripe → InVision)

---

## Periodic issue review

The Known Issues & Tech Debt section in `ARCHITECTURE.md` is the living backlog. Run a review pass every few months (or before a significant feature addition) to:

1. Re-read each open entry. Has the underlying behavior changed — is the issue resolved, worsened, or no longer relevant?
2. Check whether any P2 issues have become P1 (e.g., T-01 score truncation is mitigated by chunking; downgrade or close it).
3. Promote any P3 issues that have become real user pain.
4. Close resolved entries (mark `~~strikethrough~~` with resolution note + date, or delete if stale).

A good prompt for a review pass:
> "Read ARCHITECTURE.md Known Issues. For each entry: is it still accurate, still the right priority, and still open? Update accordingly."
