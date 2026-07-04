from __future__ import annotations

import json
import tempfile
from pathlib import Path

from openpyxl import Workbook

from metis.services import (
    generate_progress_summary,
    get_metis_status,
    get_role_details,
    list_application_activity,
    list_recommended_roles,
    list_scoring_feedback,
    run_job_search,
    track_applications,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")


def _write_tracker(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append([
        "date_suggested",
        "role_title",
        "company",
        "match_score",
        "suggestion_status",
        "action_taken",
        "date_applied",
        "application_status",
        "notes",
    ])
    ws.append([
        "2026-07-02",
        "Product Manager, AI Platform",
        "Northstar Labs",
        91,
        "Solid Match",
        "Applied",
        "2026-07-03",
        "Pending",
        "Warm intro requested",
    ])
    wb.save(path)


def _seed_demo_state(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "profile.yaml").write_text(
        "candidate:\n"
        "  name: Demo User\n"
        "target:\n"
        "  roles:\n"
        "    - Product Manager\n",
        encoding="utf-8",
    )
    (data_dir / "email_provider.json").write_text(
        json.dumps({"provider": "gmail_oauth"}),
        encoding="utf-8",
    )
    (data_dir / "gmail_token.json").write_text("{}", encoding="utf-8")
    (data_dir / "last_run.json").write_text(
        json.dumps({
            "run_date": "July 3, 2026",
            "total_evaluated": 3,
            "apply_count": 1,
            "consider_count": 1,
            "skipped_count": 1,
        }),
        encoding="utf-8",
    )
    _write_jsonl(
        data_dir / "runs.jsonl",
        [
            {
                "ts": "2026-07-03T09:00:00",
                "role_hash": "role_solid",
                "title": "Product Manager, AI Platform",
                "company": "Northstar Labs",
                "location": "San Francisco, CA",
                "source": "proactive",
                "url": "https://example.com/northstar",
                "eval": {
                    "verdict": "apply",
                    "score": 91,
                    "leveragePoints": [
                        "Platform role matches recent AI product leadership.",
                        "Company stage fits growth preference.",
                    ],
                    "frictionPoints": ["Hybrid schedule may need confirmation."],
                    "tags": [{"text": "AI platform", "sentiment": "green"}],
                },
            },
            {
                "ts": "2026-07-03T09:05:00",
                "role_hash": "role_moderate",
                "title": "Senior Product Manager",
                "company": "Harbor Analytics",
                "location": "Remote",
                "source": "linkedin",
                "eval": {
                    "verdict": "consider",
                    "score": 74,
                    "leveragePoints": [
                        "Customer analytics aligns with prior domain exposure.",
                        "Remote role fits location preference.",
                    ],
                    "frictionPoints": ["Domain appears less AI-native."],
                    "tags": [{"text": "Remote", "sentiment": "green"}],
                },
            },
            {
                "ts": "2026-07-03T09:10:00",
                "role_hash": "role_skip",
                "title": "Sales Operations Manager",
                "company": "Copper CRM",
                "location": "New York, NY",
                "source": "linkedin",
                "eval": {
                    "verdict": "skipped",
                    "score": 40,
                    "frictionPoints": ["Function is outside target PM track."],
                },
            },
        ],
    )
    (data_dir / "feedback.md").write_text(
        "# Scoring Feedback\n\n"
        "<!-- id:fb_demo | run:July_3_2026 | roles:northstar | dims:domain_background -->\n"
        "## [user] 2026-07-03\n\n"
        "Score AI platform roles higher when they involve product strategy and customer discovery.\n",
        encoding="utf-8",
    )
    _write_tracker(data_dir / "applications.xlsx")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="metis-mcp-smoke-") as tmp:
        data_dir = Path(tmp)
        _seed_demo_state(data_dir)
        env = {
            "ANTHROPIC_API_KEY": "demo-key",
            "METIS_LLM_PROVIDER": "anthropic",
        }

        status = get_metis_status(data_dir=data_dir, env=env)
        roles = list_recommended_roles(data_dir=data_dir)
        role = get_role_details("role_solid", data_dir=data_dir)
        feedback = list_scoring_feedback(data_dir=data_dir)
        activity = list_application_activity(data_dir=data_dir / "missing", tracker_path=data_dir / "applications.xlsx")
        progress = generate_progress_summary(data_dir=data_dir, tracker_path=data_dir / "applications.xlsx")
        job_search = run_job_search(data_dir=data_dir, dry_run=True, env={})
        tracking = track_applications(data_dir=data_dir, dry_run=True, env=env)

        print(json.dumps({
            "mode": "offline_synthetic_demo",
            "warning": (
                "This is seeded fake data for developer smoke testing. "
                "It does not read your real Metis profile, schedule, feedback, runs, or tracker."
            ),
            "data_dir": str(data_dir),
            "status": status,
            "recommended_roles": roles,
            "role_details": role,
            "scoring_feedback": feedback,
            "application_activity": activity,
            "progress_summary": progress,
            "job_search_preview": job_search,
            "tracking_preview": tracking,
        }, indent=2))


if __name__ == "__main__":
    main()
