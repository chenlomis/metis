# metis — Engineering Standards

Code quality criteria and implementation patterns for this codebase.
Read this before generating new code. See CLAUDE.md for enforced constraints; see ARCHITECTURE.md for system design.

---

## The five dimensions we optimise for

| Dimension | What it means here |
|---|---|
| **Maintainable** | Any function can be understood in under 2 minutes. Changes are local, not rippling. |
| **Extensible** | Adding a new source, model, or output format requires a new file, not edits across 6 existing ones. |
| **Testable** | Every function can be tested without mutating env vars, touching the filesystem, or calling an LLM. |
| **Robust** | Failures are logged and handled explicitly. Nothing fails silently. |
| **Efficient** | API calls are batched and cached. I/O is not sequential when it can be parallel. |

---

## Maintainability

### Module size
- **Hard limit: 400 lines per module.** If a file exceeds this, it has more than one responsibility.
- Current exceptions (tracked as tech debt): `track.py` (~1,275), `report_cmd.py` (~893), `init_cmd.py` (~813), `score.py` (~695), `pipeline.py` (~607), `render.py` (~581). Do not make these larger.
- When adding to a large module, ask: does this belong here, or does it warrant a new file?

### Function length
- **Target: under 40 lines.** Functions longer than 60 lines must have a documented reason.
- Long functions should be decomposed into named helpers. The helper name is the comment.
- Avoid deeply nested blocks (>3 levels). Extract the inner logic.

### Naming
- Private helpers: underscore prefix (`_parse_subject`, `_build_prompt`).
- Stage functions in pipeline: `_stage_<name>` pattern — keep it.
- Booleans: `is_`, `has_`, `should_` prefix.
- Never abbreviate unless the abbreviation is universal (`url`, `jd`, `llm`).

### Constants and magic values
- All thresholds, model names, chunk sizes, TTLs → named constants at module top.
- Model names must be defined in **one place only**. Currently duplicated across `score.py`, `extract.py`, `feedback.py` — consolidate into `config.py` (see Extensibility).
- No magic numbers inline (`sleep(1)` → `_FETCH_DELAY_S = 1.0`).

---

## Extensibility

### Config as parameters — the most important rule
**Never call `os.getenv()` at module import time.** This is the single biggest extensibility blocker in the codebase today.

**Why it matters:** module-level env reads bake config in at import. You cannot test with a different config, run two profiles simultaneously, or expose the tool as an MCP server without mutating global state.

**The pattern:**
```python
# BAD — baked in at import
MODEL = os.getenv("MODEL", "claude-sonnet-4-6")

def score_jobs(jobs):
    client = anthropic.Anthropic()  # key from env
    ...

# GOOD — passed explicitly
def score_jobs(jobs, *, config: Config):
    client = anthropic.Anthropic(api_key=config.anthropic_key)
    ...
```

`Config` is a dataclass defined in `config.py`. New code should receive configuration through parameters instead of adding new module-level environment reads. Some legacy CLI paths still read environment variables directly; finish migrating those before exposing metis as a long-lived service or MCP server.

### Source abstraction
New job sources belong in `sources/`. Each source module must:
- Export a function matching `fetch(config: Config) -> list[dict]`
- Return dicts with keys: `title`, `company`, `location`, `job_id`, `url`, `jd`, `source`
- Register in `sources/__init__.py` router

Do not add source-specific logic to `pipeline.py`.

### Eval schema
The dict that `score.py` emits and `render.py` consumes is a coupled contract. Changes to one require changes to the other. Use the `EvalResult` TypedDict in `types.py` so mismatches are type errors, not silent render failures.

```python
# target: metis/types.py
from typing import TypedDict, Literal

class EvalResult(TypedDict):
    verdict: Literal["apply", "consider", "skip", "filtered", "prescreened"]
    score: int
    dimensions: dict[str, int]       # exactly 6 named dimensions
    leveragePoints: list[str]         # exactly 2
    frictionPoints: list[str]         # exactly 1
    summary: str
```

### Render vs deliver
`render.py` owns pure HTML generation. `deliver.py` owns SMTP delivery and receives credentials via `Config`:
- `render.py` — pure function: `render_html(jobs, run_date, ...) -> str`. No I/O, no credentials.
- `deliver.py` — side-effectful: `send_digest(html, run_date, *, config: Config)`. Testable by mocking SMTP.

---

## Testability

