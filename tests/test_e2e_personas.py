"""Bounded mocked end-to-end tests for the public pipeline.

These tests exercise the real run_pipeline orchestration against example
persona profiles without touching Gmail, Anthropic, SMTP, tracker files, or
the user's ~/.job_pipeline state.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest


ROOT = Path(__file__).resolve().parents[1]

PERSONA_PROFILES = [
    ROOT / "examples" / "personas" / "profile_pm_sarah.yaml",
    ROOT / "examples" / "profile_product_designer.yaml",
    ROOT / "examples" / "profile_software_engineer.yaml",
]


def _make_linkedin_thread(n_jobs: int = 25) -> dict:
    blocks = ['Your job alert for "product manager"']
    for i in range(n_jobs):
        job_id = 4_100_000_000 + i
        blocks.extend([
            "",
            f"Staff Product Manager {i}",
            f"Acme {i}",
            "Remote",
            f"View job: https://www.linkedin.com/comm/jobs/view/{job_id}/TRACK/?trackingId=t",
        ])
    return {
        "msg_id": "mock-linkedin-alert",
        "subject": '"product manager": 25 new jobs',
        "body": "\n".join(blocks),
        "html": "",
        "email_date": datetime.datetime(2026, 6, 29, 9, 0).isoformat(),
    }


def _attach_mock_evals(jobs: list[dict]) -> list[dict]:
    for i, job in enumerate(jobs):
        score = 82 if i < 3 else 64 if i < 8 else 42
        verdict = "apply" if score >= 75 else "consider" if score >= 55 else "skipped"
        job["eval"] = {
            "score": score,
            "verdict": verdict,
            "dimensions": [],
            "leveragePoints": [
                "Role aligns with the persona target and includes relevant ownership.",
                "Company context gives enough signal for a useful first-pass evaluation.",
            ],
            "frictionPoints": ["Mocked e2e role lacks full live-market context."],
            "tags": [{"text": "mock-e2e", "sentiment": "green"}],
            "gate": None,
            "summary": None,
        }
    return jobs


@pytest.mark.parametrize("profile_path", PERSONA_PROFILES, ids=lambda p: p.stem)
def test_bounded_mocked_pipeline_e2e_for_personas(profile_path, monkeypatch):
    """Run the pipeline with 25 found roles, MAX_JOBS_PER_RUN=20, and no real I/O."""
    assert profile_path.exists(), f"missing example profile: {profile_path}"

    import metis.pipeline as pipeline
    import metis.profile as profile_mod

    scored_batches: list[int] = []

    def fake_enrich(jobs):
        for job in jobs:
            job["jd"] = (
                f"{job['title']} at {job['company']} owns roadmap, customer discovery, "
                "cross-functional execution, and launch quality for a relevant product area."
            )
        return jobs

    def fake_score_jobs(_client, jobs, _profile_data):
        scored_batches.append(len(jobs))
        return _attach_mock_evals(jobs)

    monkeypatch.setattr(profile_mod, "YAML_PATH", profile_path)
    monkeypatch.setattr(pipeline, "MAX_JOBS_PER_RUN", 20)
    monkeypatch.setattr(pipeline, "fetch_alerts", lambda *_args, **_kwargs: [_make_linkedin_thread()])
    monkeypatch.setattr(pipeline, "load_seen_roles", lambda: set())
    monkeypatch.setattr(pipeline, "load_role_queue", lambda: [])
    monkeypatch.setattr(pipeline, "save_role_queue", MagicMock())
    monkeypatch.setattr(pipeline, "save_seen_roles", MagicMock())
    monkeypatch.setattr(pipeline, "save_skipped_roles", MagicMock())
    monkeypatch.setattr(pipeline, "save_last_run", MagicMock())
    monkeypatch.setattr(pipeline, "render_html", MagicMock(return_value="<html>digest</html>"))
    monkeypatch.setattr(pipeline, "send_digest", MagicMock())
    monkeypatch.setattr(pipeline.anthropic, "Anthropic", MagicMock(return_value=MagicMock()))

    monkeypatch.setattr("metis.score.prescreen_jobs_batch", lambda _client, jobs: jobs)
    monkeypatch.setattr("metis.sources.linkedin.enrich_jobs", fake_enrich)
    monkeypatch.setattr(
        "metis.extract.extract_jd_structs",
        lambda _client, jobs: [{"jd_quality": "complete"} for _ in jobs],
    )
    monkeypatch.setattr(pipeline, "score_jobs_batch", fake_score_jobs)
    monkeypatch.setattr("metis.trace.write_trace", MagicMock())
    monkeypatch.setattr("metis.xlsx.write_to_tracker", MagicMock())

    pipeline.run_pipeline(
        since_dt=datetime.datetime(2026, 6, 28),
        score_all=False,
        dry_run=True,
    )

    assert scored_batches == [20]
    pipeline.render_html.assert_called_once()
    pipeline.send_digest.assert_not_called()
    pipeline.save_role_queue.assert_not_called()
    pipeline.save_seen_roles.assert_not_called()
    pipeline.save_skipped_roles.assert_not_called()
    pipeline.save_last_run.assert_not_called()
