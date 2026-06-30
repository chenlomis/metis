"""scorerole/track_parse.py — email classification and entity extraction.

Owns: phrase constants, subject patterns, classify_email(), parse_email(),
extract_company(), extract_role(). Pure functions — no I/O, no IMAP.
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Body-text signals — order matters: rejection check runs first
# ---------------------------------------------------------------------------

_REJECTION_PHRASES = [
    "decided not to move forward",
    "won't be moving forward",
    "will not be moving forward",
    "we're unable to move forward",
    "unable to move forward",
    "move forward with other candidates",
    "decided to move forward with other candidates",
    "not move ahead",
    "not to move ahead",
    "not to proceed with your application",
    "decided not to proceed",
    "not an ideal fit",
    "there isn't an ideal fit",
    "we regret to inform you",
    "regret to inform",
    "won't be progressing",
    "not be progressing with your application",
    "have decided not to move forward",
    "decision to not move forward",
    "90-day waiting period before reconsidering",
    "we will not be moving forward",
    "we are not able to move forward",
    "not able to move forward",
    "decided not to pursue",
    "not moving forward with your application",
    "we're moving forward with other",
    "moving forward with other candidates",
]

_RECRUITER_SCREEN_PHRASES = [
    "let me know your availability",
    "please let us know your availability",
    "please feel free to find time",
    "please select a time through",
    "select a time that will work for you",
    "set up time for a phone screen",
    "schedule some time for us to",
    "i'd like to schedule some time",
    "we'd like to invite you for a virtual",
    "we're excited to move forward with the interview",
    "interested in speaking with you about our",
    "complete the availability questionnaire",
]

_CONFIRMATION_PHRASES = [
    "your application has been received",
    "we have received your application",
    "received your application",
    "application and we will review",
    "will review your application",
    "we'll review your application",
    "we'll review your application",
    "reviewing your application",
    "we will review it",
    "will be in touch if",
    "will be in touch with you",
    "reach out if",
    "will reach out if",
    "will contact you",
    "submit an application",
    "completed the application",
    "check the application status",
    "look forward to reviewing your application",
    "look forward to learning more about you",
    "application is under review",
    "your application is being reviewed",
    "we're reviewing your application",
    "we are reviewing your application",
    "thank you for submitting your application",
    "we've received your application",
    "we've received your application",
    "we will review it shortly",
    "we will review your application",
    "we will begin reviewing it",
    "thanks for applying to",
    "thank you for applying to",
    "thank you for taking the time to apply",
    "thank you for applying for the",
    "wanted to confirm that we have received",
]


# ---------------------------------------------------------------------------
# Subject-line patterns
# ---------------------------------------------------------------------------

_SUBJECT_CANDIDATES = re.compile(
    r"thank you (for (apply|applying|your application|your interest)|from )|"
    r"thanks (for (apply|applying|your interest)|for your application|for completing)|"
    r"your application (to |for |at )|"
    r"following up on your (application|recent application)|"
    r"application (status )?update|"
    r"important information about your application|"
    r"keep track of your application|"
    r"we have received your application|"
    r"we('ve| have) received your application|"
    r"we got it",
    re.IGNORECASE,
)

_SUBJECT_IMPLIES_CONFIRMATION = re.compile(
    r"thank(?:s| you) for (applying|your application|your interest|submitting)|"
    r"thanks for (applying|your application|completing your application|submitting)|"
    r"your application for .+ at |"
    r"we('ve| have) received your application|"
    r"we have received your application|"
    r"thank you for your application to|"
    r"your application to \w|"
    r"we got it|"
    r"welcome to .+ careers|"
    r"we look forward to",
    re.IGNORECASE,
)

_SUBJECT_IMPLIES_RECRUITER_SCREEN = re.compile(
    r"next steps with |"
    r"\w[\w ]+ next steps$|"
    r"let's set up your phone screen|"
    r"phone screen with |"
    r"hello from \w",
    re.IGNORECASE,
)

_SUBJECT_IMPLIES_REJECTION = re.compile(
    r"thank you from |"
    r"following up on your (application|recent application)|"
    r"application (status )?update|"
    r"update on your .+ application|"
    r"application feedback from |"
    r"important information about your application",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Company extraction patterns
# ---------------------------------------------------------------------------

_COMPANY_FROM_SUBJECT = [
    re.compile(r"applying (?:to|at) ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"application to the .+? (?:role|position) at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"your application to ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"your application for .+ at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"applying for the role of .+ at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"interest (?:in|with) ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"thank you from ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"thanks for applying to ([A-Za-z0-9][^!,\n]+?)(?:[!,\s]|$)", re.IGNORECASE),
    re.compile(r"^([A-Za-z0-9][^|]+?)\s*\|", re.IGNORECASE),
    re.compile(r"following up on your (?:recent )?application to ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"application with ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"^([A-Za-z0-9][^-|]+?)\s+[-–]", re.IGNORECASE),
    re.compile(r"update on your ([A-Za-z0-9][^a-z]+?) application", re.IGNORECASE),
    re.compile(r"application update from ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"we have received your application for .+ at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"we have received your application for ([A-Za-z0-9][A-Za-z0-9 &.,]+?)(?:[!,]|$)", re.IGNORECASE),
    re.compile(r"^([A-Za-z0-9][^:]+?):\s+thanks for", re.IGNORECASE),
    re.compile(r"^[A-Z][a-z]+,\s+thank you from ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
]

_COMPANY_TRAILING_NOISE = re.compile(
    r"\s*[-–]\s*(?:[A-Z][a-z]+ [A-Z][a-z]+|[A-Z][a-z]+,? [A-Z][a-z]+)$"
)
_COMPANY_LEADING_THE   = re.compile(r"^the\s+", re.IGNORECASE)
_COMPANY_SENDER_SUFFIX = re.compile(
    r"\s+(?:recruiting|talent|hiring team|hr|careers|talent acquisition)$", re.IGNORECASE
)
_ROLE_TITLE_WORDS = re.compile(
    r"\b(?:manager|director|engineer|product|principal|staff|senior|lead|analyst|"
    r"scientist|designer|architect|specialist|coordinator|associate|vp|head of)\b",
    re.IGNORECASE,
)

_COMPANY_FROM_BODY = [
    re.compile(r"applying (?:for|to) .+? (?:role|position)(?: here)? at ([A-Z][A-Za-z0-9 &.,]+?)[\.\!,\n]"),
    re.compile(r"apply for .+ (?:role|position) at ([A-Z][A-Za-z0-9 &.,]+?)[\.\!,\n]"),
    re.compile(r"interest in ([A-Z][A-Za-z0-9 &.,]+?) and"),
    re.compile(r"(?:joining|with) ([A-Z][A-Za-z0-9 &.,]+?)[\.\!,\n]"),
]

_GENERIC_SENDER_DOMAINS = {
    "gmail", "googlemail", "yahoo", "hotmail", "outlook", "icloud",
    "ashbyhq", "ashby", "greenhouse-mail", "greenhouse",
    "lever", "smartrecruiters", "icims", "jobvite", "taleo",
    "bamboohr", "myworkdayjobs", "myworkday", "successfactors", "applytojob",
    "talentplatform", "workday",
    "notifications", "noreply", "no-reply", "mailer", "bounce", "mail",
}


# ---------------------------------------------------------------------------
# Role extraction patterns
# ---------------------------------------------------------------------------

_ROLE_FROM_BODY = [
    re.compile(r"apply(?:ing)? (?:for|to) the (.+?) (?:role|position)", re.IGNORECASE),
    re.compile(r"your application for the (.+?) (?:role|position)", re.IGNORECASE),
    re.compile(r"application for the (.+?) (?:role|position)", re.IGNORECASE),
    re.compile(r"the (.+?) (?:role|position)(?: here)? at [A-Z]", re.IGNORECASE),
    re.compile(r"(?:role|position) (?:of|for) (.+?) (?:at|here)", re.IGNORECASE),
    re.compile(r"position of (.+?)(?:\s*\(|,|\.|$)", re.IGNORECASE),
    re.compile(r"for the (.+?) (?:role|position|opportunity)", re.IGNORECASE),
]

_ROLE_MAX_LEN = 120

_ROLE_FROM_SUBJECT = [
    re.compile(r"your application for (.+?) at [A-Za-z]", re.IGNORECASE),
    re.compile(r"applying to (.+?)(?:\s*[-–]|\s*[!,]|$)", re.IGNORECASE),
    re.compile(r"application\s*[-–]\s*(.+?)$", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# LLM classification
# ---------------------------------------------------------------------------

_LLM_CLASSIFY_PROMPT = """\
You are classifying a job-application email into exactly one of four categories.

