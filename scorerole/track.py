"""track.py — parse confirmation and rejection emails, update the Applications tracker.

Usage:
    scorerole track                   # parse emails from last 7 days
    scorerole track --lookback 30d    # extend lookback
    scorerole track --no-excel        # print matches to stdout, no xlsx write or open

Pipeline:
    1. Fetch emails from Gmail matching broad subject-line patterns
    2. Classify each as confirmation / rejection / unknown via body text
    3. Match to an existing tracker row (fuzzy company+title)
    4. Update the row: action_taken, date_applied, application_status
       For skipped-role confirmations: create a new row from skipped_roles.json
"""
from __future__ import annotations

import re, sys, datetime, logging, imaplib, email as email_lib
from email.header import decode_header
from pathlib import Path

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
    "not to move ahead",                            # Hightouch
    "not to proceed with your application",
    "decided not to proceed",                       # Google recruiter
    "not an ideal fit",
    "there isn't an ideal fit",
    "we regret to inform you",
    "regret to inform",
    "won't be progressing",
    "not be progressing with your application",
    "have decided not to move forward",
    "decision to not move forward",                 # Anduril
    "90-day waiting period before reconsidering",   # Scale AI duplicate-apply
    "we will not be moving forward",
    "we are not able to move forward",
    "not able to move forward",
    "decided not to pursue",
    "not moving forward with your application",
    "we're moving forward with other",              # Workato / generic
    "moving forward with other candidates",
]

_RECRUITER_SCREEN_PHRASES = [
    # Scheduling language — primary signal, mutually exclusive with rejection
    "let me know your availability",              # Klaviyo
    "please let us know your availability",       # SeekOut
    "please feel free to find time",              # Datadog
    "please select a time through",               # Descript (Calendly)
    "select a time that will work for you",       # Microsoft (eightfold)
    "set up time for a phone screen",             # Microsoft (eightfold)
    "schedule some time for us to",               # generic recruiter
    "i'd like to schedule some time",             # Klaviyo variant
    "we'd like to invite you for a virtual",      # SeekOut
    # Forward-moving framing — next-step context
    "we're excited to move forward with the interview",  # Descript
    "interested in speaking with you about our",         # NVIDIA
    "complete the availability questionnaire",           # NVIDIA (Workday)
]

_CONFIRMATION_PHRASES = [
    "your application has been received",
    "we have received your application",
    "received your application",
    "application and we will review",
    "will review your application",
    "we'll review your application",                # Google: curly apostrophe normalized
    "we’ll review your application",           # curly apostrophe variant
    "reviewing your application",
    "we will review it",
    "will be in touch if",
    "will be in touch with you",                    # Gen Digital
    "reach out if",
    "will reach out if",
    "will contact you",
    "submit an application",                        # Anthropic
    "completed the application",                    # Amazon
    "check the application status",                 # Amazon
    "look forward to reviewing your application",   # Workday / Google DeepMind
    "look forward to learning more about you",
    "application is under review",
    "your application is being reviewed",
    "we're reviewing your application",
    "we are reviewing your application",
    "thank you for submitting your application",    # Binti / generic
    "we’ve received your application",              # curly-apostrophe variant
    "we’ve received your application",
    "we will review it shortly",                    # Databricks / Greenhouse
    "we will review your application",
    "we will begin reviewing it",                   # Carta / Greenhouse
    "thanks for applying to",                       # Google, Scale AI, Databricks body
    "thank you for applying to",                    # CoreWeave, Mux body
    "thank you for taking the time to apply",       # Harvey / Ashby
    "thank you for applying for the",               # Headstart / Ashby
    "wanted to confirm that we have received",      # GitLab / Greenhouse
]

# ---------------------------------------------------------------------------
# Subject-line patterns for pre-filtering (broad — body decides classification)
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

# Subject patterns that strongly imply confirmation (used as body tiebreaker)
_SUBJECT_IMPLIES_CONFIRMATION = re.compile(
    r"thank(?:s| you) for (applying|your application|your interest|submitting)|"
    r"thanks for (applying|your application|completing your application|submitting)|"
    r"your application for .+ at |"
    r"we('ve| have) received your application|"
    r"we have received your application|"
    r"thank you for your application to|"
    r"your application to \w|"
    r"we got it|"              # "Gen Digital | We Got It!"
    r"welcome to .+ careers|"
    r"we look forward to",
    re.IGNORECASE,
)

# Subject patterns that strongly imply recruiter screen (used as body tiebreaker)
_SUBJECT_IMPLIES_RECRUITER_SCREEN = re.compile(
    r"next steps with |"                  # "Next Steps with Descript"
    r"\w[\w ]+ next steps$|"             # "SeekOut Next Steps", "Klaviyo - Next Steps"
    r"let's set up your phone screen|"   # Microsoft eightfold
    r"phone screen with |"               # generic
    r"hello from \w",                    # Datadog "Hello from Datadog"
    re.IGNORECASE,
)

