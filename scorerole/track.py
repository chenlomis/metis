"""track.py — parse confirmation and rejection emails, update the Applications tracker.

Usage:
    scorerole track                  # parse emails from last 7 days
    scorerole track --since 14d      # extend lookback
    scorerole track --dry-run        # parse + print matches, no writes

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
    "we've received your application",              # curly-apostrophe variant
    "we’ve received your application",
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
    r"thank you for (applying|your application)|"
    r"thanks for (applying|your application|completing your application|submitting)|"
    r"your application for .+ at |"
    r"we('ve| have) received your application|"
    r"we have received your application|"
    r"thank you for your application to|"
    r"your application to \w",
    re.IGNORECASE,
)

# Subject patterns that strongly imply rejection (used as body tiebreaker)
_SUBJECT_IMPLIES_REJECTION = re.compile(
    r"thank you from |"           # NVIDIA, Elastic, Workato
    r"following up on your (application|recent application)|"
    r"application (status )?update|"
    r"update on your .+ application",
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
_COMPANY_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)

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
    _SUBJECT_SEARCHES = [
        "thank you for apply",
        "thank you for your application",
        "thanks for apply",
        "your application",
        "following up on your",
        "application update",
        "thank you from",
        "we got it",
        "keep track of your application",
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
            try:
                email_date = parsedate_to_datetime(date_header).date().isoformat()
            except Exception:
                email_date = datetime.date.today().isoformat()

            body = _extract_body(msg)
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


def classify_email(body: str, subject: str = "") -> str:
    """Return 'confirmation', 'rejection', or 'unknown'.

    Body phrases take priority. When the body is ambiguous (no phrase matches),
    the subject line is used as a tiebreaker — it's often unambiguous even when
    the body text uses unusual phrasing.
    """
    body_norm = _normalize_body(body)

    # Body-based signals take priority (most reliable)
    for phrase in _REJECTION_PHRASES:
        if phrase in body_norm:
            return "rejection"
    for phrase in _CONFIRMATION_PHRASES:
        if phrase in body_norm:
            return "confirmation"

    # Subject-line tiebreaker for empty/unusual bodies
    if _SUBJECT_IMPLIES_CONFIRMATION.search(subject):
        return "confirmation"
    if _SUBJECT_IMPLIES_REJECTION.search(subject):
        return "rejection"

    return "unknown"


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _clean_company(raw: str) -> str:
    """Normalize a raw company string extracted from subject or body."""
    company = raw.strip().rstrip("!").strip()
    company = re.sub(r",\s+[A-Z][a-z]+$", "", company)       # trailing ", Lomis"
    company = _COMPANY_TRAILING_NOISE.sub("", company)         # trailing "- Lomis Chen"
    company = _COMPANY_LEADING_THE.sub("", company)            # leading "the "
    return company.strip()


def extract_company(subject: str, body: str) -> str | None:
    """Extract company name from subject (primary) or body (fallback)."""
    # Normalize line-folds in subject (MIME headers can wrap mid-phrase)
    subject = " ".join(subject.split())

    for pattern in _COMPANY_FROM_SUBJECT:
        m = pattern.search(subject)
        if m:
            company = _clean_company(m.group(1))
            if len(company) > 1:
                return company

    for pattern in _COMPANY_FROM_BODY:
        m = pattern.search(body)
        if m:
            return _clean_company(m.group(1))

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


def parse_email(email_dict: dict) -> dict:
    """Classify and extract structured fields from a candidate email."""
    subject = email_dict["subject"]
    body    = email_dict["body"]

    classification = classify_email(body, subject)
    company = extract_company(subject, body)
    role    = extract_role(subject, body)

    return {
        "classification": classification,
        "company":        company,
        "role":           role,
        "date":           email_dict["date"],
        "subject":        subject,
        "sender":         email_dict["sender"],
    }


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
        "Applied":     "C6EFCE",
        "Not Applied": "D9D9D9",
        "Pending":     "FFEB9C",
        "Proceeding":  "C6EFCE",
        "Rejected":    "FFC7CE",
        "Skipped":     "D9D9D9",
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
# Main entry point
# ---------------------------------------------------------------------------

def run_track(gmail_address: str, app_password: str, since_dt: datetime.datetime, dry_run: bool = False) -> None:
    """Parse confirmation/rejection emails and update the Applications tracker."""
    try:
        import openpyxl
    except ImportError:
        raise SystemExit("openpyxl is required: pip install openpyxl")

    from .tracker import TRACKER_PATH
    from .state   import lookup_skipped_role, promote_skipped_role

    emails = fetch_candidate_emails(gmail_address, app_password, since_dt)
    if not emails:
        log.info("track: no candidate emails found in lookback window.")
        return

    parsed_emails = [parse_email(e) for e in emails]

    actionable = [p for p in parsed_emails if p["classification"] in ("confirmation", "rejection")
                  and p["company"]]
    unknown    = [p for p in parsed_emails if p["classification"] == "unknown"]

    log.info("track: %d actionable (%d confirmation, %d rejection), %d unknown",
             len(actionable),
             sum(1 for p in actionable if p["classification"] == "confirmation"),
             sum(1 for p in actionable if p["classification"] == "rejection"),
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
                log.debug("track: no match for rejection — company=%s (subject: %s)",
                          company, parsed["subject"])
            continue

        current_action = ws.cell(row_idx, 6).value   # action_taken
        current_status = ws.cell(row_idx, 8).value   # application_status

        if kind == "confirmation" and current_action != "Applied":
            log.info("track: ✓ confirmation — %s / %s → Applied + Pending", company, role or "?")
            update_confirmation(ws, row_idx, parsed["date"])
            changed += 1
        elif kind == "confirmation" and current_action == "Applied":
            log.debug("track: already Applied — skipping confirmation for %s", company)
        elif kind == "rejection" and current_status != "Rejected":
            log.info("track: ✗ rejection  — %s / %s → Rejected", company, role or "?")
            update_rejection(ws, row_idx)
            changed += 1
        elif kind == "rejection" and current_status == "Rejected":
            log.debug("track: already Rejected — skipping for %s", company)

    if changed:
        wb.save(TRACKER_PATH)
        TRACKER_PATH.chmod(0o600)
        log.info("track: updated %d row(s) in %s", changed, TRACKER_PATH)
        print(f"  Tracker → {TRACKER_PATH}")
        if sys.stdout.isatty():
            import subprocess
            subprocess.Popen(["open", str(TRACKER_PATH)])
    else:
        log.info("track: no updates needed.")
