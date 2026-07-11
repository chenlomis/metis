# Changelog

Not every internal experiment lands here. This file is for user-visible changes,
behavioral fixes, and migration notes worth knowing before you upgrade.

## Unreleased

### Added
- Added MCP service hardening around dry-run defaults, structured failures, and concurrent feedback writes.
- Added capped tracking smoke support with `METIS_TRACK_MAX_EMAILS` for large inboxes and low-cost QA.
- Added a conservative Ruff lint gate for syntax and likely-runtime errors.

### Fixed
- `MAX_JOBS_PER_RUN` set in the shell now wins over `.env`, so cost caps are respected.
- Data-directory `.env` files now override project defaults, which keeps persona and smoke runs isolated.
- `metis sources` mutations now write to the active `METIS_PROFILE` instead of the default profile path.
- `metis track --dry-run` no longer runs digest backfill writes.
- `metis track` now uses the active provider API key instead of always passing the Anthropic key.
- Obvious job alerts, receipts, and news emails no longer spend LLM fallback calls during tracking.
- `metis summary --lookback garbage` now fails clearly instead of silently using a 30-day window.
- CLI logging now falls back to console output if the log file is not writable.

### Notes
- `metis summary --output report.pdf` still requires `weasyprint` and its platform dependencies.
- The active `metis init` flow is interactive-only; documented `--resume` / `--linkedin` flags need a follow-up pass.
