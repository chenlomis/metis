# Contributing to metis

Thanks for taking a look at metis. Issues, ideas, docs fixes, parser edge cases, and small PRs are all welcome.

metis is still early, so a lot of useful contributions are not giant features. A clearer error message, a better setup note, a new alert parser, or a test that catches a weird email format can make the tool meaningfully better for the next person.

If you are thinking about a larger change, open an issue first so we can line up on the shape of it before you spend time building.

## Development setup

```bash
git clone https://github.com/chenlomis/metis
cd metis
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
npm install
make test
```

`make test` automatically uses `.venv`, `venv`, or the currently active Python environment.

## Before opening a PR

- Run `make test`.
- Keep changes scoped to the behavior you are fixing.
- Do not commit real `.env`, profile, tracker, email, or runtime state files.
- Update `README.md` when changing user-facing commands or setup.
- Update `ARCHITECTURE.md` when changing data flow or extension points.
- Add or update tests for parser, state, scoring contract, rendering, or scheduler changes.

## Good areas to contribute

The [README roadmap](./README.md#roadmap) is the best source for larger directions. A few especially useful areas:

- Email parsing edge cases, especially LinkedIn variants and non-LinkedIn alert formats.
- New source adapters for job boards, ATSs, and company alert emails.
- Outlook / Microsoft 365 support.
- LLM provider abstraction, so scoring is not tied to one backend.
- Cleaner library boundaries for the future MCP server and PyPI package.
- Output interfaces beyond email, such as chat, Slack, Telegram, or local agent surfaces.
- Resume tailoring and application-assist workflows with human approval before submission.
- Globalization: non-English alert emails, international salary/location handling, and region-specific job boards.
- Tests around state safety, dry-run behavior, scoring contracts, and scheduler behavior.
- Accessibility and email-client compatibility improvements for the digest.

## Architecture notes

The CLI entry point is `metis/cli.py`; digest orchestration lives in `metis/pipeline.py`. The most important boundaries are:

- `metis/sources/`: fetch and parse job sources.
- `metis/extract.py`: structured JD extraction.
- `metis/score.py`: scoring and verdict assignment.
- `metis/render.py`: digest HTML generation.
- `metis/deliver.py`: SMTP delivery.
- `metis/state.py`: persisted state and dedup.

`score.py` and `render.py` share a locked eval schema. If you change one side of that contract, update the other side in the same PR and run the full test suite.

For UI, email, and report changes, preserve the existing tone and visual system unless the PR is explicitly about redesign. Colors and theme helpers should stay centralized rather than scattered through feature code.

## Privacy

metis handles resumes, job history, email metadata, and API credentials. Treat privacy as part of correctness:

- Never log secrets.
- Never commit personal profiles or tracker files.
- Document any new third-party service and exactly what data it receives.
- Prefer local files under `~/.job_pipeline/` for runtime data.
