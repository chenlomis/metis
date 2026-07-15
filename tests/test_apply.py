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


def test_confirmation_email_reconciles_browser_state(tmp_path):
    from metis.application_state import (
        load_application_state, reconcile_application_event, update_application_state,
    )

    update_application_state(
        "role-1", status="prefilled", root=tmp_path,
        role={"company": "Microsoft", "title": "Principal Product Manager - Microsoft Discovery"},
    )
    matched = reconcile_application_event(
        "Microsoft Careers", "Principal Product Manager - Microsoft Discovery",
        "confirmation", event_date="2026-07-12", root=tmp_path,
    )

    assert matched == "role-1"
    entry = load_application_state(tmp_path)["role-1"]
    assert entry["status"] == "applied_confirmed"
    assert entry["email_evidence"]["classification"] == "confirmation"


def test_confirmation_reconciles_company_alias_and_ats_id_suffix(tmp_path):
    from metis.application_state import (
        load_application_state, reconcile_application_event, update_application_state,
    )

    title = "Principal PMT - Personalization ML Platform, Prime Video Personalization & Discovery"
    update_application_state(
        "prime-role", status="prefilled", root=tmp_path,
        role={"company": "Prime Video & Amazon MGM Studios", "title": title},
    )
    matched = reconcile_application_event(
        "Amazon", f"{title} (ID: 10451506)", "confirmation",
        event_date="2026-07-12", root=tmp_path,
    )

    assert matched == "prime-role"
    assert load_application_state(tmp_path)["prime-role"]["status"] == "applied_confirmed"


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


def test_load_candidates_keeps_prefilled_and_needs_review_roles(tmp_path):
    from metis.application_state import update_application_state
    from metis.apply_cmd import load_application_candidates

    _record(tmp_path, role_hash="prepared")
    update_application_state("prepared", status="prefilled", root=tmp_path)
    candidate = load_application_candidates(tmp_path)[0]
    assert candidate.workflow_status == "prefilled"

    update_application_state("prepared", status="needs_review", root=tmp_path)
    candidate = load_application_candidates(tmp_path)[0]
    assert candidate.workflow_status == "needs_review"


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


def test_load_candidates_includes_unresolved_linkedin_role(tmp_path, monkeypatch):
    from metis import apply_cmd

    fallback = tmp_path / "default.docx"
    fallback.write_bytes(b"docx")
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "role_hash": "unresolved", "title": "Staff Product Manager", "company": "Acme",
        "url": "https://www.linkedin.com/jobs/view/123/", "apply_mode": "offsite",
        "eval": {"score": 88, "verdict": "apply"},
    }), encoding="utf-8")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", runs)
    monkeypatch.setenv("METIS_DEFAULT_RESUME", str(fallback))

    candidate = apply_cmd.load_application_candidates(tmp_path)[0]

    assert candidate.role_key == "unresolved"


def test_search_queries_are_bounded_and_search_redirect_is_unwrapped():
    from metis.apply_cmd import _is_external_job_url, _search_queries, _search_result_url

    role = {"title": "Senior Staff Product Manager", "company": "Acme"}
    assert len(_search_queries(role)) == 2
    assert _search_result_url("/url?q=https%3A%2F%2Fjobs.ashbyhq.com%2Facme%2F1&sa=U") == "https://jobs.ashbyhq.com/acme/1"
    assert _is_external_job_url("https://careers.microsoft.com/us/en/job/123/title")
    assert _is_external_job_url("https://snowflake.wd5.myworkdayjobs.com/en-US/SnowflakeCareers/job/123")
    assert not _is_external_job_url("https://www.linkedin.com/jobs/view/123")
    assert not _is_external_job_url("https://www.indeed.com/viewjob?jk=123")



def test_linkedin_apply_mode_detection():
    from metis.sources.linkedin import _apply_mode_from_html

    assert _apply_mode_from_html('<icon class="apply-button__offsite-apply-icon-svg">') == "offsite"
    assert _apply_mode_from_html('<button>Easy Apply</button>') == "easy_apply"
    assert _apply_mode_from_html("<p>Apply</p>") == "unknown"


