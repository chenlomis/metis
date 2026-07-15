from __future__ import annotations
import re, json, imaplib, email, datetime, logging, time
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Credentials read from env — set via .env (see .env.example)
import os as _os
_GMAIL_ADDRESS_ENV      = _os.getenv("GMAIL_ADDRESS", "")
_GMAIL_APP_PASSWORD_ENV = _os.getenv("GMAIL_APP_PASSWORD", "")

# All three LinkedIn alert senders, nested OR for IMAP:
#   jobalerts-noreply@ → standard "Your job alert for X" digests
#   jobs-noreply@       → "Company is hiring" / "Jobs similar to X" recommendation emails
#   jobs-listings@      → "Jobs you might like" (JYMBII) digests
_LINKEDIN_SENDER_SEARCH = (
    'OR OR FROM "jobalerts-noreply@linkedin.com" '
    'FROM "jobs-noreply@linkedin.com" '
    'FROM "jobs-listings@linkedin.com"'
)
_LINKEDIN_SENDERS = [
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
]

_NOISE_LINES = re.compile(
    r"^\d+\s+connections?$"
    r"|actively hiring"
    r"|be an early applicant"
    r"|your job alert"
    r"|^-{3,}$"
    r"|promoted",
    re.IGNORECASE,
)

# LinkedIn shows "N company alum", "N company alumni", "N school alumni" etc.
# Captured separately so we get the count AND keep it out of title/company/location.
_ALUMNI_LINE = re.compile(
    r"^(\d+)\s+(?:company\s+|school\s+)?alumn?i?$",
    re.IGNORECASE,
)

# Company name with legal suffix that bleeds into the title slot
_CO_SUFFIX = re.compile(r',\s*(Inc\.?|LLC|Corp\.?|Ltd\.?|GmbH|S\.A\.|PBC)$', re.I)

# Used for shift detection: company slot contains a bare location string
# (city, state, region) rather than a company name.
# Anchored ^…$ so "San Francisco Health" and "New York Times" are NOT matched.
_LOCATION_LIKE = re.compile(
    r"^(?:"
    r"san\s+francisco|new\s+york(?:\s+city)?|los\s+angeles|seattle|"
    r"austin|boston|chicago|denver|atlanta|miami|new\s+jersey|"
    r"bay\s+area|silicon\s+valley|remote|united\s+states|"
    r"greater\s+\w+(?:\s+\w+)?\s+area|"
    r"[a-z\s]+,\s*(?:CA|NY|WA|TX|MA|IL|CO|GA|FL|OR|VA|NC|AZ|OH|PA|"
    r"NJ|MN|MI|MO|IN|TN|UT|MD|WI|SC|NV|CT|LA|AL|AR|IA|KS|KY|ME|MS|"
    r"MT|NE|NH|NM|ND|OK|RI|SD|VT|WV|WY|DC|HI|AK|ID|DE)"
    r")$",
    re.IGNORECASE,
)

# Used for shift detection: a real job title contains at least one of these words.
# A company name or bare location will not match.
_TITLE_JOB_KEYWORDS = re.compile(
    r"\b(?:manager|director|lead|head|officer|president|vp|principal|"
    r"staff|senior|associate|engineer|analyst|scientist|designer|"
    r"architect|specialist|coordinator|owner|product|program|project|"
    r"operations|strategy|general\s+manager)\b",
    re.IGNORECASE,
)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Matches LinkedIn job view URLs in both plain text and HTML href attributes
_JOB_URL_RE = re.compile(r"https://www\.linkedin\.com/comm/jobs/view/(\d+)/")

# Matches "Company · Location" lines in HTML recommendation emails
_COMPANY_LOC_RE = re.compile(r"^(.+?)\s+·\s+(.+)$")

# LinkedIn action-button text that bleeds into the location slot in recommendation emails
# e.g. "Apply with resume & profile" or "Easy Apply"
_LOCATION_GARBAGE_RE = re.compile(
    r"^(apply with\b|easy apply)",
    re.IGNORECASE,
)

# Short strings that appear as link text but are not job titles
_NON_TITLE_LABELS = frozenset({
    "view all jobs", "easy apply", "linkedin", "apply", "see more",
    "expand your search", "remote jobs", "recommendations based on your activity",
})


def _sanitize_location(loc: str) -> str:
    """Strip LinkedIn CTA text that bleeds into the location field.

    LinkedIn recommendation emails include 'Apply with resume & profile' as a
    UI element right after the company·location line, and the HTML scraper
    sometimes captures it as location. Strip it so jobs show a real location or empty.
    """
    if not loc:
        return loc
    stripped = loc.strip()
    if _LOCATION_GARBAGE_RE.match(stripped):
        return ""
    # Strip trailing '· Apply with...' suffix (e.g. 'United States · Apply with resume')
    cleaned = re.sub(
        r"\s*·\s*(apply with|easy apply)\b.*$", "", stripped, flags=re.IGNORECASE
    ).strip()
    return cleaned


