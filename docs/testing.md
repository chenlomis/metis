# Testing

Metis relies on targeted regression tests more than a single global coverage
number. Many important behaviors sit at provider boundaries: email formats,
LLM JSON, OAuth state, launchd/cron config, and spreadsheet writes. Line
coverage is useful for visibility, but the primary goal is to keep these
failure modes explicit and repeatable.

## Commands

```bash
make test-fast      # fast core + schedule pass
make test           # full pytest suite
make test-e2e       # persona smoke tests
make coverage       # full suite with coverage report and coverage.xml
make lint           # compile metis/ and tests/
```

`make coverage` is informational. The project does not enforce a global
coverage threshold yet because the baseline should be reviewed first, and
because prompt-heavy/provider-heavy modules need contract tests more than a
blanket percentage target.

## Coverage Matrix

| Risk area | What should not regress | Primary tests |
|---|---|---|
| CLI routing | Commands validate the right environment and pass provider-specific keys | `tests/test_cli.py`, `tests/test_init2.py` |
| LinkedIn/email parsing | Real alert variations parse into stable role objects; malformed alerts do not crash | `tests/test_core.py`, `tests/test_oauth_email_fetcher.py` |
| Inbox provider state | Gmail/Outlook/IMAP routing, token storage, and corrupt-token handling stay isolated | `tests/test_oauth_email_fetcher.py`, `tests/test_oauth_security.py` |
| LLM provider boundary | Anthropic/OpenAI clients normalize responses, errors, usage, and model selection | `tests/test_llm_provider.py` |
| LLM malformed output | Bad JSON, short responses, and parse failures degrade predictably | `tests/test_extract.py`, `tests/test_feedback_cmd.py`, `tests/test_core.py` |
| Score/render contract | Scoring eval shape remains compatible with digest rendering | `tests/test_score_render_contract.py`, `tests/test_render_format.py` |
| Digest format | Locked labels, section names, tags, buttons, and skipped-role layout do not drift | `tests/test_render_format.py` |
| Pipeline delivery guarantees | Dry-run avoids writes, state updates happen in the intended order, retries preserve deliverability | `tests/test_delivery_guarantees.py` |
| Schedule config | launchd/crontab output, schedule persistence, pause/resume/remove, and pinned state env are preserved | `tests/test_schedule.py` |
| Tracker/state safety | Dedup keys, tracker writes, and application parsing stay stable | `tests/test_core.py`, `tests/test_regression_jun19.py` |
| Feedback calibration | Feedback parsing, conflict handling, and persistence stay append-only and recoverable | `tests/test_feedback_cmd.py` |
| Prompt contracts | Required identity, profile context, and calibration text remain in prompts | `tests/test_prompts.py`, `tests/test_prompt_utils.py` |
| Persona isolation | Persona runs do not touch the primary profile/data directory | `tests/test_e2e_personas.py` |
| Resume tailoring | Resume command routing, artifact generation, and tracing stay isolated from the digest pipeline | `tests/test_resume_tailor.py`, `tests/test_integration_logging.py` |

## Testing Guidance

- Prefer deterministic fixtures over live provider calls. Tests should not
  require Gmail, Outlook, Anthropic, OpenAI, launchd, cron, or a real tracker.
- Add focused regression tests for every production failure. A narrow test near
  the broken boundary is better than broad mocks that only prove code executed.
- Keep render and score contracts strict. If a digest label, eval schema, or
  tracker column changes intentionally, update the contract test in the same
  change.
- Treat coverage reports as a map for blind spots, not a substitute for
  scenario coverage. Deterministic modules such as `state.py`, `contracts.py`,
  `normalization.py`, and tracker helpers are better candidates for future
  module-level thresholds than prompt-heavy modules.