def test_linkedin_apply_selector_handles_authenticated_accessible_name():
    """LinkedIn labels offsite controls with the full role, not just 'Apply'."""
    from metis.apply_cmd import _find_linkedin_apply_control

    class Locator:
        def __init__(self, items=()): self.items = list(items)
        def count(self): return len(self.items)
        def nth(self, index): return self.items[index]

    class Control:
        def is_visible(self): return True
        def get_attribute(self, name):
            return "Apply to Principal Product Manager on company website" if name == "aria-label" else None

    control = Control()

    class Page:
        def locator(self, selector):
            return Locator([control]) if selector == "button.jobs-apply-button" else Locator()
        def get_by_role(self, *args, **kwargs): return Locator()
        def wait_for_timeout(self, _milliseconds): pass

    found = _find_linkedin_apply_control(Page(), timeout_seconds=0.1)
    assert found is control
    assert found.get_attribute("aria-label") == "Apply to Principal Product Manager on company website"


def test_browser_launch_injects_linkedin_cookie(monkeypatch):
    from metis.apply_cmd import _launch_browser_context

    monkeypatch.setenv("LINKEDIN_COOKIE", "test-li-at-value")
    injected_cookies = []

    class Context:
        pages = []
        def add_cookies(self, cookies): injected_cookies.extend(cookies)

    class Browser:
        def new_context(self, **kwargs): return Context()

    class Chromium:
        def launch(self, **kwargs): return Browser()

    class Playwright:
        chromium = Chromium()

    ctx = _launch_browser_context(Playwright(), headless=False)
    assert isinstance(ctx, Context)
    assert any(c.get("name") == "li_at" and c.get("value") == "test-li-at-value" for c in injected_cookies)


def test_linkedin_session_uses_auth_cookie_not_page_copy():
    from metis.apply_cmd import _has_linkedin_session

    class Context:
        def __init__(self, cookies): self._cookies = cookies
        def cookies(self, _url): return self._cookies

    assert _has_linkedin_session(Context([{"name": "li_at", "value": "encrypted-session"}]))
    assert not _has_linkedin_session(Context([{"name": "JSESSIONID", "value": "ajax"}]))


def test_linkedin_session_probe_accepts_functional_feed_when_cookie_is_hidden():
    from metis.apply_cmd import _probe_linkedin_session

    class Context:
        def cookies(self, _url): return []

    class Page:
        url = "about:blank"
        def goto(self, url, **kwargs): self.url = "https://www.linkedin.com/feed/"
        def wait_for_timeout(self, _milliseconds): pass

    assert _probe_linkedin_session(Context(), Page())


def test_apply_diagnostic_is_privacy_safe_and_owner_only(tmp_path):
    import json
    from metis.apply_cmd import ApplicationCandidate, _write_apply_diagnostic

    resume = tmp_path / "resume.docx"
    resume.write_bytes(b"docx")
    candidate = ApplicationCandidate(
        "role-1", {"company": "Acme", "title": "Staff PM", "apply_mode": "offsite"},
        None, resume, False,
    )
    _write_apply_diagnostic(tmp_path, candidate, phase="linkedin_routing", error="missing apply")

    path = tmp_path / "apply_diagnostics.jsonl"
    record = json.loads(path.read_text())
    assert record["phase"] == "linkedin_routing"
    assert "html" not in record and "form_values" not in record
    assert path.stat().st_mode & 0o777 == 0o600


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


def test_recent_evaluation_metadata_overlays_older_tailoring_record(tmp_path, monkeypatch):
    from metis import apply_cmd

    _record(tmp_path, role_hash="role-1")
    runs = tmp_path / "runs.jsonl"
    runs.write_text(json.dumps({
        "ts": "2026-07-11", "role_hash": "role-1", "title": "Principal Product Manager",
        "company": "Acme", "url": "https://boards.greenhouse.io/acme/jobs/123",
        "eval": {"score": 94, "verdict": "apply"},
    }), encoding="utf-8")
    monkeypatch.setattr(apply_cmd, "RUNS_PATH", runs)

    candidate = apply_cmd.load_application_candidates(tmp_path)[0]

    assert candidate.tailored is True
    assert candidate.role["ts"] == "2026-07-11"
    assert candidate.role["eval"]["score"] == 94


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
    monkeypatch.setenv("METIS_SEXUAL_ORIENTATION", "Heterosexual")

    values = apply_cmd._candidate_values()

    assert values["first_name"] == "Env"
    assert values["last_name"] == "Person"
    assert values["location"] == "Redmond, WA"
    assert values["referral_source"] == "LinkedIn"
    assert values["hispanic_latino"] == "no"
    assert values["race"] == "East Asian"
    assert values["veteran_status"] == "no"
    assert values["sexual_orientation"] == "Heterosexual"