def _extract_text(msg) -> str:
    """Return the best plain-text representation of the email body."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
        # Fallback to HTML→text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return BeautifulSoup(
                        payload.decode("utf-8", errors="ignore"), "html.parser"
                    ).get_text(separator="\n")
    payload = msg.get_payload(decode=True)
    return payload.decode("utf-8", errors="ignore") if payload else ""


def _get_html_body(msg) -> str:
    """Return the raw HTML body of the email, or empty string if none."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
    payload = msg.get_payload(decode=True)
    if payload:
        decoded = payload.decode("utf-8", errors="ignore")
        if "<html" in decoded[:500].lower():
            return decoded
    return ""


def extract_jobs(body: str) -> list[dict]:
    """Parse jobs from standard LinkedIn alert emails (plain-text 'View job:' format)."""
    # Anchor on "View job: URL", look backwards for title / company / location.
    # Alumni-count lines are captured separately before noise filtering so they
    # don't displace the title from the -3 slot.
    jobs, seen = [], set()
    url_re = re.compile(
        r"View job:\s+(https://www\.linkedin\.com/comm/jobs/view/(\d+)/\S+)"
    )
    for m in url_re.finditer(body):
        job_id = m.group(2)
        if job_id in seen:
            continue

        alumni_count: int | None = None
        before_lines: list[str] = []
        for ln in body[: m.start()].splitlines():
            stripped = ln.strip()
            if not stripped:
                continue
            am = _ALUMNI_LINE.match(stripped)
            if am:
                alumni_count = int(am.group(1))  # capture; don't add to layout lines
            elif not _NOISE_LINES.search(stripped):
                before_lines.append(stripped)

        if len(before_lines) < 3:
            continue

        # Default positional assignment: bottom-up = location, company, title
        location = before_lines[-1]
        company  = before_lines[-2]
        title    = before_lines[-3]

        # ── Shift detection (runs in priority order, mutually exclusive) ──────
        #
        # Case A — trailing garbage line pushed all three fields down one slot.
        # Signal: the "company" slot contains a bare city/state/region string.
        # Fix: shift all three fields up by one.
        if _LOCATION_LIKE.match(company.strip()) and len(before_lines) >= 4:
            location = before_lines[-2]
            company  = before_lines[-3]
            title    = before_lines[-4]
        else:
            # Case B — company name with legal suffix bled into the title slot
            # (e.g. "Qventus, Inc"). Fix: title shifts up one.
            if _CO_SUFFIX.search(title) and len(before_lines) >= 4:
                title = before_lines[-4]
            # Case C — company/location name in title slot, no legal suffix.
            # Signal: title has no job-role keywords. Only shift if the slot
            # above does contain job keywords (avoids false positives).
            elif not _TITLE_JOB_KEYWORDS.search(title) and len(before_lines) >= 4:
                candidate = before_lines[-4]
                if _TITLE_JOB_KEYWORDS.search(candidate):
                    title = candidate
        seen.add(job_id)
        jobs.append({
            "title":        title,
            "company":      company,
            "location":     _sanitize_location(location),
            "alumni_count": alumni_count,
            "job_id":       job_id,
            "url":          f"https://www.linkedin.com/jobs/view/{job_id}/",
        })
    return jobs


