# Security Policy

metis is a local-first CLI, but it handles sensitive data: resumes, scoring profiles, Gmail access, job history, application status, and API keys. Please report security issues privately instead of opening a public issue.

## Reporting a Vulnerability

Email the maintainer or use GitHub private vulnerability reporting if it is enabled for the repository. Include:

- A short description of the issue.
- Steps to reproduce.
- What data could be exposed or modified.
- Any relevant logs with secrets redacted.

Please do not include real API keys, Gmail app passwords, resume text, or private email contents in a report.

## Security Boundaries

metis is designed so that:

- Credentials stay on the user's machine.
- Runtime state lives outside the repo, usually under `~/.job_pipeline/`.
- Resume/profile/job data is sent only to configured AI providers for extraction and scoring.
- Gmail credentials are used only for IMAP/SMTP over SSL.

Any new integration that sends user data to another service must be documented in the README privacy section before release.

## Local Secret Hygiene

Do not commit:

- `.env` or `.env.*` files, except `.env.example`.
- `profile.yaml`.
- `applications.xlsx`.
- `seen_roles.json`.
- `runs.jsonl`.
- `feedback.md`.
- `debug_email.txt`.

The repository `.gitignore` covers these files. Before publishing or tagging a release, run:

```bash
git ls-files | rg '(^|/)\.env$|profile\.yaml$|applications\.xlsx$|seen_roles\.json$|runs\.jsonl$|feedback\.md$|debug_email\.txt$'
```

That command should print nothing.

