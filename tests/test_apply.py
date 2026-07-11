import json
from pathlib import Path


def _record(root: Path, *, role_hash="role-1", clean_resume="resume.docx") -> Path:
    folder = root / "resume_tailor" / "20260710" / "acme_pm"
    folder.mkdir(parents=True)
    resume = folder / clean_resume
    resume.write_bytes(b"docx")
    record = folder / "tailoring_record.json"
    record.write_text(json.dumps({
        "role": {
            "role_hash": role_hash,
            "title": "Principal Product Manager",
            "company": "Acme",
            "url": "https://boards.greenhouse.io/acme/jobs/123",
            "eval": {"score": 88, "verdict": "apply"},
        },
        "artifacts": {"clean_resume": str(resume)},
        "plan": {"employer_lens": {"fit_assessment": "recommended"}},
    }), encoding="utf-8")
    return record


def test_application_state_round_trip(tmp_path):
    from metis.application_state import load_application_state, update_application_state

    update_application_state("abc", status="prefilled", root=tmp_path, ats="greenhouse")

    assert load_application_state(tmp_path)["abc"]["status"] == "prefilled"
    assert load_application_state(tmp_path)["abc"]["ats"] == "greenhouse"
    assert (tmp_path / "application_state.json").stat().st_mode & 0o777 == 0o600


def test_application_profile_round_trip(tmp_path):
    from metis.application_profile import load_application_profile, save_application_profile

    path = save_application_profile({"location": "Redmond, WA", "race": "East Asian"}, tmp_path)

    assert load_application_profile(tmp_path)["location"] == "Redmond, WA"
    assert path.stat().st_mode & 0o777 == 0o600


def test_load_candidates_uses_tailored_resume_and_hides_applied(tmp_path):
    from metis.application_state import update_application_state
    from metis.apply_cmd import load_application_candidates

    _record(tmp_path)
    candidates = load_application_candidates(tmp_path)
    assert len(candidates) == 1
    assert candidates[0].tailored is True

    update_application_state("role-1", status="applied", root=tmp_path)
    assert load_application_candidates(tmp_path) == []
    assert len(load_application_candidates(tmp_path, include_applied=True)) == 1


def test_load_candidates_includes_recommended_role_with_default_resume(tmp_path, monkeypatch):
    from metis import apply_cmd

    fallback = tmp_path / "default.docx"
    fallback.write_bytes(b"docx")
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "role_hash": "default-role",
        "title": "Senior Product Manager",
        "company": "Default Co",
        "url": "https://jobs.ashbyhq.com/default/1",
        "eval": {"score": 80, "verdict": "consider"},
    }), encoding="utf-8")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", runs)
    monkeypatch.setenv("METIS_DEFAULT_RESUME", str(fallback))

    candidates = apply_cmd.load_application_candidates(tmp_path)

    assert len(candidates) == 1
    assert candidates[0].tailored is False
    assert candidates[0].resume_path == fallback


def test_load_candidates_uses_all_run_dates_not_only_latest(tmp_path, monkeypatch):
    from metis import apply_cmd

    fallback = tmp_path / "default.docx"
    fallback.write_bytes(b"docx")
    runs = tmp_path / "runs.jsonl"
    runs.write_text("\n".join([
        json.dumps({"ts": "2026-07-01", "role_hash": "older", "title": "Staff PM", "company": "Older", "url": "https://jobs.ashbyhq.com/older/1", "eval": {"score": 90, "verdict": "apply"}}),
        json.dumps({"ts": "2026-07-10", "role_hash": "newer", "title": "Senior PM", "company": "Newer", "url": "https://jobs.ashbyhq.com/newer/1", "eval": {"score": 60, "verdict": "consider"}}),
    ]), encoding="utf-8")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", runs)
    monkeypatch.setenv("METIS_DEFAULT_RESUME", str(fallback))

    candidates = apply_cmd.load_application_candidates(tmp_path)

    assert [item.role_key for item in candidates] == ["older", "newer"]


def test_load_candidates_excludes_tracker_applied_role(tmp_path, monkeypatch):
    import openpyxl
    from metis import apply_cmd

    fallback = tmp_path / "default.docx"
    fallback.write_bytes(b"docx")
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({"role_hash": "tracked", "title": "Staff PM", "company": "Acme", "url": "https://jobs.ashbyhq.com/acme/1", "eval": {"score": 90, "verdict": "apply"}}), encoding="utf-8")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["date_suggested", "role_title", "company", "match_score", "suggestion_status", "action_taken"])
    sheet.append(["2026-07-01", "Staff PM", "Acme", 0.9, "Solid Match", "Applied"])
    workbook.save(tmp_path / "applications.xlsx")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", runs)
    monkeypatch.setenv("METIS_DEFAULT_RESUME", str(fallback))

    assert apply_cmd.load_application_candidates(tmp_path) == []