# Subject patterns that strongly imply rejection (used as body tiebreaker)
_SUBJECT_IMPLIES_REJECTION = re.compile(
    r"thank you from |"           # NVIDIA, Elastic, Workato
    r"following up on your (application|recent application)|"
    r"application (status )?update|"
    r"update on your .+ application|"
    r"application feedback from |"  # Google DeepMind
    r"important information about your application",  # Altruist
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Company extraction — tried in order, first match wins
# ---------------------------------------------------------------------------

_COMPANY_FROM_SUBJECT = [
    # "applying to/at {COMPANY}[!, comma, end]"  — Yelp uses "at", most use "to"
    re.compile(r"applying (?:to|at) ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "application to the {ROLE} role at {COMPANY}" — must come before bare "application to"
    re.compile(r"application to the .+? (?:role|position) at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "your application to {COMPANY}"
    re.compile(r"your application to ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "your application for {ROLE} at {COMPANY}" — must come before bare "applying to"
    re.compile(r"your application for .+ at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "Thanks for applying for the role of {ROLE} at {COMPANY}" (Workday)
    re.compile(r"applying for the role of .+ at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "interest in {COMPANY}" / "interest with {COMPANY}"
    re.compile(r"interest (?:in|with) ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "thank you from {COMPANY}" (NVIDIA, Workato)
    re.compile(r"thank you from ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "thanks for applying to {COMPANY}"
    re.compile(r"thanks for applying to ([A-Za-z0-9][^!,\n]+?)(?:[!,\s]|$)", re.IGNORECASE),
    # "{COMPANY} | We Got It!" / "Luma | Thanks for..."
    re.compile(r"^([A-Za-z0-9][^|]+?)\s*\|", re.IGNORECASE),
    # "following up on your (recent) application to {COMPANY}" (Google rejection)
    re.compile(r"following up on your (?:recent )?application to ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "following up on your application with {COMPANY}" (Front)
    re.compile(r"application with ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "{COMPANY} - Thank You For..." or "Google DeepMind - Thank You..."
    re.compile(r"^([A-Za-z0-9][^-|]+?)\s+[-–]", re.IGNORECASE),
    # "Update on your {COMPANY} Application"
    re.compile(r"update on your ([A-Za-z0-9][^a-z]+?) application", re.IGNORECASE),
    # "Application Update from {COMPANY}" (Snorkel AI)
    re.compile(r"application update from ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "{Name}, we have received your application for {ROLE} at {COMPANY}" (Netflix)
    re.compile(r"we have received your application for .+ at ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
    # "we have received your application for {COMPANY}!" (Circle — company, not role, after "for")
    re.compile(r"we have received your application for ([A-Za-z0-9][A-Za-z0-9 &.,]+?)(?:[!,]|$)", re.IGNORECASE),
    # "{COMPANY}: Thanks for..." (Atlassian)
    re.compile(r"^([A-Za-z0-9][^:]+?):\s+thanks for", re.IGNORECASE),
    # "Lomis, Thank you from {COMPANY}" — leading name prefix
    re.compile(r"^[A-Z][a-z]+,\s+thank you from ([A-Za-z0-9][^!,\n]+?)(?:[!,]|$)", re.IGNORECASE),
]

# Trailing noise to strip from extracted company names
_COMPANY_TRAILING_NOISE = re.compile(
    r"\s*[-–]\s*(?:[A-Z][a-z]+ [A-Z][a-z]+|[A-Z][a-z]+,? [A-Z][a-z]+)$"  # "- Lomis Chen"
)
_COMPANY_LEADING_THE   = re.compile(r"^the\s+", re.IGNORECASE)
_COMPANY_SENDER_SUFFIX = re.compile(
    r"\s+(?:recruiting|talent|hiring team|hr|careers|talent acquisition)$", re.IGNORECASE
)
# Words that indicate the extracted string is a role title, not a company name
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

# ---------------------------------------------------------------------------
# Role extraction — body is primary; subject fallback
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

# Role strings longer than this are almost certainly boilerplate leakage
_ROLE_MAX_LEN = 120

_ROLE_FROM_SUBJECT = [
    # "Your application for {ROLE} at {COMPANY}"
    re.compile(r"your application for (.+?) at [A-Za-z]", re.IGNORECASE),
    # "applying to {ROLE} - {COMPANY}" or "Applying to {ROLE}!"
    re.compile(r"applying to (.+?)(?:\s*[-–]|\s*[!,]|$)", re.IGNORECASE),
    # "Update on your {COMPANY} Application - {ROLE}"
    re.compile(r"application\s*[-–]\s*(.+?)$", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Email fetching
# ---------------------------------------------------------------------------

def _decode_header_value(raw: str | bytes) -> str:
    if isinstance(raw, bytes):
        parts = decode_header(raw.decode("utf-8", errors="replace"))
    else:
        parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _strip_html(html: str) -> str:
    """Strip HTML tags and decode entities to plain text."""
    import html as html_lib
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _safe_decode(payload: bytes, charset: str | None) -> str:
    """Decode bytes to str, falling back gracefully on unknown/binary charsets."""
    for enc in (charset, "utf-8", "latin-1"):
        if not enc or enc.lower() in ("binary", "unknown", "x-unknown"):
            continue
        try:
            return payload.decode(enc, errors="replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return payload.decode("latin-1", errors="replace")


def _extract_body(msg) -> str:
    """Extract plain-text body from an email.Message object.

    Prefers text/plain parts. Falls back to stripping text/html when no plain
    text is available — required for Amazon, Netflix, Google DeepMind, etc.
    """
    plain_parts = []
    html_parts  = []

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = _safe_decode(payload, part.get_content_charset())
            if ct == "text/plain":
                plain_parts.append(decoded)
            elif ct == "text/html":
                html_parts.append(decoded)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            decoded = _safe_decode(payload, msg.get_content_charset())
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                plain_parts.append(decoded)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return _strip_html("\n".join(html_parts))
    return ""


def fetch_candidate_emails(gmail_address: str, app_password: str, since_dt: datetime.datetime) -> list[dict]:
    """Fetch emails from Gmail that might be confirmations or rejections.

    Uses server-side IMAP SUBJECT search to avoid pulling headers for every inbox
    email. Runs several searches and unions the results, then fetches full RFC822
    only for matching message IDs.
    """
    from email.utils import parsedate_to_datetime

    since_str = since_dt.strftime("%d-%b-%Y")

    # Server-side subject filters — each maps to one IMAP SEARCH call.
    # Gmail evaluates these server-side so no per-message round trips needed.
    # Each term is a COMPLETE WORD or phrase as it appears in real subjects.
    # Gmail IMAP does word-based search — "apply" does NOT match "applying".
    # All terms derived from real email samples in scorerole_confEmail/.
    _SUBJECT_SEARCHES = [
        # Confirmation — "Thank you for applying to {Company}" (Greenhouse, direct)
        "thank you for applying",
        "thanks for applying",
        # Confirmation — "Thank you for your application to {Company}" (Ashby, Lever)
        "thank you for your application",
        "thanks for your application",
        # Confirmation — "Your application for {Role} at {Company}" (Ashby/Superhuman)
        "your application for",
        # Confirmation — "Thanks for your interest in {Company}" (Greenhouse/Carta)
        "thanks for your interest",
        "thank you for your interest",
        # Confirmation — "Keep track of your application" (Amazon jobs)
        "keep track of your application",
        # Confirmation — "We've Received Your Application!" (Console/Ashby)
        "we've received your application",
        # Confirmation — "We received your application" (various)
        "we received your application",
        # Confirmation — "We Got It!" (Gen Digital)
        "we got it",
        # Confirmation — "Next Steps with {Company}" (Descript/Klaviyo)
        "next steps",
        # Rejection — "Following up on your application" (Google, Front)
        "following up on your",
        # Rejection/Update — "Application Update", "Update on your application"
        "application update",
        "update on your",
        # Rejection — "Thank you from {Company}" (NVIDIA, Workato)
        "thank you from",
        # Rejection — "Application feedback from {Company}" (Google DeepMind)
        "application feedback",
        # Confirmation/Rejection — "Your Application to {Company}" (Anduril direct)
        "your application to",
        # Recruiter screen — "Action Requested: Let's set up your phone screen" (Microsoft)
        "phone screen",
        # Recruiter screen — "Hello from Datadog" (named recruiter outreach)
        "hello from",
        # Update — "Application Follow Up" (GitHub/iCIMS)
        "application follow up",
        # Update — "We look forward to" (various)
        "we look forward to",
    ]

    emails = []

    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(gmail_address, app_password)
        imap.select("INBOX")

        # Union of all subject searches — deduplicated
        matching_ids: set[bytes] = set()
        for term in _SUBJECT_SEARCHES:
            _, data = imap.search(None, f'SINCE {since_str} SUBJECT "{term}"')
            if data and data[0]:
                matching_ids.update(data[0].split())

        if not matching_ids:
            log.info("track: no candidate emails found since %s", since_str)
            return []

        log.info("track: %d subject-matched emails — fetching bodies…", len(matching_ids))

        for msg_id in sorted(matching_ids):
            _, raw = imap.fetch(msg_id, "(RFC822)")
            if not raw or not raw[0]:
                continue
            raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else raw[0]
            msg = email_lib.message_from_bytes(raw_bytes)

            subject     = _decode_header_value(msg.get("Subject", ""))
            sender      = _decode_header_value(msg.get("From", ""))
            date_header = msg.get("Date", "")

            # Skip out-of-office auto-replies — they're triggered by our own emails
            if re.search(r"\bout of office\b|auto.?reply|automatic reply|autoreply", subject, re.IGNORECASE):
                log.debug("track: skipping OOO/auto-reply — %s", subject[:80])
                continue
            # Also check X-Auto-Submitted header (RFC 3834)
            if msg.get("X-Auto-Submitted", "").lower() not in ("", "no"):
                log.debug("track: skipping auto-submitted — %s", subject[:80])
                continue

            try:
                email_date = parsedate_to_datetime(date_header).date().isoformat()
            except Exception:
                email_date = datetime.date.today().isoformat()

            body = _extract_body(msg)
            log.debug("track: fetched — %s | from: %s", subject[:80], sender[:50])
            emails.append({
                "subject": subject,
                "sender":  sender,
                "body":    body,
                "date":    email_date,
            })

    log.info("track: fetched %d candidate emails since %s", len(emails), since_str)
    return emails


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _normalize_body(text: str) -> str:
    """Normalize body text for reliable phrase matching.

    Handles two common PDF extraction artifacts:
    - Curly apostrophes (U+2019 → ') so "won't", "we'll", "we've" match straight-apostrophe phrases
    - Newlines mid-sentence (replaced with space) so multi-line phrases match
    """
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("\r\n", " ").replace("\n", " ")
    return text.lower()


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
    """Ask Haiku to classify an email that phrase matching left as 'unknown'.

    Returns one of the four classification strings; falls back to 'unknown'
    on any API error or unexpected response.
    """
    truncated_body = body[:_LLM_BODY_CHAR_LIMIT]
    prompt = _LLM_CLASSIFY_PROMPT.format(subject=subject, body=truncated_body)
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        result = response.content[0].text.strip().lower()
        if result in _LLM_VALID_CLASSES:
            return result
        log.warning("track: LLM returned unexpected class %r — falling back to 'unknown'", result)
    except Exception as exc:
        log.warning("track: LLM classification failed (%s) — falling back to 'unknown'", exc)
    return "unknown"


def classify_email(body: str, subject: str = "", llm_client=None) -> str:
    """Return 'confirmation', 'rejection', 'recruiter_screen', or 'unknown'.

    Body phrases take priority. Recruiter screen is checked before confirmation
    because scheduling language is unambiguous and some forward-moving phrases
    ("we'd like to move forward") could otherwise collide with confirmation.
    When the body is ambiguous, the subject tiebreaker runs. If still unknown
    and an llm_client is provided, Haiku is called as a last-resort classifier.
    """
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

    # Subject-line tiebreaker for empty/unusual bodies
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
# Extraction
# ---------------------------------------------------------------------------

def _clean_company(raw: str) -> str | None:
    """Normalize and validate a raw company string extracted from subject or body.

    Returns None if the extracted string looks like a job title rather than a company name.
    """
    company = raw.strip().rstrip("!").strip()
    company = re.sub(r",\s+[A-Z][a-z]+$", "", company)       # trailing ", Lomis"
    company = _COMPANY_TRAILING_NOISE.sub("", company)         # trailing "- Lomis Chen"
    company = _COMPANY_LEADING_THE.sub("", company)            # leading "the "
    company = _COMPANY_SENDER_SUFFIX.sub("", company)          # trailing "Recruiting", "Talent"
    company = company.strip()
    if not company or len(company) < 2:
        return None
    # If the string contains colon or looks like a role title, discard it — we caught too much
    if ":" in company:
        company = company.split(":")[0].strip()
    if _ROLE_TITLE_WORDS.search(company) and len(company.split()) >= 2:
        return None
    return company


def extract_company(subject: str, body: str) -> str | None:
    """Extract company name from subject (primary) or body (fallback)."""
    # Normalize line-folds in subject (MIME headers can wrap mid-phrase)
    subject = " ".join(subject.split())

    # ATS-specific patterns that would otherwise trip up generic patterns
    # "{COMPANY} Job Application Update: {ID} {ROLE}" (Workday / GE HealthCare)
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
    r"time to apply for our|taking the time",
    re.IGNORECASE,
)


def _clean_role(raw: str) -> str | None:
    """Normalize and validate a raw role string."""
    role = raw.strip()
    # Drop ATS job IDs: "(ID: 10443018)", "JR2018175 Senior...", "200022543"
    role = re.sub(r"\s*[\(\[]?(?:ID|JR|REQ)[:\s]\S+[\)\]]?", "", role, flags=re.IGNORECASE)
    role = re.sub(r"^\w{2,}\d{6,}\s+", "", role)   # leading alphanumeric job codes
    role = re.sub(r"\s+\d{6,}$", "", role)           # trailing numeric job IDs
    role = role.strip().rstrip(".,")
    if len(role) < 4 or len(role) > _ROLE_MAX_LEN:
        return None
    if _ROLE_BOILERPLATE.search(role):
        return None
    return role


def extract_role(subject: str, body: str) -> str | None:
    """Extract role title from body (primary) or subject (fallback)."""
    subject = " ".join(subject.split())  # normalize line-folds
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

    # Sender domain fallback: "no-reply@databricks.com" → "Databricks"
    # Only used when subject/body extraction failed, and domain isn't a generic ESP
    if not company:
        company = _company_from_sender(email_dict.get("sender", ""))

    role    = extract_role(subject, body)
    log.debug("track: parse  [%s] company=%r | %s", classification[:4], company, subject[:70])

    return {
        "classification": classification,
        "company":        company,
        "role":           role,
        "date":           email_dict["date"],
        "subject":        subject,
        "sender":         email_dict["sender"],
    }


_GENERIC_SENDER_DOMAINS = {
    # Email providers
    "gmail", "googlemail", "yahoo", "hotmail", "outlook", "icloud",
    # ATS platforms — emails come "from" these but the company is in the display name
    "ashbyhq", "ashby", "greenhouse-mail", "greenhouse",
    "lever", "smartrecruiters", "icims", "jobvite", "taleo",
    "bamboohr", "myworkdayjobs", "myworkday", "successfactors", "applytojob",
    "talentplatform", "workday",
    # Generic sender names
    "notifications", "noreply", "no-reply", "mailer", "bounce", "mail",
}

def _company_from_sender(sender: str) -> str | None:
    """Extract company name from sender display name or email domain.

    Tries in order:
    1. Display name pattern: "Console Hiring Team" → "Console"
    2. Email domain: "careers@databricks.com" → "Databricks"
    Ignores generic ATS and ESP domains.
    """
    # 1. Display name: "Console Hiring Team <...>" or "EvenUp Talent Team <...>"
    display_m = re.match(r'^"?([^"<]+?)"?\s*(?:<|$)', sender)
    if display_m:
        display = display_m.group(1).strip()
        # Strip trailing " Hiring Team", " Talent Team", " Careers", " Recruiting", etc.
        company = re.sub(
            r'\s+(?:hiring team|talent team|talent acquisition|recruiting team|'
            r'careers|recruiter|hr|talent|@ \w+)$',
            "", display, flags=re.IGNORECASE
        ).strip()
        # Strip trailing legal suffixes and punctuation
        company = re.sub(r',?\s+(?:inc|llc|corp|ltd)\.?$', "", company, flags=re.IGNORECASE).strip()
        # Reject personal names: "Laura Otto", "John Smith" (2 title-case words, no digits)
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

    # 2. Email domain fallback
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


# ---------------------------------------------------------------------------
# Fuzzy matching against tracker rows
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def find_tracker_row(ws, company: str, role: str | None) -> int | None:
    """Return 1-based row index of the best-matching row, or None.

    Matching strategy:
      1. Company must match above COMPANY_THRESHOLD (fuzzy)
      2. If role is provided, title must match above ROLE_THRESHOLD (fuzzy)
      3. Return the highest-scoring match
    """
    COMPANY_THRESHOLD = 0.70
    ROLE_THRESHOLD    = 0.55   # lower because ATS titles drift from LinkedIn titles

    best_row   = None
    best_score = 0.0

    for row_idx in range(2, ws.max_row + 1):
        row_company = str(ws.cell(row_idx, 3).value or "")
        row_title   = str(ws.cell(row_idx, 2).value or "")

        company_score = _similarity(company, row_company)
        if company_score < COMPANY_THRESHOLD:
            continue

        if role:
            role_score = _similarity(role, row_title)
            if role_score < ROLE_THRESHOLD:
                continue
            combined = (company_score + role_score) / 2
        else:
            combined = company_score

        if combined > best_score:
            best_score = combined
            best_row   = row_idx

    return best_row


# ---------------------------------------------------------------------------
# Tracker updates
# ---------------------------------------------------------------------------

def _apply_status_fill(ws, row_idx: int, col: int, value: str) -> None:
    from openpyxl.styles import PatternFill
    _STATUS_FILL = {
        "Applied":          "C6EFCE",
        "Not Applied":      "D9D9D9",
        "Pending":          "FFEB9C",
        "Proceeding":       "C6EFCE",
        "Rejected":         "FFC7CE",
        "Skipped":          "D9D9D9",
        "Recruiter Screen": "BDD7EE",  # light blue
    }
    color = _STATUS_FILL.get(value)
    if color:
        ws.cell(row_idx, col).fill = PatternFill(fill_type="solid", fgColor=color)


def update_confirmation(ws, row_idx: int, date_applied: str) -> None:
    """Flip action_taken → Applied and set date_applied + application_status."""
    ws.cell(row_idx, 6).value = "Applied"
    ws.cell(row_idx, 7).value = date_applied
    ws.cell(row_idx, 8).value = "Pending"
    _apply_status_fill(ws, row_idx, 6, "Applied")
    _apply_status_fill(ws, row_idx, 8, "Pending")


def update_rejection(ws, row_idx: int) -> None:
    """Set application_status → Rejected."""
    ws.cell(row_idx, 8).value = "Rejected"
    _apply_status_fill(ws, row_idx, 8, "Rejected")


def update_recruiter_screen(ws, row_idx: int) -> None:
    """Set action_taken → Applied (if not already) and application_status → Recruiter Screen."""
    if ws.cell(row_idx, 6).value != "Applied":
        ws.cell(row_idx, 6).value = "Applied"
        _apply_status_fill(ws, row_idx, 6, "Applied")
    ws.cell(row_idx, 8).value = "Recruiter Screen"
    _apply_status_fill(ws, row_idx, 8, "Recruiter Screen")


def _write_row_from_email(ws, parsed: dict, suggestion_status: str,
                          date_suggested: str | None = None,
                          match_score: float | None = None,
                          url: str = "") -> None:
    """Shared helper: append one row to ws from parsed email data."""
    from openpyxl.styles import Alignment
    from .tracker import _set_hyperlink

    next_row = ws.max_row + 1
    values = [
        date_suggested or parsed["date"],
        parsed.get("role") or "",
        parsed.get("company") or "",
        match_score,
        suggestion_status,
        "Applied",
        parsed["date"],
        "Pending",
        None,
    ]
    for col_idx, value in enumerate(values, start=1):
        ws.cell(next_row, col_idx, value).alignment = Alignment(vertical="top")

    if url:
        _set_hyperlink(ws.cell(next_row, 2), url, values[1])

    ws.cell(next_row, 4).number_format = "0%"
    for col, val in [(5, suggestion_status), (6, "Applied"), (8, "Pending")]:
        _apply_status_fill(ws, next_row, col, val)


def create_skipped_row(ws, parsed: dict, skipped_meta: dict) -> None:
    """Create a new row for a skipped role the user applied to anyway."""
    score = skipped_meta.get("match_score")
    _write_row_from_email(
        ws, parsed,
        suggestion_status="Skipped",
        date_suggested=skipped_meta.get("date_suggested"),
        match_score=(score / 100.0) if score else None,
        url=skipped_meta.get("url", ""),
    )
    # Prefer stored role title / company over email-extracted ones
    next_row = ws.max_row
    if skipped_meta.get("role_title"):
        ws.cell(next_row, 2).value = skipped_meta["role_title"]
    if skipped_meta.get("company"):
        ws.cell(next_row, 3).value = skipped_meta["company"]


def create_backfill_row(ws, parsed: dict) -> None:
    """Create a row for a confirmed application with no prior tracker entry.

    Used for roles applied to before the tracker existed, or applied to outside
    the scorerole digest. suggestion_status='Pre-tracker' marks these as backfills.
    date_suggested is left blank — we don't know when (or if) scorerole suggested it.
    """
    _write_row_from_email(ws, parsed, suggestion_status="Pre-tracker")


# ---------------------------------------------------------------------------
# Digest backfill — parse past "Personalized Job Alert Digest" emails
# ---------------------------------------------------------------------------

def _parse_digest_html(html: str, email_date: str) -> list[dict]:
    """Extract Apply/Consider job rows from a rendered digest HTML email.

    Returns a list of job dicts compatible with write_to_tracker():
    {'title', 'company', 'url', 'eval': {'score', 'verdict'}}
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    # ── Prefer embedded JSON data island (added to future digests) ──────────
    tag = soup.find("script", {"type": "application/json", "id": "scorerole-data"})
    if tag and tag.string:
        import json as _json
        try:
            payload = _json.loads(tag.string)
            jobs = []
            for j in payload.get("jobs", []):
                verdict = j.get("verdict", "").lower()
                if verdict not in ("apply", "consider"):
                    continue
                jobs.append({
                    "title":   j.get("title", ""),
                    "company": j.get("company", ""),
                    "url":     j.get("postingUrl", ""),
                    "eval":    {"score": j.get("score", 0), "verdict": verdict},
                })
            return jobs
        except Exception:
            pass  # fall through to HTML parsing

    # ── HTML fallback: parse job cards by "View posting" links ──────────────
    # React Email renders: title in <td font-size:15px font-weight:500>,
    # company in <p color:#72716d>, score in <span border-radius:20px>.
    # Python fallback uses slightly different colors but same structure.
    # Both use "View posting" anchor text to identify job cards.

    def _nstyle(tag) -> str:
        """Normalize a tag's style for robust substring matching."""
        return re.sub(r"\s+", "", (tag.get("style") or "").lower())

    # Known muted colors across React Email and Python fallback renderers.
    _MUTED = ("72716d", "888780", "726f6a", "6b6b6b", "757575", "666666")

    jobs = []
    for anchor in soup.find_all("a"):
        if "View posting" not in anchor.get_text():
            continue
        url = anchor.get("href", "")
        # Walk up to find the enclosing card table — the card has border-radius:8px.
        # find_parent("table") would grab the inner footer table, not the card.
        card = anchor.find_parent(
            "table",
            style=lambda s: s and "border-radius:8px" in s.replace(" ", "")
        )
        if not card:
            continue

        # Title: font-size 15px with bold/500/600 weight.
        # _nstyle() strips spaces so "font-size: 15px" and "font-size:15px" both match.
        # Expanded tag names cover <td>, <div>, <p>, <span> across renderer variants.
        title_td = card.find(
            lambda t: t.name in ("td", "div", "p", "span")
            and "15px" in _nstyle(t)
            and any(w in _nstyle(t) for w in ("font-weight:500", "font-weight:600",
                                               "font-weight:bold"))
        )
        title = title_td.get_text(strip=True) if title_td else ""

        # Company+location: muted color element (primary), or "·" separator (fallback).
        # Primary handles both React Email and Python fallback color palettes.
        # Fallback catches any renderer variant where the color doesn't match exactly.
        co_tag = card.find(
            lambda t: t.name in ("p", "div", "td", "span")
            and any(c in _nstyle(t) for c in _MUTED)
            and t is not title_td
        )
        if not co_tag or not co_tag.get_text(strip=True):
            # Structural fallback: find a leaf-ish element containing "·".
            # Two guards prevent matching a card container:
            #   - t not in title_td.parents → skip ancestors of the title element
            #   - len < 120 → skip container elements whose text spans the whole card
            co_tag = card.find(
                lambda t: t.name in ("p", "div", "td", "span")
                and "·" in t.get_text()
                and t is not title_td
                and (title_td is None or t not in title_td.parents)
                and len(t.get_text(strip=True)) < 120
            )
        company_raw = co_tag.get_text(strip=True) if co_tag else ""
        company = company_raw.split("·")[0].strip()

        # Score: span with border-radius:20px containing "\d+%"
        score_span = card.find(
            lambda t: t.name == "span"
            and "border-radius:20px" in (t.get("style") or "").replace(" ", "")
            and re.search(r"\d+%", t.get_text())
        )
        score = 0
        if score_span:
            m = re.search(r"(\d+)%", score_span.get_text())
            if m:
                score = int(m.group(1))

        # Verdict: from pill background color
        # React Email:    apply=#eef2ee  consider=#f4f0e8
        # Python fallback: apply=#EAF3DE consider=#FAEEDA
        style = (score_span.get("style", "") if score_span else "").lower()
        if "eef2ee" in style or "eaf3de" in style:
            verdict = "apply"
        elif "f4f0e8" in style or "faeeda" in style:
            verdict = "consider"
        else:
            verdict = "consider"   # safe default

        if title and company:
            jobs.append({
                "title":   title,
                "company": company,
                "url":     url,
                "eval":    {"score": score, "verdict": verdict},
            })

    return jobs


def backfill_from_digests(
    gmail_address: str,
    app_password: str,
    since_dt: datetime.datetime,
) -> int:
    """Fetch past digest emails from Gmail and write any new rows to the tracker.

    Only writes Apply/Consider roles not already in the tracker (dedup by
    normalized title+company). Returns the number of new rows added.
    """
    from email.utils import parsedate_to_datetime
    from .tracker import write_to_tracker

    since_str = since_dt.strftime("%d-%b-%Y")
    added_total = 0

    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(gmail_address, app_password)
        imap.select("INBOX")

        _, data = imap.search(None, f'SINCE {since_str} SUBJECT "Personalized Job Alert Digest"')
        if not data or not data[0]:
            log.info("track: no digest emails found since %s", since_str)
            return 0

        digest_ids = data[0].split()
        n_digests = len(digest_ids)
        log.info("track: found %d digest email(s) to backfill from", n_digests)

        for msg_id in digest_ids:
            _, raw = imap.fetch(msg_id, "(RFC822)")
            if not raw or not raw[0]:
                continue
            raw_bytes = raw[0][1] if isinstance(raw[0], tuple) else raw[0]
            msg = email_lib.message_from_bytes(raw_bytes)

            date_header = msg.get("Date", "")
            try:
                email_date = parsedate_to_datetime(date_header).date().isoformat()
            except Exception:
                email_date = datetime.date.today().isoformat()

            # Extract HTML body from digest email
            html_body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        payload = part.get_payload(decode=True)
                        if payload:
                            html_body = _safe_decode(payload, part.get_content_charset())
                            break
            else:
                if msg.get_content_type() == "text/html":
                    payload = msg.get_payload(decode=True)
                    if payload:
                        html_body = _safe_decode(payload, msg.get_content_charset())

            if not html_body:
                log.debug("track: digest %s has no HTML body — skipping", email_date)
                continue

            jobs = _parse_digest_html(html_body, email_date)
            if jobs:
                log.info("track: digest %s → %d role(s) parsed", email_date, len(jobs))
                write_to_tracker(jobs, run_date=email_date)
                added_total += len(jobs)
            else:
                log.warning("track: digest %s → 0 roles parsed (HTML structure mismatch?)", email_date)

    log.info("track: digest backfill complete — %d role(s) eligible across %d digest(s)",
             added_total, n_digests)
    return added_total


# ---------------------------------------------------------------------------
# LLM client factory
# ---------------------------------------------------------------------------

def _build_llm_client(api_key: str | None):
    """Return an Anthropic client if profile.yaml has track.llm_fallback: true.

    Returns None when the flag is absent, false, or the api_key is missing —
    so classify_email() runs phrase-only (safe for OSS users with no key).
    """
    if not api_key:
        return None
    try:
        from .profile import load_profile_yaml
        profile = load_profile_yaml() or {}
    except Exception:
        return None
    if not profile.get("track", {}).get("llm_fallback", False):
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        log.info("track: LLM fallback enabled (Haiku) for unknown emails")
        return client
    except Exception as exc:
        log.warning("track: could not build LLM client (%s) — running phrase-only", exc)
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_track(
    gmail_address: str,
    app_password: str,
    since_dt: datetime.datetime,
    dry_run: bool = False,
    api_key: str | None = None,
) -> None:
    """Parse confirmation/rejection emails and update the Applications tracker."""
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("openpyxl is required: pip install openpyxl")

    from .tracker import TRACKER_PATH
    from .state   import lookup_skipped_role, promote_skipped_role

    # Build LLM client if the profile has llm_fallback enabled
    llm_client = _build_llm_client(api_key)

    # Step 1: backfill tracker from past digest emails FIRST so every suggested
    # role has a proper row (with score, URL, suggestion_status) before we try
    # to match confirmation/rejection emails against tracker rows.
    log.info("track: step 1 — backfilling tracker from digest emails…")
    backfill_from_digests(gmail_address, app_password, since_dt)

    # Step 2: parse confirmation / rejection emails and update tracker rows
    emails = fetch_candidate_emails(gmail_address, app_password, since_dt)
    if not emails:
        log.info("track: no candidate emails found in lookback window.")
        return

    parsed_emails = [parse_email(e, llm_client=llm_client) for e in emails]

    actionable = [p for p in parsed_emails
                  if p["classification"] in ("confirmation", "rejection", "recruiter_screen")
                  and p["company"]]
    unknown    = [p for p in parsed_emails if p["classification"] == "unknown"]

    log.info("track: %d actionable (%d confirmation, %d rejection, %d recruiter_screen), %d unknown",
             len(actionable),
             sum(1 for p in actionable if p["classification"] == "confirmation"),
             sum(1 for p in actionable if p["classification"] == "rejection"),
             sum(1 for p in actionable if p["classification"] == "recruiter_screen"),
             len(unknown))

    if unknown:
        for u in unknown:
            log.warning("track: unclassified — '%s' (company=%s)", u["subject"], u["company"])

    if dry_run:
        for p in parsed_emails:
            print(f"[{p['classification'].upper():12}] {p['company'] or '?':25} | {p['role'] or '?'}")
        return

    if TRACKER_PATH.exists():
        wb = openpyxl.load_workbook(TRACKER_PATH)
        ws = wb.active
    else:
        from .tracker import _write_header, _set_column_widths
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Applications"
        _write_header(ws)
        _set_column_widths(ws)
        ws.freeze_panes = "A2"
        log.info("track: created new tracker at %s", TRACKER_PATH)

    changed = 0

    for parsed in actionable:
        company = parsed["company"]
        role    = parsed["role"]
        kind    = parsed["classification"]

        row_idx = find_tracker_row(ws, company, role)

        if row_idx is None:
            if kind == "confirmation":
                # Check skipped sidecar first (has match_score + date_suggested)
                skipped_meta = lookup_skipped_role(company, role or "")
                if skipped_meta:
                    log.info("track: backfill (skipped) — %s / %s", company, role)
                    create_skipped_row(ws, parsed, skipped_meta)
                    promote_skipped_role(company, role or "")
                else:
                    log.info("track: backfill (pre-tracker) — %s / %s", company, role or "?")
                    create_backfill_row(ws, parsed)
                changed += 1
            else:
                log.info("track: skip rejection (no tracker row) — %s", company)
            continue

        current_action = ws.cell(row_idx, 6).value   # action_taken
        current_status = ws.cell(row_idx, 8).value   # application_status

        if kind == "confirmation" and current_action != "Applied":
            log.info("track: ✓ confirmation — %s / %s → Applied + Pending", company, role or "?")
            update_confirmation(ws, row_idx, parsed["date"])
            changed += 1
        elif kind == "confirmation" and current_action == "Applied":
            log.info("track: skip confirmation (already Applied) — %s", company)
        elif kind == "rejection" and current_status != "Rejected":
            log.info("track: ✗ rejection  — %s / %s → Rejected", company, role or "?")
            update_rejection(ws, row_idx)
            changed += 1
        elif kind == "rejection" and current_status == "Rejected":
            log.info("track: skip rejection (already Rejected) — %s", company)
        elif kind == "recruiter_screen" and current_status not in ("Recruiter Screen", "Rejected"):
            log.info("track: ★ recruiter screen — %s / %s → Recruiter Screen", company, role or "?")
            update_recruiter_screen(ws, row_idx)
            changed += 1
        elif kind == "recruiter_screen":
            log.info("track: skip recruiter screen (already %s) — %s", current_status, company)

    from .tracker import _sort_rows_by_date
    _sort_rows_by_date(ws)   # always re-sort so date order is maintained

    if changed:
        wb.save(TRACKER_PATH)
        TRACKER_PATH.chmod(0o600)
        log.info("track: updated %d row(s) in %s", changed, TRACKER_PATH)
        print(f"  Tracker → {TRACKER_PATH}")
        if sys.stdout.isatty():
            import subprocess
            subprocess.Popen(["open", str(TRACKER_PATH)])
    else:
        wb.save(TRACKER_PATH)   # save even if no updates — sort may have reordered rows
        TRACKER_PATH.chmod(0o600)
        log.info("track: no updates needed.")