### The core rule
**Every function must be testable by passing arguments — no env var reads, no hardcoded paths, no global state inside the function body.**

### What a good test looks like
```python
def test_salary_gate_filters_below_floor():
    job = {"extraction": {"salary_max": 90_000}}
    profile = {"salary_floor_usd": 120_000}
    result = apply_salary_gate(job, profile)
    assert result["eval"]["verdict"] == "filtered"
    assert result["eval"]["gate"] == "salary_floor"
```
- No `monkeypatch.setenv` required
- No filesystem access
- No LLM call
- Assertion on a specific, observable output

### Mocking LLM calls
- Use `monkeypatch` to replace `client.messages.create` — never make real API calls in tests.
- Fixture pattern: `@pytest.fixture def mock_client(monkeypatch): ...`
- Test the *logic around* the LLM call, not the LLM output itself.

### Test file → module mapping
Each production module has a corresponding test file:
- `metis/score.py` → `tests/test_core.py`
- `metis/extract.py` → `tests/test_extract.py`
- `metis/track.py` → `tests/test_track.py` (target — not yet created)

### CI
Run `make test` inside the venv. A GitHub Actions workflow (`.github/workflows/test.yml`) should run on every push to main. Tests that only pass locally are not tests.

---

## Robustness

### Error handling hierarchy
1. **Expected failures** (bad email format, missing JD, API 429): catch specifically, log at `WARNING`, continue pipeline.
2. **Unexpected failures** (assertion errors, key errors, type errors): let them propagate — do not swallow. The pipeline should fail loudly.
3. **Infrastructure failures** (SMTP down, IMAP timeout): catch, log at `ERROR`, exit with non-zero status. State must be consistent on exit (T-07: `save_seen_roles` after `send_digest`).

### The pattern for expected failures
```python
# GOOD
try:
    result = _parse_json_response(raw)
except json.JSONDecodeError as exc:
    log.warning("JSON parse failed for %s at %s: %s", title, company, exc)
    return _blank_extraction()

# BAD — hides bugs
try:
    result = _parse_json_response(raw)
except Exception:
    return None
```

### Never
- `except Exception: pass` — always log, always explain why you're swallowing.
- `except Exception: return None` without a log line.
- Catch broad `Exception` when you mean `json.JSONDecodeError` or `KeyError`.

### Fail-fast validation
Validate at the boundary, not deep in the stack:
- Credentials: `_validate_env()` before any API call.
- Profile: load and validate before pipeline starts.
- Job data: `_is_plausible_job_row()` before any write.

---

## Efficiency

### API calls
- Batch Haiku calls: `_EXTRACT_CHUNK_SIZE = 10`, `_SCORE_CHUNK_SIZE = 15`. Do not call once per job.
- Cache the system prompt: `cache_control: {"type": "ephemeral"}` on profile context.
- Pre-screen with Haiku before Sonnet — never send a job to Sonnet that Haiku would filter.
- Show cost estimate before any large batch. Never surprise the user with a bill.

### I/O
- JD enrichment is currently sequential HTTP. **Target: async with `httpx` + semaphore (max 5 concurrent).** Until then, do not make it more sequential.
- State files (`seen_roles.json`, `runs.jsonl`) — read once at start, write once at end. Never open in a loop.
- xlsx — open once per `run_track` call, make all writes, close once.

### Sleeping
- `time.sleep()` in retry loops is acceptable. Elsewhere it is a code smell.
- Retry pattern: exponential backoff with jitter, max 3 attempts, log each retry.

---

## Refactor sequence (current tech debt)

Work through these in order. Run `make test` after each. Do not combine steps.

| Step | Change | Risk | Status |
|---|---|---|---|
| 1 | Add `EvalResult` TypedDict in `types.py`; annotate `score.py`, `render.py`, `trace.py` | Zero — additive only | Done 2026-06-26 |
| 2 | Split `render.py` → `render.py` (HTML only) + `deliver.py` (SMTP) | Low — mechanical split, tests cover render format | Done 2026-06-26 |
| 3 | Split `track.py` → `track_imap.py`, `track_parse.py`, `track_write.py` | Medium — many functions, keep signatures identical | Done 2026-06-26 |
| 4 | Config-as-parameters: define `Config` dataclass, thread through all call sites | High — touches 9 modules; do last; requires full test pass | In progress — dataclass exists; legacy CLI paths still read env directly |

Step 4 is the prerequisite for the MCP server. Do not attempt it until steps 1–3 are complete and green.