def test_load_candidates_includes_resolved_tracker_role(tmp_path, monkeypatch):
    import openpyxl
    from metis import apply_cmd
    from metis.application_state import update_application_state
    from metis.state import _role_hash

    fallback = tmp_path / "default.docx"
    fallback.write_bytes(b"docx")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["date_suggested", "role_title", "company", "match_score", "suggestion_status", "action_taken"])
    sheet.append(["2026-07-01", "Principal PM", "Acme", 0.88, "Solid Match", "Not Applied"])
    workbook.save(tmp_path / "applications.xlsx")
    role_key = _role_hash("Principal PM", "Acme")
    update_application_state(role_key, status="blocked", root=tmp_path, application_url="https://jobs.ashbyhq.com/acme/1")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", tmp_path / "missing.jsonl")
    monkeypatch.setenv("METIS_DEFAULT_RESUME", str(fallback))

    candidates = apply_cmd.load_application_candidates(tmp_path)

    assert len(candidates) == 1
    assert candidates[0].role_key == role_key


def test_load_candidates_uses_saved_application_url(tmp_path, monkeypatch):
    from metis import apply_cmd
    from metis.application_state import update_application_state

    fallback = tmp_path / "default.docx"
    fallback.write_bytes(b"docx")
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "role_hash": "resolved-role",
        "title": "Senior Product Manager",
        "company": "Aggregator",
        "url": "https://www.linkedin.com/jobs/view/123/",
        "eval": {"score": 80, "verdict": "apply"},
    }), encoding="utf-8")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", runs)
    monkeypatch.setenv("METIS_DEFAULT_RESUME", str(fallback))
    update_application_state(
        "resolved-role", status="blocked", root=tmp_path,
        application_url="https://jobs.ashbyhq.com/employer/abc",
        application_company="Actual Employer",
    )

    candidate = apply_cmd.load_application_candidates(tmp_path)[0]

    assert apply_cmd._start_url(candidate.role) == "https://jobs.ashbyhq.com/employer/abc/application"
    assert candidate.role["company"] == "Actual Employer"
    assert candidate.role["source_company"] == "Aggregator"


def test_detect_supported_ats():
    from metis.apply_cmd import detect_ats

    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert detect_ats("https://jobs.ashbyhq.com/acme/1") == "ashby"
    assert detect_ats("https://jobs.lever.co/acme/1") == "lever"
    assert detect_ats("https://example.com/jobs/1") is None


def test_start_url_prefers_direct_application_url():
    from metis.apply_cmd import _start_url

    assert _start_url({"url": "https://linkedin.example/job", "apply_url": "https://jobs.lever.co/acme/1"}) == "https://jobs.lever.co/acme/1"


def test_start_url_opens_ashby_application_route():
    from metis.apply_cmd import _start_url

    assert _start_url({"apply_url": "https://jobs.ashbyhq.com/acme/abc/"}) == "https://jobs.ashbyhq.com/acme/abc/application"
    assert _start_url({"apply_url": "https://jobs.ashbyhq.com/acme/abc/application"}) == "https://jobs.ashbyhq.com/acme/abc/application"


def test_submission_success_detection():
    from metis.apply_cmd import _looks_submitted

    assert _looks_submitted("https://example.com/confirmation", "")
    assert _looks_submitted("https://example.com/jobs", "Thanks for your application")
    assert not _looks_submitted("https://example.com/apply", "Submit application")


def test_candidate_values_prefer_env(monkeypatch):
    from metis import apply_cmd

    monkeypatch.setattr(apply_cmd, "load_profile_yaml", lambda: {"candidate": {"name": "Profile Person", "location": "Seattle"}})
    monkeypatch.setenv("METIS_FIRST_NAME", "Env")
    monkeypatch.setenv("METIS_LAST_NAME", "Person")
    monkeypatch.setenv("METIS_LOCATION", "Redmond, WA")
    monkeypatch.setenv("METIS_HISPANIC_LATINO", "no")
    monkeypatch.setenv("METIS_RACE", "East Asian")
    monkeypatch.setenv("METIS_VETERAN_STATUS", "no")

    values = apply_cmd._candidate_values()

    assert values["first_name"] == "Env"
    assert values["last_name"] == "Person"
    assert values["location"] == "Redmond, WA"
    assert values["referral_source"] == "LinkedIn"
    assert values["hispanic_latino"] == "no"
    assert values["race"] == "East Asian"
    assert values["veteran_status"] == "no"


def test_cli_apply_routes(monkeypatch):
    from metis import cli
    import metis.apply_cmd as apply_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(apply_cmd, "run_apply", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["apply", "--top", "2"])

    assert calls["top_n"] == 2
