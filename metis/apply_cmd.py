from __future__ import annotations

import datetime as dt
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .application_state import data_dir, load_application_state, update_application_state
from .application_profile import application_value, load_application_profile
from .profile import load_profile_yaml
from .state import RUNS_PATH, _role_hash


_SELECT_ALL = "__metis_select_all__"
_CANCEL = "__metis_cancel__"
_FINAL_STATUSES = {"prefilled", "needs_review", "applied", "applied_confirmed", "rejected", "recruiter_screen"}
_SUCCESS_PATTERNS = (
    re.compile(r"application (?:has been )?submitted", re.I),
    re.compile(r"thanks? for (?:your )?application", re.I),
    re.compile(r"we(?:'|’)ve received your application", re.I),
    re.compile(r"application received", re.I),
)


@dataclass(frozen=True)
class ApplicationCandidate:
    role_key: str
    role: dict[str, Any]
    record_path: Path | None
    resume_path: Path
    tailored: bool


def _role_key(role: dict[str, Any]) -> str:
    return str(role.get("role_hash") or _role_hash(role.get("title", ""), role.get("company", "")))


def detect_ats(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if "greenhouse.io" in host:
        return "greenhouse"
    if "ashbyhq.com" in host:
        return "ashby"
    if host == "lever.co" or host.endswith(".lever.co"):
        return "lever"
    return None


def _start_url(role: dict[str, Any]) -> str:
    url = str(role.get("apply_url") or role.get("url") or "")
    if detect_ats(url) == "ashby":
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        if not path.endswith("/application"):
            path += "/application"
        return parsed._replace(path=path).geturl()
    return url


def _fallback_resume() -> Path | None:
    raw = application_value("default_resume")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() and path.suffix.lower() == ".docx" else None


def _is_application_ready(role: dict[str, Any]) -> bool:
    return detect_ats(_start_url(role)) in {"greenhouse", "lever", "ashby"}


def _tracker_applied_roles(root: Path) -> set[tuple[str, str]]:
    """Return normalized tracker roles already marked Applied."""
    path = Path(os.getenv("TRACKER_PATH", str(root / "applications.xlsx")))
    if not path.exists():
        return set()
    try:
        import openpyxl

        sheet = openpyxl.load_workbook(path, read_only=True, data_only=True).active
        return {
            (_normalize_match(str(row[2] or "")), _normalize_match(str(row[1] or "")))
            for row in sheet.iter_rows(min_row=2, values_only=True)
            if len(row) >= 6 and str(row[5] or "").strip().lower() == "applied"
        }
    except Exception:
        return set()


def _tracker_pending_roles(root: Path) -> list[dict[str, Any]]:
    """Load tracker recommendations so older unapplied roles remain selectable."""
    path = Path(os.getenv("TRACKER_PATH", str(root / "applications.xlsx")))
    if not path.exists():
        return []
    try:
        import openpyxl

        sheet = openpyxl.load_workbook(path, read_only=False, data_only=False).active
        roles = []
        for row_index in range(2, sheet.max_row + 1):
            action = str(sheet.cell(row_index, 6).value or "").strip().lower()
            if action == "applied":
                continue
            title_cell = sheet.cell(row_index, 2)
            title = str(title_cell.value or "").strip()
            company = str(sheet.cell(row_index, 3).value or "").strip()
            raw_score = sheet.cell(row_index, 4).value
            score = float(raw_score or 0)
            if score <= 1:
                score *= 100
            url = title_cell.hyperlink.target if title_cell.hyperlink else ""
            if not title or not company or score < 75:
                continue
            roles.append({
                "role_hash": _role_hash(title, company), "title": title, "company": company,
                "url": url, "ts": str(sheet.cell(row_index, 1).value or ""),
                "eval": {"score": round(score), "verdict": "apply"},
            })
        return roles
    except Exception:
        return []


def _normalize_match(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def load_application_candidates(root: Path | None = None, *, include_applied: bool = False) -> list[ApplicationCandidate]:
    root = root or data_dir()
    state = load_application_state(root)
    tracker_applied = _tracker_applied_roles(root)
    fallback = _fallback_resume()
    candidates: dict[str, ApplicationCandidate] = {}
    for record_path in sorted((root / "resume_tailor").glob("*/*/tailoring_record.json")):
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        role = record.get("role") or {}
        role_key = _role_key(role)
        if (_normalize_match(str(role.get("company") or "")), _normalize_match(str(role.get("title") or ""))) in tracker_applied:
            continue
        saved = state.get(role_key) or {}
        saved_application_url = str(saved.get("application_url") or "")
        if saved_application_url:
            role = {**role, "apply_url": saved_application_url}
        application_company = str(saved.get("application_company") or "")
        if application_company and application_company != str(role.get("company") or ""):
            role = {**role, "source_company": role.get("company"), "company": application_company}
        if not _start_url(role):
            continue
        if not _is_application_ready(role):
            continue
        if not include_applied and (state.get(role_key) or {}).get("status") in _FINAL_STATUSES:
            continue
        clean = Path(str((record.get("artifacts") or {}).get("clean_resume") or "")).expanduser()
        tailored = clean.is_file() and clean.suffix.lower() == ".docx"
        resume = clean if tailored else fallback
        if resume is None:
            continue
        fit = str((((record.get("plan") or {}).get("employer_lens") or {}).get("fit_assessment") or "")).lower()
        if fit.startswith("not_recommended"):
            continue
        candidate = ApplicationCandidate(role_key, role, record_path, resume, tailored)
        previous = candidates.get(role_key)
        if previous is None or previous.record_path is None or record_path.stat().st_mtime >= previous.record_path.stat().st_mtime:
            candidates[role_key] = candidate
    if fallback and RUNS_PATH.exists():
        recent_roles: list[dict[str, Any]] = []
        for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
            try:
                role = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            if (role.get("eval") or {}).get("verdict") not in {"apply", "consider"} or not _start_url(role):
                continue
            recent_roles.append(role)
        for role in recent_roles:
            role_key = _role_key(role)
            if (_normalize_match(str(role.get("company") or "")), _normalize_match(str(role.get("title") or ""))) in tracker_applied:
                continue
            if role_key in candidates:
                continue
            if not include_applied and (state.get(role_key) or {}).get("status") in _FINAL_STATUSES:
                continue
            saved = state.get(role_key) or {}
            saved_application_url = str(saved.get("application_url") or "")
            if saved_application_url:
                role = {**role, "apply_url": saved_application_url}
            application_company = str(saved.get("application_company") or "")
            if application_company and application_company != str(role.get("company") or ""):
                role = {**role, "source_company": role.get("company"), "company": application_company}
            if not _is_application_ready(role):
                continue
            candidates[role_key] = ApplicationCandidate(role_key, role, None, fallback, False)
    if fallback:
        for role in _tracker_pending_roles(root):
            role_key = _role_key(role)
            if role_key in candidates:
                continue
            saved = state.get(role_key) or {}
            if not include_applied and saved.get("status") in _FINAL_STATUSES:
                continue
            saved_application_url = str(saved.get("application_url") or "")
            if saved_application_url:
                role = {**role, "apply_url": saved_application_url}
            if not _is_application_ready(role):
                continue
            candidates[role_key] = ApplicationCandidate(role_key, role, None, fallback, False)
    return sorted(
        candidates.values(),
        key=lambda item: (
            int((item.role.get("eval") or {}).get("score") or 0),
            item.record_path.stat().st_mtime if item.record_path else 0,
        ),
        reverse=True,
    )


def _label(candidate: ApplicationCandidate) -> str:
    role = candidate.role
    score = int((role.get("eval") or {}).get("score") or 0)
    company = " ".join(str(role.get("company") or "").split())[:22]
    title = " ".join(str(role.get("title") or "").split())[:48]
    if candidate.record_path:
        day = dt.datetime.fromtimestamp(candidate.record_path.stat().st_mtime).strftime("%b %d")
    else:
        day = str(role.get("ts") or "")[:10] or "recent"
    suffix = "tailored" if candidate.tailored else "default resume"
    return f"{score:>3}% | {company:<22} | {title:<48} | {day} · {suffix}"


def select_candidates(candidates: list[ApplicationCandidate]) -> list[ApplicationCandidate]:
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
        from InquirerPy.separator import Separator
        from .theme import INQUIRER_STYLE, console
    except Exception:
        return candidates[:1]
    console.print()
    console.print(f"[dim]Choose application-ready roles. {len(candidates)} pending, highest match first.[/dim]")
    choices = [
        Choice(_SELECT_ALL, f"Select all {len(candidates)} roles"),
        Choice(_CANCEL, "Cancel / exit"),
        Separator(),
        *[Choice(item.role_key, f"{idx:>2}. {_label(item)}") for idx, item in enumerate(candidates, 1)],
    ]
    selected = inquirer.checkbox(
        message="Applications",
        choices=choices,
        style=INQUIRER_STYLE,
        instruction="Space toggles, Enter confirms",
        validate=lambda result: bool(result),
        invalid_message="Press Space to select at least one role, or Ctrl-C to cancel.",
    ).execute()
    if _CANCEL in selected:
        raise SystemExit("Cancelled.")
    if _SELECT_ALL in selected:
        return candidates
    by_key = {item.role_key: item for item in candidates}
    return [by_key[key] for key in selected if key in by_key]


def _looks_submitted(url: str, text: str) -> bool:
    lowered_url = url.lower()
    if any(part in lowered_url for part in ("/confirmation", "/submitted", "application-success")):
        return True
    return any(pattern.search(text or "") for pattern in _SUCCESS_PATTERNS)


def _candidate_values() -> dict[str, str]:
    profile = load_profile_yaml() or {}
    candidate = profile.get("candidate") or {}
    name = str(candidate.get("name") or "").strip()
    first, _, last = name.partition(" ")
    configured = load_application_profile()
    values = {
        "first_name": os.getenv("METIS_FIRST_NAME", first).strip(),
        "last_name": os.getenv("METIS_LAST_NAME", last).strip(),
        "email": os.getenv("GMAIL_ADDRESS", str(candidate.get("email") or "")).strip(),
        "phone": os.getenv("METIS_PHONE", str(candidate.get("phone") or "")).strip(),
        "location": os.getenv("METIS_LOCATION", str(candidate.get("location") or "")).strip(),
        "linkedin": os.getenv("METIS_LINKEDIN_URL", str(candidate.get("linkedin") or "")).strip(),
        "pronouns": os.getenv("METIS_PRONOUNS", "").strip(),
        "current_employer": os.getenv("METIS_CURRENT_EMPLOYER", "").strip(),
        "github": os.getenv("METIS_GITHUB_URL", "").strip(),
        "gender_identity": os.getenv("METIS_GENDER_IDENTITY", "").strip(),
        "hispanic_latino": os.getenv("METIS_HISPANIC_LATINO", "").strip(),
        "race": os.getenv("METIS_RACE", "").strip(),
        "veteran_status": os.getenv("METIS_VETERAN_STATUS", "").strip(),
        "transgender": os.getenv("METIS_TRANSGENDER", "").strip(),
        "disability": os.getenv("METIS_DISABILITY", "").strip(),
        "work_authorized": os.getenv("METIS_WORK_AUTHORIZED", "").strip(),
        "sponsorship_required": os.getenv("METIS_SPONSORSHIP_REQUIRED", "").strip(),
        "willing_to_relocate": os.getenv("METIS_WILLING_TO_RELOCATE", "").strip(),
        "referral_source": os.getenv("METIS_REFERRAL_SOURCE", "LinkedIn").strip(),
    }
    return {key: application_value(key, str(configured.get(key, value) or "")) for key, value in values.items()}


def _fill_visible_form(page: Any, candidate: ApplicationCandidate) -> None:
    """Best-effort deterministic fields; existing browser/autofill values win."""
    page.wait_for_selector("input", state="attached", timeout=15_000)
    values = _candidate_values()
    stable = {
        'input[name*="first" i]': values["first_name"],
        'input[name*="last" i]': values["last_name"],
        "input[type=email]": values["email"],
        "input[type=tel]": values["phone"],
        'input[name*="linkedin" i]': values["linkedin"],
    }
    for selector, value in stable.items():
        if not value:
            continue
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible() and not locator.input_value().strip():
            locator.fill(value)
    resume_upload = page.get_by_label("Resume", exact=True)
    if resume_upload.count():
        resume_upload.first.set_input_files(str(candidate.resume_path))
    if detect_ats(page.url) == "ashby":
        page.wait_for_timeout(750)
        _fill_ashby_form(page, candidate, values)
    elif detect_ats(page.url) == "greenhouse":
        _fill_greenhouse_form(page, candidate, values)
    _ensure_resume_attached(page, candidate)
    # Resume uploads can rerender Ashby/Greenhouse forms and clear dependent
    # autocomplete controls. Re-run location after the final upload settles.
    page.wait_for_timeout(500)
    _fill_location_control(page, values["location"])


def _fill_empty(locator: Any, value: str) -> None:
    if not value or not locator.count():
        return
    field = locator.first
    if field.is_visible() and not field.input_value().strip():
        field.fill(value, timeout=3_000)


def _fill_ashby_form(page: Any, candidate: ApplicationCandidate, values: dict[str, str]) -> None:
    full_name = " ".join(part for part in (values["first_name"], values["last_name"]) if part).strip()
    fields = {
        r"^(?:Name|Legal Name|Legal First and Last Name|Full Name)\b": full_name,
        r"^Preferred (?:First )?Name\b": values["first_name"],
        r"^Preferred Last Name\b": values["last_name"],
        r"^Preferred Pronouns?\b": values["pronouns"],
        r"^Email\b": values["email"],
        r"^Phone Number\b": values["phone"],
        r"^LinkedIn(?: URL| Profile| Profile Link)?\b": values["linkedin"],
        r"^Current (?:or Most Recent Employer|Company)\b": values["current_employer"],
        r"^GitHub(?: URL)?\b": values["github"],
        r"^How did you hear about (?:us|this opportunity)\b": values["referral_source"],
    }
    for label, value in fields.items():
        _fill_empty(page.get_by_label(re.compile(label, re.I)), value)

    locations = [
        page.get_by_label(re.compile(r"^(?:Current )?Location(?: \(City\))?\b", re.I)),
    ]
    location = next((item for item in locations if item.count()), locations[0])
    if values["location"] and location.count() and location.first.is_visible() and not location.first.input_value().strip():
        _choose_location(page, location.first, values["location"])

    # These fixed answers were explicitly supplied by the candidate. Do not
    # infer them from scoring/profile prose.
    if values["work_authorized"].lower() in {"yes", "true", "1"}:
        _choose_yes_no(page, re.compile(r"(?:legally )?authorized to work", re.I), yes=True)
    no_sponsorship = page.get_by_label(re.compile(r"No, I do not require sponsorship", re.I))
    if values["sponsorship_required"].lower() in {"no", "false", "0"} and no_sponsorship.count():
        try:
            no_sponsorship.first.check(timeout=3_000)
        except Exception:
            pass
    relocate = page.get_by_label(re.compile(r"not based in this location but willing to relocate", re.I))
    if values["willing_to_relocate"].lower() in {"yes", "true", "1"} and relocate.count():
        try:
            relocate.first.check(timeout=3_000)
        except Exception:
            pass
    _choose_custom_select(page, re.compile(r"^(?:Preferred )?Pronouns?\b", re.I), values["pronouns"])
    _choose_radio_group(page, re.compile(r"transgender", re.I), re.compile(r"^No$", re.I) if values["transgender"].lower() == "no" else re.compile(re.escape(values["transgender"]), re.I))
    _choose_radio_group(page, re.compile(r"gender identity", re.I), re.compile(r"woman|female", re.I) if values["gender_identity"] else None)
    _choose_radio_group(page, re.compile(r"disabil", re.I), re.compile(r"no,? i do not|^no$", re.I) if values["disability"].lower() == "no" else None)
    if values["willing_to_relocate"].lower() in {"yes", "true", "1"}:
        _choose_radio_group(page, re.compile(r"located or willing to relocate", re.I), re.compile(r"planning to relocate", re.I))
    if values["sponsorship_required"].lower() in {"no", "false", "0"}:
        _choose_yes_no(page, re.compile(r"require visa sponsorship", re.I), yes=False)
    _fill_or_select(page, re.compile(r"how did you hear", re.I), values["referral_source"])
    _answer_demographics_and_eligibility(page, values)


def _fill_greenhouse_form(page: Any, candidate: ApplicationCandidate, values: dict[str, str]) -> None:
    _fill_empty(page.locator("#first_name"), values["first_name"])
    _fill_empty(page.locator("#last_name"), values["last_name"])
    _fill_empty(page.locator("#preferred_name"), values["first_name"])
    _fill_empty(page.locator("#email"), values["email"])
    _fill_empty(page.locator("#phone"), values["phone"])
    resume = page.locator('input[type="file"]#resume')
    if resume.count():
        resume.first.set_input_files(str(candidate.resume_path))
    _fill_empty(page.get_by_label(re.compile(r"LinkedIn Profile", re.I)), values["linkedin"])
    full_name = " ".join(part for part in (values["first_name"], values["last_name"]) if part).strip()
    aliases = [
        (r"^(?:Full|Legal) Name\b", full_name),
        (r"^First Name\b", values["first_name"]), (r"^Last Name\b", values["last_name"]),
        (r"^Preferred (?:First )?Name\b", values["first_name"]),
        (r"^(?:Preferred )?Pronouns?\b", values["pronouns"]),
        (r"^Email\b", values["email"]), (r"^Phone(?: Number)?\b", values["phone"]),
        (r"^LinkedIn(?: URL| Profile)?\b", values["linkedin"]),
        (r"^Current (?:Company|Employer)\b", values["current_employer"]),
        (r"^GitHub(?: URL)?\b", values["github"]),
    ]
    for label, value in aliases:
        _fill_empty(page.get_by_label(re.compile(label, re.I)), value)
    _choose_labeled_select(page, re.compile(r"^Country", re.I), "United States")
    if values["work_authorized"].lower() in {"yes", "true", "1"}:
        _choose_labeled_select(page, re.compile(r"legally authorized to work", re.I), "Yes")
    if values["sponsorship_required"].lower() in {"no", "false", "0"}:
        _choose_labeled_select(page, re.compile(r"require sponsorship", re.I), "No")
    _fill_or_select(page, re.compile(r"how did you hear", re.I), values["referral_source"])
    location = page.get_by_label(re.compile(r"^(?:Current )?Location(?: \(City\))?\b", re.I))
    if values["location"] and location.count() and not _locator_has_value(location):
        _choose_location(page, location.first, values["location"])
    _answer_demographics_and_eligibility(page, values)


def _fill_location_control(page: Any, value: str) -> None:
    if not value:
        return
    location = page.get_by_label(re.compile(r"^(?:Current )?Location(?: \(City\))?\b", re.I))
    if location.count() and not _locator_has_value(location):
        _choose_location(page, location.first, value)


def _ensure_resume_attached(page: Any, candidate: ApplicationCandidate) -> None:
    candidates = [
        page.get_by_label("Resume", exact=True),
        page.get_by_label(re.compile(r"^Resume/CV", re.I)),
        page.locator('input[type="file"]#resume'),
    ]
    for locator in candidates:
        if not locator.count():
            continue
        field = locator.first
        try:
            if not field.evaluate("e => e.files && e.files.length"):
                field.set_input_files(str(candidate.resume_path))
            return
        except Exception:
            continue


def _fill_or_select(page: Any, label_pattern: re.Pattern[str], value: str) -> None:
    if not value:
        return
    control = page.get_by_label(label_pattern)
    if not control.count():
        return
    field = control.first
    try:
        tag = field.evaluate("e => e.tagName.toLowerCase()")
        if tag == "select":
            labels = field.locator("option").all_text_contents()
            match = next((label for label in labels if value.lower() in label.lower()), None)
            if match:
                field.select_option(label=match)
            return
        if tag in {"input", "textarea"}:
            is_combobox = (field.get_attribute("role") or "").lower() == "combobox"
            if is_combobox:
                field.fill("", timeout=3_000)
                field.fill(value, timeout=3_000)
            elif not field.input_value().strip():
                field.fill(value, timeout=3_000)
            page.wait_for_timeout(250)
            options = page.locator('[role="option"]:visible')
            if options.count():
                preferred = options.filter(has_text=re.compile(re.escape(value), re.I))
                if preferred.count():
                    preferred.first.click(timeout=3_000)
            return
        field.click(timeout=3_000)
        option = page.get_by_role("option", name=re.compile(re.escape(value), re.I))
        if option.count():
            option.first.click(timeout=3_000)
    except Exception:
        pass


def _answer_known_choice(page: Any, question: str, answers: str | list[str]) -> None:
    """Answer a supplied fixed-value question across text, native, and custom controls."""
    if isinstance(answers, str):
        answers = [answers]
    answers = [answer for answer in answers if answer]
    if not answers:
        return
    pattern = re.compile(question, re.I)
    for answer in answers:
        _fill_or_select(page, pattern, answer)
        _choose_radio_group(page, pattern, re.compile(rf"^{re.escape(answer)}(?:\b|$)", re.I))
        if _question_is_answered(page, pattern):
            return
    if answers[0].lower() in {"yes", "no"}:
        _choose_yes_no(page, pattern, yes=answers[0].lower() == "yes")


def _answer_demographics_and_eligibility(page: Any, values: dict[str, str]) -> None:
    """Apply only explicit candidate answers; never infer sensitive attributes."""
    authorized = "Yes" if values["work_authorized"].lower() in {"yes", "true", "1"} else ""
    sponsorship = "No" if values["sponsorship_required"].lower() in {"no", "false", "0"} else ""
    disability = ["No", "No, I don't have a disability", "No, I do not have a disability"] if values["disability"].lower() == "no" else values["disability"]
    veteran = ["No", "Not a protected veteran", "I am not a protected veteran"] if values["veteran_status"].lower() in {"no", "not a protected veteran"} else values["veteran_status"]
    gender = ["Female", "Woman"] if re.search(r"woman|female", values["gender_identity"], re.I) else values["gender_identity"]
    hispanic = ["No", "Not Hispanic or Latino"] if values["hispanic_latino"].lower() in {"no", "non-hispanic", "not hispanic or latino"} else values["hispanic_latino"]
    race = ["Asian", "Asian (Not Hispanic or Latino)"] if "asian" in values["race"].lower() else values["race"]
    transgender = ["No", "No, I do not identify as transgender"] if values["transgender"].lower() == "no" else values["transgender"]
    answers = [
        (r"(?:legally |lawfully )?authorized to work", authorized),
        (r"(?:need|require).{0,35}sponsorship|sponsorship.{0,35}(?:need|required)", sponsorship),
        (r"^Gender\b|gender identity", gender),
        (r"transgender|trans experience", transgender),
        (r"Hispanic|Latino", hispanic),
        (r"identify your race|race(?: or| and)? ethnicity|racial/ethnic|ethnicities|^Race\b", race),
        (r"protected veteran|veteran status|military status|veteran or active|served in the military", veteran),
        (r"disability status|have a disability|live with a disability|do you have a disability", disability),
    ]
    for question, answer in answers:
        _answer_known_choice(page, question, answer)


def _choose_labeled_select(page: Any, label_pattern: re.Pattern[str], option_text: str) -> None:
    control = page.get_by_label(label_pattern)
    if not control.count():
        return
    try:
        tag = control.first.evaluate("e => e.tagName.toLowerCase()")
        if tag == "select":
            labels = control.first.locator("option").all_text_contents()
            match = next((label for label in labels if option_text.lower() in label.lower()), None)
            if match:
                control.first.select_option(label=match)
            return
        control.first.click(timeout=3_000)
        option = page.get_by_role("option", name=re.compile(rf"^{re.escape(option_text)}$", re.I))
        if option.count():
            option.first.click(timeout=3_000)
            return
        control.first.fill(option_text, timeout=3_000)
        control.first.press("ArrowDown", timeout=3_000)
        control.first.press("Enter", timeout=3_000)
    except Exception:
        pass


def _locator_has_value(locator: Any) -> bool:
    if not locator.count():
        return False
    try:
        field = locator.first
        if (field.get_attribute("type") or "").lower() == "file":
            return bool(field.evaluate("e => e.files && e.files.length"))
        return bool(field.input_value().strip())
    except Exception:
        try:
            text = (locator.first.inner_text() or "").strip()
            return bool(text and text.lower() not in {"select...", "start typing...", "type here..."})
        except Exception:
            return False


def _any_label_has_value(page: Any, labels: list[str]) -> bool:
    return any(_locator_has_value(page.get_by_label(label, exact=True)) for label in labels)


def _question_is_answered(page: Any, pattern: re.Pattern[str]) -> bool:
    direct = page.get_by_label(pattern)
    if direct.count():
        try:
            if direct.first.is_checked():
                return True
        except Exception:
            pass
        if _locator_has_value(direct):
            return True
    labels = page.locator("label")
    matches = []
    for index in range(labels.count()):
        label = labels.nth(index)
        text = label.inner_text().strip()
        if pattern.search(text):
            matches.append((len(text), label))
    if not matches:
        return True
    question = min(matches, key=lambda item: item[0])[1]
    for levels in range(1, 7):
        container = question.locator("xpath=" + "/.." * levels)
        choices = container.locator('input[type="radio"], input[type="checkbox"]')
        if choices.count() and any(choices.nth(i).is_checked() for i in range(choices.count())):
            return True
    return False


def _question_exists(page: Any, pattern: re.Pattern[str]) -> bool:
    if page.get_by_label(pattern).count():
        return True
    labels = page.locator("label")
    return any(pattern.search(labels.nth(index).inner_text().strip()) for index in range(labels.count()))


def _verify_prefill(page: Any) -> list[str]:
    ats = detect_ats(page.url)
    missing: list[str] = []
    if ats == "ashby":
        checks = {
            "name": ["Name", "Full Name", "Legal First and Last Name"],
            "email": ["Email"],
            "resume": ["Resume"],
            "LinkedIn": ["LinkedIn", "LinkedIn URL", "LinkedIn Profile", "Linkedin Profile Link"],
        }
        for field, aliases in checks.items():
            if any(page.get_by_label(alias, exact=True).count() for alias in aliases) and not _any_label_has_value(page, aliases):
                missing.append(field)
        if any(page.get_by_label(alias, exact=True).count() for alias in ["Location", "Current Location"]):
            if not _any_label_has_value(page, ["Location", "Current Location"]):
                missing.append("location")
        if not _question_is_answered(page, re.compile(r"(?:legally )?authorized to work", re.I)):
            missing.append("work authorization")
        if not _question_is_answered(page, re.compile(r"require visa sponsorship", re.I)):
            missing.append("sponsorship")
    elif ats == "greenhouse":
        checks = {
            "first name": page.locator("#first_name"),
            "last name": page.locator("#last_name"),
            "email": page.locator("#email"),
            "resume": page.locator("#resume"),
            "LinkedIn": page.get_by_label(re.compile(r"LinkedIn Profile", re.I)),
        }
        missing.extend(name for name, locator in checks.items() if locator.count() and not _locator_has_value(locator))
        if not _question_is_answered(page, re.compile(r"legally authorized to work", re.I)):
            missing.append("work authorization")
        if not _question_is_answered(page, re.compile(r"require sponsorship", re.I)):
            missing.append("sponsorship")
    common_questions = {
        "gender": re.compile(r"^Gender\b|gender identity", re.I),
        "transgender": re.compile(r"transgender|trans experience", re.I),
        "race/ethnicity": re.compile(r"identify your race|race(?: or| and)? ethnicity|racial/ethnic|ethnicities|^Race\b", re.I),
        "veteran status": re.compile(r"protected veteran|veteran status|military status|veteran or active|served in the military", re.I),
        "disability": re.compile(r"disability status|have a disability|live with a disability|do you have a disability", re.I),
    }
    for name, pattern in common_questions.items():
        if _question_exists(page, pattern) and not _question_is_answered(page, pattern):
            missing.append(name)
    return missing


def _choose_custom_select(page: Any, label_pattern: re.Pattern[str], value: str) -> None:
    if not value:
        return
    control = page.get_by_label(label_pattern)
    if not control.count():
        return
    try:
        control.first.fill(value, timeout=3_000)
        page.wait_for_timeout(300)
        control.first.press("ArrowDown", timeout=3_000)
        control.first.press("Enter", timeout=3_000)
    except Exception:
        pass


def _choose_location(page: Any, field: Any, value: str) -> None:
    city = value.split(",", 1)[0].strip() or value
    try:
        field.click(timeout=3_000)
        field.fill("")
        field.press_sequentially(city, delay=100, timeout=5_000)
        page.wait_for_timeout(3_000)
        options = page.locator('[role="option"]:visible')
        if options.count():
            preferred = options.filter(has_text=re.compile(re.escape(city), re.I))
            (preferred.first if preferred.count() else options.first).click(timeout=3_000)
        else:
            field.press("ArrowDown", timeout=3_000)
            field.press("Enter", timeout=3_000)
        page.wait_for_timeout(500)
    except Exception:
        pass


def _choose_yes_no(page: Any, question_pattern: re.Pattern[str], *, yes: bool) -> None:
    labels = page.locator("label")
    matches = []
    for index in range(labels.count()):
        label = labels.nth(index)
        text = label.inner_text().strip()
        if question_pattern.search(text):
            matches.append((len(text), label))
    if not matches:
        return
    question = min(matches, key=lambda item: item[0])[1]
    desired = re.compile(r"^Yes$" if yes else r"^No$", re.I)
    for levels in range(0, 7):
        container = question if levels == 0 else question.locator("xpath=" + "/.." * levels)
        buttons = container.get_by_role("button", name=desired)
        if buttons.count():
            try:
                buttons.first.click(timeout=3_000)
                return
            except Exception:
                pass
    try:
        question.click(timeout=3_000)
    except Exception:
        pass


def _choose_radio_group(page: Any, question_pattern: re.Pattern[str], option_pattern: re.Pattern[str] | None) -> None:
    if option_pattern is None:
        return
    labels = page.locator("label")
    matching_questions = []
    for index in range(labels.count()):
        label = labels.nth(index)
        text = label.inner_text().strip()
        if question_pattern.search(text):
            matching_questions.append((len(text), label))
    if not matching_questions:
        return
    question = min(matching_questions, key=lambda item: item[0])[1]
    for levels in range(1, 7):
        container = question.locator("xpath=" + "/.." * levels)
        if container.locator('input[type="radio"]').count() < 2:
            continue
        labels = container.locator("label")
        for index in range(labels.count()):
            label = labels.nth(index)
            if not option_pattern.search(label.inner_text().strip()):
                continue
            try:
                label.click(timeout=3_000)
            except Exception:
                pass
            return


def _chrome_profile_dir(root: Path) -> Path:
    configured = application_value("chrome_profile_dir")
    return Path(configured).expanduser() if configured else root / "chrome_profiles" / "default"


def _launch_browser_context(playwright: Any, profile_dir: Path, *, headless: bool) -> Any:
    args = [
        "--no-default-browser-check",
        *(
            [f"--profile-directory={application_value('chrome_profile_name')}"]
            if application_value("chrome_profile_name") else []
        ),
    ]
    try:
        return playwright.chromium.launch_persistent_context(
            str(profile_dir), channel="chrome", headless=headless, args=args,
        )
    except Exception as exc:
        message = str(exc)
        if "existing browser session" not in message.lower() and "profile is already in use" not in message.lower():
            raise
        raise SystemExit(
            "The Metis Chrome session is already open. Close its Chrome window, wait a few seconds, "
            "then run `metis apply` again. Your prepared application tabs and state are preserved."
        ) from None


def _navigate_to_application(context: Any, page: Any) -> Any:
    if detect_ats(page.url):
        return page
    body = page.locator("body").inner_text(timeout=5_000)
    if "couldn’t sign you in" in body.lower() or "couldn't sign you in" in body.lower():
        raise RuntimeError("Google blocked sign-in in the automated Chrome profile. Sign into LinkedIn directly instead of using Continue with Google.")
    candidates = [
        page.get_by_role("link", name=re.compile(r"^(?:easy )?apply(?: now)?$", re.I)),
        page.get_by_role("button", name=re.compile(r"^(?:easy )?apply(?: now)?$", re.I)),
    ]
    control = None
    for controls in candidates:
        for index in range(controls.count()):
            item = controls.nth(index)
            if item.is_visible():
                control = item
                break
        if control is not None:
            break
    if control is None:
        raise RuntimeError("Could not find an external Apply control on the posting.")
    text = (control.inner_text() or "").strip()
    if "easy apply" in text.lower():
        raise RuntimeError("LinkedIn Easy Apply is not supported yet.")
    before = list(context.pages)
    control.click(timeout=5_000)
    page.wait_for_timeout(1_500)
    new_pages = [item for item in context.pages if item not in before]
    destination = new_pages[-1] if new_pages else page
    destination.wait_for_load_state("domcontentloaded", timeout=60_000)
    if not detect_ats(destination.url):
        raise RuntimeError(f"The external application host is not supported yet: {destination.url}")
    return destination


def _sync_tracker_applied(candidate: ApplicationCandidate) -> None:
    import openpyxl
    from . import xlsx
    from .track_write import find_tracker_row, update_confirmation

    xlsx.write_to_tracker([candidate.role])
    if not xlsx.TRACKER_PATH.exists():
        return
    workbook = openpyxl.load_workbook(xlsx.TRACKER_PATH)
    sheet = workbook.active
    row = find_tracker_row(sheet, str(candidate.role.get("company") or ""), str(candidate.role.get("title") or ""))
    if row is None:
        return
    update_confirmation(sheet, row, dt.date.today().isoformat())
    xlsx.format_tracker_sheet(sheet)
    workbook.save(xlsx.TRACKER_PATH)
    xlsx.TRACKER_PATH.chmod(0o600)


def prepare_in_browser(candidate: ApplicationCandidate, *, root: Path | None = None, headless: bool = False) -> str:
    return prepare_batch_in_browser([candidate], root=root, headless=headless)[0]["status"]


def prepare_batch_in_browser(
    candidates: list[ApplicationCandidate], *, root: Path | None = None, headless: bool = False,
) -> list[dict[str, str]]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Browser support is not installed. Run `pip install 'metis-job[browser]'`.") from exc

    root = root or data_dir()
    results: dict[str, dict[str, str]] = {}
    with sync_playwright() as playwright:
        profile_dir = _chrome_profile_dir(root)
        profile_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        context = _launch_browser_context(playwright, profile_dir, headless=headless)
        initial_pages = list(context.pages)
        active: dict[Any, tuple[ApplicationCandidate, dict[str, Any]]] = {}
        for candidate in candidates:
            start_url = _start_url(candidate.role)
            ats = detect_ats(start_url) or "external"
            common = {
                "role": candidate.role,
                "tailoring_record": str(candidate.record_path) if candidate.record_path else None,
                "resume_used": str(candidate.resume_path),
                "ats": ats,
            }
            update_application_state(candidate.role_key, status="opened", root=root, **common)
            page = context.new_page()
            try:
                page.goto(start_url, wait_until="domcontentloaded", timeout=60_000)
                page = _navigate_to_application(context, page)
                common["ats"] = detect_ats(page.url) or ats
                common["application_url"] = page.url
                _fill_visible_form(page, candidate)
                missing = _verify_prefill(page)
                status = "needs_review" if missing else "prefilled"
                update_application_state(
                    candidate.role_key, status=status, root=root, prefill_missing=missing, **common,
                )
                if missing:
                    print(f"Needs review: {candidate.role.get('title')} at {candidate.role.get('company')} — missing {', '.join(missing)}")
                results[candidate.role_key] = {
                    "role": f"{candidate.role.get('title')} at {candidate.role.get('company')}", "status": status,
                }
                active[page] = (candidate, common)
            except Exception as exc:
                update_application_state(candidate.role_key, status="blocked", root=root, error=str(exc), **common)
                print(f"Could not prepare {candidate.role.get('title')} at {candidate.role.get('company')}: {exc}")
                results[candidate.role_key] = {"role": f"{candidate.role.get('title')} at {candidate.role.get('company')}", "status": "blocked"}
                page.close()
        for page in initial_pages:
            if page in context.pages and page not in active:
                page.close()
        if headless or not active:
            context.close()
            return [results[item.role_key] for item in candidates]
        print(f"Prepared {len(active)} application tab(s). Review each and click Submit; close the tabs to stop monitoring.")
        try:
            while active:
                for page, (candidate, common) in list(active.items()):
                    if page.is_closed():
                        active.pop(page, None)
                        continue
                    try:
                        if _looks_submitted(page.url, page.locator("body").inner_text(timeout=2_000)):
                            update_application_state(
                                candidate.role_key, status="applied", root=root,
                                submission_evidence={"url": page.url, "kind": "browser_success"}, **common,
                            )
                            _sync_tracker_applied(candidate)
                            results[candidate.role_key]["status"] = "applied"
                            active.pop(page, None)
                    except Exception:
                        if page.is_closed():
                            active.pop(page, None)
                if active:
                    time.sleep(1)
        except Exception as exc:
            # Closing the last tab or the entire Chrome window tears down Playwright's
            # page/context while the monitor may be between calls. That is a normal exit.
            if "closed" not in str(exc).lower():
                raise
        try:
            context.close()
        except Exception:
            pass
    return [results[item.role_key] for item in candidates]


def run_apply(
    *, apply_all: bool = False, top_n: int | None = None, include_applied: bool = False,
    match_terms: list[str] | None = None,
) -> list[dict[str, str]]:
    candidates = load_application_candidates(include_applied=include_applied)
    terms = [term.strip().lower() for term in (match_terms or []) if term.strip()]
    if terms:
        candidates = [
            candidate for candidate in candidates
            if any(term in f"{candidate.role.get('company', '')} {candidate.role.get('title', '')}".lower() for term in terms)
        ]
    if not candidates:
        raise SystemExit(
            "No pending tailored roles with an available DOCX resume. Run `metis resume tailor`, "
            "or set METIS_DEFAULT_RESUME."
        )
    if top_n is not None:
        if top_n <= 0:
            raise SystemExit("--top must be a positive integer.")
        selected = candidates[:top_n]
    elif apply_all:
        selected = candidates
    elif not os.isatty(0):
        raise SystemExit("Run `metis apply --all`, or run interactively to choose roles.")
    else:
        selected = select_candidates(candidates)
    return prepare_batch_in_browser(selected)