def test_cli_apply_routes(monkeypatch):
    from metis import cli
    import metis.apply_cmd as apply_cmd

    calls = {}
    monkeypatch.setattr(cli, "_configure_logging", lambda: None)
    monkeypatch.setattr(cli, "_validate_env", lambda require_gmail=True: None)
    monkeypatch.setattr(cli, "GMAIL_ADDRESS", "")
    monkeypatch.setattr(cli, "GMAIL_APP_PASSWORD", "")
    monkeypatch.setattr(apply_cmd, "run_apply", lambda **kwargs: calls.update(kwargs) or [])

    cli.main(["apply", "--top", "2"])

    assert calls["top_n"] == 2


def test_run_apply_latest_orders_by_evaluation_date(tmp_path, monkeypatch):
    from metis import apply_cmd

    resume = tmp_path / "default.docx"
    resume.write_bytes(b"docx")
    candidates = [
        apply_cmd.ApplicationCandidate("high-old", {"title": "Old", "company": "A", "ts": "2026-07-01", "eval": {"score": 95}}, None, resume, False),
        apply_cmd.ApplicationCandidate("low-new", {"title": "New", "company": "B", "ts": "2026-07-10", "eval": {"score": 70}}, None, resume, False),
    ]
    monkeypatch.setattr(apply_cmd, "load_application_candidates", lambda **kwargs: candidates)
    monkeypatch.setattr(apply_cmd, "prepare_batch_in_browser", lambda selected: [item.role_key for item in selected])

    result = apply_cmd.run_apply(latest_n=1)

    assert result == ["low-new"]


def test_linkedin_probe_failure_does_not_crash_direct_ats_candidates(tmp_path, monkeypatch):
    """LinkedIn session failing should not abort roles that have direct ATS URLs."""
    import metis.apply_cmd as apply_cmd

    resume = tmp_path / "resume.docx"
    resume.write_bytes(b"docx")
    direct_candidate = apply_cmd.ApplicationCandidate(
        "direct-ats", {
            "title": "Staff PM", "company": "Acme",
            "apply_url": "https://job-boards.greenhouse.io/acme/jobs/123",
            "eval": {"score": 85},
        }, None, resume, False,
    )

    calls = []

    def fake_prepare(candidates, *, root=None, headless=False):
        calls.extend(c.role_key for c in candidates)
        return [{"role": c.role_key, "status": "prefilled"} for c in candidates]

    monkeypatch.setattr(apply_cmd, "load_application_candidates", lambda **kw: [direct_candidate])
    monkeypatch.setattr(apply_cmd, "prepare_batch_in_browser", fake_prepare)

    result = apply_cmd.run_apply(apply_all=True)
    assert result[0]["status"] == "prefilled"


def test_run_apply_default_resume_overrides_tailored_resume(tmp_path, monkeypatch):
    from metis import apply_cmd

    default = tmp_path / "default.docx"
    tailored = tmp_path / "tailored.docx"
    default.write_bytes(b"docx")
    tailored.write_bytes(b"docx")
    candidate = apply_cmd.ApplicationCandidate(
        "role", {"title": "PM", "company": "A", "eval": {"score": 90}}, None, tailored, True, "tailored",
    )
    monkeypatch.setattr(apply_cmd, "_fallback_resume", lambda: default)
    monkeypatch.setattr(apply_cmd, "load_application_candidates", lambda **kwargs: [candidate])
    monkeypatch.setattr(apply_cmd, "prepare_batch_in_browser", lambda selected: selected)

    selected = apply_cmd.run_apply(top_n=1, force_default_resume=True)

    assert selected[0].resume_path == default
    assert selected[0].resume_kind == "default"
    assert selected[0].tailored is False


def test_search_result_url_handles_ddg_redirect():
    from metis.apply_cmd import _search_result_url

    # DDG HTML wraps result links as protocol-relative redirect URLs
    ddg_href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fcareers.microsoft.com%2Fus%2Fen%2Fjob%2F123&rut=abc"
    assert _search_result_url(ddg_href) == "https://careers.microsoft.com/us/en/job/123"

    # Google /url? pattern still works
    google_href = "/url?q=https%3A%2F%2Fjobs.airbnb.com%2F123&sa=U"
    assert _search_result_url(google_href) == "https://jobs.airbnb.com/123"

    # Plain https URL passes through
    assert _search_result_url("https://boards.greenhouse.io/acme/jobs/1") == "https://boards.greenhouse.io/acme/jobs/1"

    # Protocol-relative non-DDG URL is dropped
    assert _search_result_url("//example.com/job") == ""