def extract_jobs_html(html: str) -> list[dict]:
    """Parse jobs from HTML recommendation emails ('Company is hiring' / 'Similar jobs').

    These emails embed job links in <a href> tags without a plain-text 'View job:' line.
    Company and location appear as 'Company · Location' text adjacent to each job link.
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag.get("href", "")
        m = _JOB_URL_RE.search(href)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen:
            continue

        title = a_tag.get_text(strip=True)
        # Filter out navigation links, buttons, logo links, etc.
        if not title or len(title) < 6 or title.lower() in _NON_TITLE_LABELS:
            continue

        # Locate "Company · Location" in the text immediately following the link.
        # Strategy 1: direct next siblings of the <a> tag
        company, location = _find_company_location_siblings(a_tag)

        # Strategy 2: text lines within the parent container
        if not company:
            company, location = _find_company_location_container(a_tag, title)

        if not company:
            log.debug(f"HTML extract: skipping '{title}' — could not find company/location")
            continue

        seen.add(job_id)
        jobs.append({
            "title":        title,
            "company":      company,
            "location":     _sanitize_location(location),
            "alumni_count": None,
            "job_id":       job_id,
            "url":          f"https://www.linkedin.com/jobs/view/{job_id}/",
        })

    return jobs


def _find_company_location_siblings(a_tag) -> tuple[str, str]:
    """Search direct next siblings of <a> for 'Company · Location' text."""
    for node in a_tag.next_siblings:
        text = (
            node.get_text(separator=" ", strip=True)
            if hasattr(node, "get_text")
            else str(node).strip()
        )
        if not text:
            continue
        cm = _COMPANY_LOC_RE.match(text)
        if cm:
            return cm.group(1).strip(), cm.group(2).strip()
        # Stop if we hit another job link — we've passed this job's block
        if hasattr(node, "find") and node.find("a", href=_JOB_URL_RE):
            break
    return "", ""


def _find_company_location_container(a_tag, title: str) -> tuple[str, str]:
    """Walk up to the nearest container and search its text lines."""
    container = a_tag.parent
    # Step up one level if the parent is too narrow to hold both title and company
    if container and len(container.get_text(strip=True)) < len(title) + 4:
        container = container.parent
    if not container:
        return "", ""
    for line in container.get_text(separator="\n").splitlines():
        line = line.strip()
        if not line or line == title:
            continue
        cm = _COMPANY_LOC_RE.match(line)
        if cm:
            return cm.group(1).strip(), cm.group(2).strip()
    return "", ""


def _apply_mode_from_html(html: str) -> str:
    lowered = (html or "").lower()
    if "no longer accepting applications" in lowered:
        return "closed"
    if "offsite-apply" in lowered or "offsite_apply" in lowered:
        return "offsite"
    if "easy apply" in lowered or "easy-apply" in lowered or "easy_apply" in lowered:
        return "easy_apply"
    return "unknown"


def _fetch_one_jd(job: dict) -> tuple[str, str, str]:
    """Fetch the JD text and external apply URL for a job.

    Returns (jd_text, apply_url). apply_url is the external ATS link
    (Greenhouse, Lever, Ashby, etc.) from the JSON-LD applyAction, or
    empty string for LinkedIn Easy Apply roles.

    Retries up to 3 times with exponential backoff on transient errors (429/5xx).
    """
    try:
        r = None
        for attempt in range(3):
            try:
                r = httpx.get(job["url"], headers=_BROWSER_HEADERS, timeout=12, follow_redirects=True)
            except httpx.TimeoutException:
                log.warning("JD fetch timeout (attempt %d/3): %s at %s", attempt + 1, job["title"], job["url"])
                if attempt < 2:
                    time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                break
            if r.status_code in (429, 500, 502, 503, 504):
                log.warning("JD fetch HTTP %d (attempt %d/3): %s", r.status_code, attempt + 1, job["url"])
                if attempt < 2:
                    time.sleep(2 ** attempt)
            else:
                # Non-retryable status (403, 404, etc.)
                return "", "", "unknown"
        if r is None or r.status_code != 200:
            return "", "", "unknown"
        apply_mode = _apply_mode_from_html(r.text)
        soup = BeautifulSoup(r.text, "html.parser")
        # LinkedIn embeds structured job data in JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") == "JobPosting":
                    raw_desc = data.get("description", "")
                    jd_text  = BeautifulSoup(raw_desc, "html.parser").get_text("\n")[:3000].strip()
                    # applyAction.target is the external ATS URL; absent for LinkedIn Easy Apply
                    apply_url = (
                        data.get("applyAction", {}).get("target", "")
                        or data.get("url", "")
                        or ""
                    )
                    # Reject LinkedIn-internal apply URLs — only keep real external ATS links
                    if "linkedin.com" in apply_url:
                        apply_url = ""
                    return jd_text, apply_url, apply_mode
            except (json.JSONDecodeError, AttributeError):
                continue
        # Fallback: common LinkedIn description containers (no apply URL available here)
        for cls_pat in [r"description__text", r"job-details__main-content", r"show-more-less-html"]:
            el = soup.find(class_=re.compile(cls_pat, re.I))
            if el:
                return el.get_text("\n", strip=True)[:3000], "", apply_mode
    except Exception as e:
        log.warning(f"JD fetch failed ({job['title']} @ {job['company']}): {e}")
    return "", "", "unknown"


def enrich_jobs(jobs: list[dict]) -> list[dict]:
    """Fetch JD text and external apply URL for each job. Sequential with delay.

    Proactive jobs (source='proactive') already have a JD from the ATS API — they
    are skipped to avoid redundant HTTP fetches.
    """
    import time as _time
    linkedin_jobs = [j for j in jobs if j.get("source") != "proactive"]
    for i, job in enumerate(linkedin_jobs):
        jd_text, apply_url, apply_mode = _fetch_one_jd(job)
        job["jd"]        = jd_text
        job["apply_url"] = apply_url
        job["apply_mode"] = apply_mode
        if i < len(linkedin_jobs) - 1:
            _time.sleep(0.4)
    fetched   = sum(1 for j in jobs if j.get("jd"))
    has_apply = sum(1 for j in jobs if j.get("apply_url"))
    proactive = sum(1 for j in jobs if j.get("source") == "proactive")
    log.info(f"JD fetched for {fetched}/{len(jobs)} jobs "
             f"({proactive} proactive, pre-fetched); "
             f"{has_apply} have external apply URL")
    return jobs


def _fetch_emails(imap, search_criteria: str, max_recent: int) -> list[dict]:
    """Fetch and parse emails matching search_criteria, up to max_recent most recent."""
    _, data = imap.search(None, search_criteria)
    all_ids = data[0].split()
    recent = all_ids[-max_recent:] if len(all_ids) > max_recent else all_ids
    log.info(f"Checking {len(recent)} LinkedIn emails (criteria: {search_criteria[:60]}…)")
    threads = []
    for mid in reversed(recent):
        _, raw = imap.fetch(mid, "(RFC822)")
        msg = email.message_from_bytes(raw[0][1])
        msg_id = msg.get("Message-ID", "")
        if not msg_id:
            continue
        body = _extract_text(msg)
        html  = _get_html_body(msg)
        if body or html:
            subject = msg.get("Subject", "")
            try:
                import email.utils as _eu
                email_date = _eu.parsedate_to_datetime(msg.get("Date", "")).isoformat()
            except Exception:
                email_date = datetime.datetime.now().isoformat()
            threads.append({
                "msg_id": msg_id,
                "body": body,
                "html": html,
                "subject": subject,
                "email_date": email_date,
            })
    return threads



_IMAP_MAX_RETRIES = 3
_IMAP_RETRY_DELAY = 30   # seconds between retries on transient network errors


def fetch_linkedin_alerts_since(
    since_dt: datetime.datetime,
    *,
    gmail_address: str = "",
    gmail_app_password: str = "",
) -> list[dict]:
    """Fetch all LinkedIn emails on or after since_dt. Read-only — ignores seen_ids."""
    date_str = since_dt.strftime("%d-%b-%Y")
    criteria = f'{_LINKEDIN_SENDER_SEARCH} SINCE "{date_str}"'
    threads: list[dict] = []

    _addr = gmail_address or _GMAIL_ADDRESS_ENV
    _pwd  = gmail_app_password or _GMAIL_APP_PASSWORD_ENV
    if not (_addr and _pwd):
        try:
            from .email_fetcher import fetch_emails_from_sender, get_provider

            if get_provider() != "imap":
                for sender in _LINKEDIN_SENDERS:
                    for msg in fetch_emails_from_sender(sender, since_dt):
                        body = msg.get("text", "")
                        html = msg.get("html", "")
                        if body or html:
                            threads.append({
                                "msg_id": "",
                                "body": body,
                                "html": html,
                                "subject": msg.get("subject", ""),
                                "email_date": msg.get("date", datetime.datetime.now().isoformat()),
                            })
                return threads
        except Exception as exc:
            log.warning("OAuth email fetch failed for LinkedIn alerts — falling back to IMAP: %s", exc)

    for attempt in range(1, _IMAP_MAX_RETRIES + 1):
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
                try:
                    imap.login(_addr, _pwd)
                except imaplib.IMAP4.error as e:
                    raise SystemExit(
                        f"\n❌  Gmail login failed: {e}\n\n"
                        f"   GMAIL_ADDRESS:      {_addr or '(not set)'}\n"
                        f"   GMAIL_APP_PASSWORD: {'(set)' if _pwd else '(not set)'}\n\n"
                        f"   Make sure GMAIL_APP_PASSWORD is a Gmail App Password (not your account password).\n"
                        f"   Generate one at: https://myaccount.google.com/apppasswords\n"
                        f"   Requires 2-Step Verification to be enabled on your Google account.\n"
                    ) from None
                imap.select("INBOX")
                threads = _fetch_emails(imap, criteria, 100)
            break   # success — exit retry loop
        except SystemExit:
            raise   # auth errors are not retried
        except OSError as e:
            if attempt < _IMAP_MAX_RETRIES:
                log.warning(
                    "Gmail IMAP connect failed (attempt %d/%d): %s — "
                    "retrying in %ds (network may not be ready yet)…",
                    attempt, _IMAP_MAX_RETRIES, e, _IMAP_RETRY_DELAY,
                )
                time.sleep(_IMAP_RETRY_DELAY)
            else:
                raise SystemExit(
                    f"\n❌  Could not connect to Gmail IMAP after {_IMAP_MAX_RETRIES} attempts: {e}\n"
                    f"   Check your internet connection and try again.\n"
                ) from None

    log.info(f"{len(threads)} LinkedIn emails matched for --since {date_str} rerun")
    return threads
