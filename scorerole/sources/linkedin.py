import re, json, imaplib, email, datetime, logging
import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Credentials read from env — set by pipeline.py at startup
import os
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "chenlomis@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

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

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _extract_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="ignore")
        # Fallback to HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    return BeautifulSoup(
                        payload.decode("utf-8", errors="ignore"), "html.parser"
                    ).get_text(separator="\n")
    payload = msg.get_payload(decode=True)
    return payload.decode("utf-8", errors="ignore") if payload else ""


def extract_jobs(body: str) -> list[dict]:
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
        # Last 3 layout lines (bottom-up) = location, company, title
        location, company, title = (
            before_lines[-1],
            before_lines[-2],
            before_lines[-3],
        )
        seen.add(job_id)
        jobs.append({
            "title":        title,
            "company":      company,
            "location":     location,
            "alumni_count": alumni_count,
            "job_id":       job_id,
            "url":          f"https://www.linkedin.com/jobs/view/{job_id}/",
        })
    return jobs


def _fetch_one_jd(job: dict) -> str:
    try:
        r = httpx.get(job["url"], headers=_BROWSER_HEADERS, timeout=12, follow_redirects=True)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        # LinkedIn embeds structured job data in JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if data.get("@type") == "JobPosting":
                    raw_desc = data.get("description", "")
                    return BeautifulSoup(raw_desc, "html.parser").get_text("\n")[:3000].strip()
            except (json.JSONDecodeError, AttributeError):
                continue
        # Fallback: common LinkedIn description containers
        for cls_pat in [r"description__text", r"job-details__main-content", r"show-more-less-html"]:
            el = soup.find(class_=re.compile(cls_pat, re.I))
            if el:
                return el.get_text("\n", strip=True)[:3000]
    except Exception as e:
        log.warning(f"JD fetch failed ({job['title']} @ {job['company']}): {e}")
    return ""


def enrich_jobs(jobs: list[dict]) -> list[dict]:
    # Sequential with a small delay to avoid LinkedIn 429s
    import time as _time
    for i, job in enumerate(jobs):
        job["jd"] = _fetch_one_jd(job)
        if i < len(jobs) - 1:
            _time.sleep(0.4)
    fetched = sum(1 for j in jobs if j.get("jd"))
    log.info(f"JD fetched for {fetched}/{len(jobs)} jobs")
    return jobs


def fetch_linkedin_alerts(seen_ids: set) -> list[dict]:
    new_threads = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        _, data = imap.search(None, 'FROM "jobalerts-noreply@linkedin.com"')
        all_ids = data[0].split()
        # Check most recent 30 emails only
        recent = all_ids[-30:] if len(all_ids) > 30 else all_ids
        log.info(f"Checking {len(recent)} recent LinkedIn alert emails")
        for mid in reversed(recent):
            _, raw = imap.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(raw[0][1])
            msg_id = msg.get("Message-ID", "")
            if not msg_id or msg_id in seen_ids:
                continue
            body = _extract_text(msg)
            if body:
                new_threads.append({"msg_id": msg_id, "body": body})
    log.info(f"{len(new_threads)} new alert emails to process")
    return new_threads


def fetch_linkedin_alerts_since(since_dt: datetime.datetime) -> list[dict]:
    """Fetch all LinkedIn alert emails on or after since_dt. Read-only — ignores seen_ids."""
    new_threads = []
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        date_str = since_dt.strftime("%d-%b-%Y")
        _, data = imap.search(
            None, f'FROM "jobalerts-noreply@linkedin.com" SINCE "{date_str}"'
        )
        all_ids = data[0].split()
        # Use last 100 for wider rerun windows
        recent = all_ids[-100:] if len(all_ids) > 100 else all_ids
        log.info(f"--since rerun: checking {len(recent)} emails since {date_str}")
        for mid in reversed(recent):
            _, raw = imap.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(raw[0][1])
            msg_id = msg.get("Message-ID", "")
            if not msg_id:
                continue
            body = _extract_text(msg)
            if body:
                new_threads.append({"msg_id": msg_id, "body": body})
    log.info(f"{len(new_threads)} emails matched for rerun")
    return new_threads