Categories:
- confirmation   : the sender acknowledges receiving the application or confirms a next step
- rejection      : the sender declines the application or ends consideration
- recruiter_screen : the sender requests to schedule a call, phone screen, or interview
- unknown        : none of the above (newsletters, automated alerts, unrelated)

Reply with exactly one lowercase word from the list above. No punctuation, no explanation.

Subject: {subject}

Body (truncated):
{body}"""

_LLM_BODY_CHAR_LIMIT = 1500
_LLM_VALID_CLASSES = frozenset(["confirmation", "rejection", "recruiter_screen", "unknown"])


def _classify_with_llm(subject: str, body: str, client) -> str:
    from .prompts import track_classify_system_prompt

    truncated_body = body[:_LLM_BODY_CHAR_LIMIT]
    prompt = _LLM_CLASSIFY_PROMPT.format(subject=subject, body=truncated_body)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system=track_classify_system_prompt(),
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
        if result in _LLM_VALID_CLASSES:
            return result
        log.warning("track: LLM returned unexpected class %r — falling back to 'unknown'", result)
    except Exception as exc:
        log.warning("track: LLM classification failed (%s) — falling back to 'unknown'", exc)
    return "unknown"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _normalize_body(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("\r\n", " ").replace("\n", " ")
    return text.lower()


def classify_email(body: str, subject: str = "", llm_client=None) -> str:
    """Return 'confirmation', 'rejection', 'recruiter_screen', or 'unknown'."""
    body_norm = _normalize_body(body)

    for phrase in _REJECTION_PHRASES:
        if phrase in body_norm:
            return "rejection"
    for phrase in _RECRUITER_SCREEN_PHRASES:
        if phrase in body_norm:
            return "recruiter_screen"
    for phrase in _CONFIRMATION_PHRASES:
        if phrase in body_norm:
            return "confirmation"

    if _SUBJECT_IMPLIES_RECRUITER_SCREEN.search(subject):
        return "recruiter_screen"
    if _SUBJECT_IMPLIES_CONFIRMATION.search(subject):
        return "confirmation"
    if _SUBJECT_IMPLIES_REJECTION.search(subject):
        return "rejection"

    if llm_client is not None:
        return _classify_with_llm(subject, body, llm_client)

    return "unknown"


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------

def _clean_company(raw: str) -> str | None:
    company = raw.strip().rstrip("!").strip()
    company = re.sub(r",\s+[A-Z][a-z]+$", "", company)
    company = _COMPANY_TRAILING_NOISE.sub("", company)
    company = _COMPANY_LEADING_THE.sub("", company)
    company = _COMPANY_SENDER_SUFFIX.sub("", company)
    company = company.strip()
    if not company or len(company) < 2:
        return None
    if re.fullmatch(r"(?:re|fw|fwd)", company, re.IGNORECASE):
        return None
    if re.search(r"\b(?:we received your application|let'?s connect|your application)\b", company, re.IGNORECASE):
        return None
    if ":" in company:
        company = company.split(":")[0].strip()
    if _ROLE_TITLE_WORDS.search(company) and len(company.split()) >= 2:
        return None
    return company


def _company_from_sender(sender: str) -> str | None:
    display_m = re.match(r'^"?([^"<]+?)"?\s*(?:<|$)', sender)
    if display_m:
        display = display_m.group(1).strip()
        company = re.sub(
            r'\s+(?:hiring team|talent team|talent acquisition|recruiting team|'
            r'careers|recruiter|hr|talent|@ \w+)$',
            "", display, flags=re.IGNORECASE
        ).strip()
        company = re.sub(r',?\s+(?:inc|llc|corp|ltd)\.?$', "", company, flags=re.IGNORECASE).strip()
        words = company.split()
        is_personal_name = (
            len(words) == 2
            and all(w[0].isupper() and w[1:].islower() and w.isalpha() for w in words)
        )
        if (company
                and len(company) > 2
                and not is_personal_name
                and not re.search(r'\b(?:noreply|no.reply|mailer|notifications|do.not.reply)\b',
                                  company, re.IGNORECASE)
                and "@" not in company):
            return company

    m = re.search(r"@([a-zA-Z0-9][-a-zA-Z0-9.]*\.[a-zA-Z]{2,})", sender)
    if not m:
        return None
    domain = m.group(1).lower()
    parts = domain.split(".")
    if len(parts) >= 2:
        slug = parts[-2]
    else:
        return None
    if slug in _GENERIC_SENDER_DOMAINS or len(slug) <= 2:
        return None
    return slug.title()


def extract_company(subject: str, body: str) -> str | None:
    subject = " ".join(subject.split())

    m = re.search(r"^([A-Za-z0-9][^:]+?) job application update", subject, re.IGNORECASE)
    if m:
        company = _clean_company(m.group(1))
        if company:
            return company

    for pattern in _COMPANY_FROM_SUBJECT:
        m = pattern.search(subject)
        if m:
            company = _clean_company(m.group(1))
            if company and len(company) > 1:
                return company

    for pattern in _COMPANY_FROM_BODY:
        m = pattern.search(body)
        if m:
            result = _clean_company(m.group(1))
            if result:
                return result

    return None


_ROLE_BOILERPLATE = re.compile(
    r"if you are not|keep an eye|we will contact|please continue|"
    r"for this role|for this position|you are not selected|"
    r"time to apply for our|taking the time|"
    r"unique skills deemed necessary|we received your application|let'?s connect",
    re.IGNORECASE,
)
_ROLE_TITLE_SIGNAL = re.compile(
    r"\b(?:product|program|project|manager|director|engineer|designer|"
    r"scientist|analyst|architect|lead|principal|staff|senior|head|vp)\b",
    re.IGNORECASE,
)


def _clean_role(raw: str) -> str | None:
    role = raw.strip()
    role = re.sub(r"\s*[\(\[]?(?:ID|JR|REQ)[:\s]\S+[\)\]]?", "", role, flags=re.IGNORECASE)
    role = re.sub(r"^\w{2,}\d{6,}\s+", "", role)
    role = re.sub(r"\s+\d{6,}$", "", role)
    role = role.strip().rstrip(".,")
    if len(role) < 4 or len(role) > _ROLE_MAX_LEN:
        return None
    if _ROLE_BOILERPLATE.search(role):
        return None
    return role


def _finalize_role(role: str | None, company: str | None) -> str | None:
    if not role:
        return None
    if company and _norm_for_entity(role) == _norm_for_entity(company):
        return None
    if not _ROLE_TITLE_SIGNAL.search(role):
        return None
    return role


def _norm_for_entity(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def extract_role(subject: str, body: str) -> str | None:
    subject = " ".join(subject.split())
    for pattern in _ROLE_FROM_BODY:
        m = pattern.search(body)
        if m:
            role = _clean_role(m.group(1))
            if role:
                return role

    for pattern in _ROLE_FROM_SUBJECT:
        m = pattern.search(subject)
        if m:
            role = _clean_role(m.group(1))
            if role:
                return role

    return None


def parse_email(email_dict: dict, llm_client=None) -> dict:
    """Classify and extract structured fields from a candidate email."""
    subject = email_dict["subject"]
    body    = email_dict["body"]

    classification = classify_email(body, subject, llm_client=llm_client)
    company = extract_company(subject, body)

    if not company:
        company = _company_from_sender(email_dict.get("sender", ""))

    role = _finalize_role(extract_role(subject, body), company)
    log.debug("track: parse  [%s] company=%r | %s", classification[:4], company, subject[:70])

    return {
        "classification": classification,
        "company":        company,
        "role":           role,
        "date":           email_dict["date"],
        "subject":        subject,
        "sender":         email_dict["sender"],
    }
