"""Non-LinkedIn email alert fetching (Wellfound, Ladders, ClinchTalent, iCIMS, generic/LLM).

Each configured sender is queried via IMAP. Emails are parsed by a
format-specific parser to extract job title, location, and URL, then
each job's JD is fetched before being returned to the pipeline.

Known formats use dedicated regex/HTML parsers (fast, free).
Unknown senders fall back to LLM extraction (adaptive, no code change needed).

Storage: ~/.job_pipeline/email_sources.yaml  (chmod 600, user-managed)
"""
from __future__ import annotations

import datetime
import email as _email_lib
import imaplib
import logging
import re
import time
from pathlib import Path
from urllib.parse import unquote

import requests
import yaml
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

EMAIL_SOURCES_PATH = Path.home() / ".job_pipeline" / "email_sources.yaml"

_FETCH_DELAY_S = 1.0
_JD_TIMEOUT_S  = 10
_HEADERS       = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
_LOCATION_RE   = re.compile(r'[A-Z][a-z]+(?: [A-Z][a-z]+)?, [A-Z][a-z]+')


# ── Config helpers ────────────────────────────────────────────────────────────

def load_email_sources() -> list[dict]:
    """Return user-configured email sources from ~/.job_pipeline/email_sources.yaml."""
    if not EMAIL_SOURCES_PATH.exists():
        return []
    try:
        data = yaml.safe_load(EMAIL_SOURCES_PATH.read_text()) or {}
        return data.get("email_sources", [])
    except Exception as e:
        log.warning("Failed to load email_sources.yaml: %s", e)
        return []


def save_email_sources(sources: list[dict]) -> None:
    EMAIL_SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
    EMAIL_SOURCES_PATH.write_text(
        yaml.dump({"email_sources": sources}, default_flow_style=False, allow_unicode=True)
    )
    EMAIL_SOURCES_PATH.chmod(0o600)


def detect_format(sender: str) -> str:
    """Infer parser format from sender address.

    Known senders use dedicated parsers (fast, no LLM cost).
    Unknown senders return 'llm' — parsed by LLM extraction at runtime.
    """
    s = sender.lower()
    if "wellfound" in s or "angellist" in s:
        return "wellfound"
    if "theladders" in s or "ladders.com" in s:
        return "ladders"
    if "clinchtalent" in s:
        return "clinchtalent"
    if "icims" in s:
        return "icims"
    return "llm"


def format_label(fmt: str) -> str:
    return {
        "wellfound":    "Wellfound",
        "ladders":      "Ladders",
        "clinchtalent": "ClinchTalent",
        "icims":        "iCIMS",
        "llm":          "Auto (LLM)",
        "generic":      "Generic",
    }.get(fmt, fmt)


# ── Parsers ───────────────────────────────────────────────────────────────────

def _synthetic_jd(job: dict) -> str:
    """Build a jd string from email-level fields when a real JD fetch isn't available."""
    parts = [job.get("title", ""), job.get("company", "")]
    if job.get("location"):
        parts.append(job["location"])
    if job.get("salary"):
        parts.append(job["salary"])
    if job.get("tags"):
        parts.append(" | ".join(job["tags"]))
    return "\n".join(p for p in parts if p)


def _parse_wellfound(body_html: str, company: str) -> list[dict]:
    """Parse Wellfound job alert HTML (sender: team@hi.wellfound.com).

    Email contains structured job cards with title, company, salary, location,
    work model, experience, and employment type. Company name comes from each
    card, not the configured source (which is just 'Wellfound').

    Wellfound tracking links (links.wellfound.com) redirect to the homepage,
    not individual job pages — so we build a synthetic JD from card fields
    and skip URL-based JD enrichment.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()

    # Each job card: bold title followed by company/meta text in same container
    for bold in soup.find_all(["b", "strong"]):
        title = bold.get_text(strip=True)
        if not title or len(title) < 6 or len(title) > 120:
            continue
        # Heuristic: job titles contain role keywords
        if not re.search(r'\b(manager|engineer|director|analyst|designer|scientist|'
                         r'lead|head|vp|principal|staff|senior|product|data|software)\b',
                         title, re.IGNORECASE):
            continue

        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        # Pull context from the parent container
        container = bold.find_parent(["td", "div", "p", "table"])
        ctx = container.get_text(" ", strip=True) if container else ""

        # Company: italicised text immediately after the bold title
        card_company = ""
        nxt = bold.find_next_sibling()
        if nxt and nxt.name in ("i", "em"):
            raw = nxt.get_text(strip=True)
            card_company = raw.split("/")[0].strip()
        if not card_company:
            # fallback: first token before "/" in surrounding text
            m = re.search(r'\b([A-Z][A-Za-z0-9& ]+?)\s*/', ctx)
            if m:
                card_company = m.group(1).strip()

        # Salary: $NNN–NNNk pattern
        salary = ""
        m = re.search(r'\$[\d,]+\s*[–\-]\s*\$?[\d,]+k?', ctx, re.IGNORECASE)
        if m:
            salary = m.group(0).strip()

        # Location: look for Remote / city patterns
        location = ""
        loc_m = re.search(
            r'(Remote[^|$\n]*|[A-Z][a-z]+(?: [A-Z][a-z]+)?,\s*[A-Z]{2})',
            ctx,
        )
        if loc_m:
            location = loc_m.group(0).strip()

        # Tags: B2B, B2C, Public Stage, Scale Stage, etc.
        tags = re.findall(r'\b(B2B|B2C|B2B2C|Public Stage|Scale Stage|Series [A-E]|'
                          r'Actively Hiring|Top Investors|Full-time|Part-time)\b', ctx)

        job: dict = {
            "title":    title,
            "company":  card_company or company,
            "location": location,
            "salary":   salary,
            "tags":     tags,
            "url":      "",   # Wellfound tracking URLs don't resolve to job pages
        }
        jobs.append(job)

    return jobs


def _parse_ladders(body_text: str, body_html: str, company: str) -> list[dict]:
    """Parse Ladders job alert emails (sender: jobs@my.theladders.com).

    Plain-text format: "Title / Location / $NNNk - $NNNk*  Remote?"
    HTML contains hyperlinked titles — company name is in anchor text context.
    Ladders tracking URLs resolve to real job pages but return Cloudflare 403
    to plain requests; JD fetch is best-effort and falls back to synthetic jd.
    """
    jobs: list[dict] = []
    seen: set[str] = set()

    # Try HTML first — gives us the actual job URLs from anchor hrefs
    url_map: dict[str, str] = {}  # title_lower → url
    if body_html:
        soup = BeautifulSoup(body_html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "theladders.com" not in href and "job" not in href.lower():
                continue
            t = a.get_text(strip=True)
            if t and len(t) > 6:
                url_map[t.lower()] = href

    # Parse plain-text rows: "Title / Location  /  $NNN - $NNN*  Remote?"
    row_re = re.compile(
        r'^(?P<title>.+?)\s*/\s*(?P<location>[^/]+?)\s*/\s*(?P<salary>\$[\d,]+\s*[-–]\s*\$?[\d,k*]+.*?)$',
        re.MULTILINE,
    )

    for m in row_re.finditer(body_text):
        title    = m.group("title").strip()
        location = m.group("location").strip()
        salary   = m.group("salary").strip()

        if not title or len(title) < 6 or len(title) > 140:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)

        url = url_map.get(key, "")

        jobs.append({
            "title":    title,
            "company":  "",   # Ladders doesn't include company in plain text
            "location": location,
            "salary":   salary,
            "tags":     [],
            "url":      url,
        })

    return jobs


def _parse_with_llm(body_html: str, body_text: str, company: str) -> list[dict]:
    """LLM-based extraction for unknown/unrecognised email formats.

    Used as the adaptive fallback when no dedicated parser exists for a sender.
    Uses the cheapest available model — extraction is a simple structured task.
    """
    import json, os
    content = body_text.strip() or BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True)
    content = content[:6000]  # keep token cost low

    prompt = (
        "Extract all job listings from this email alert. "
        "Return a JSON array where each element has these keys: "
        "title (string), company (string, empty if unknown), "
        "location (string, empty if unknown), salary (string, empty if unknown), url (string, empty if unknown). "
        "Only include real job listings — skip navigation, footers, and boilerplate. "
        "Return only the JSON array, no other text.\n\n"
        f"EMAIL:\n{content}"
    )

    try:
        provider = os.getenv("METIS_LLM_PROVIDER", "anthropic").lower()
        if provider == "openai":
            import openai
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
            resp = client.chat.completions.create(
                model=os.getenv("PRESCREEN_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2000,
            )
            raw = resp.choices[0].message.content or ""
        else:
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
            resp = client.messages.create(
                model=os.getenv("PRESCREEN_MODEL", "claude-haiku-4-5-20251001"),
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text

        # Strip markdown code fences if present
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
        items = json.loads(raw)
        if not isinstance(items, list):
            return []
        return [
            {
                "title":    str(it.get("title", "")).strip(),
                "company":  str(it.get("company", "") or company).strip(),
                "location": str(it.get("location", "")).strip(),
                "salary":   str(it.get("salary", "")).strip(),
                "tags":     [],
                "url":      str(it.get("url", "")).strip(),
            }
            for it in items
            if it.get("title")
        ]
    except Exception as e:
        log.warning("LLM email extraction failed: %s", e)
        return []


_CLINCHTALENT_NOISE = re.compile(
    r'^(?:Hi |Here are|Face,|Â|©|\d{4} |If you wish|Twitter|Instagram|Facebook|LinkedIn|YouTube)',
    re.IGNORECASE,
)


def _clean_clinchtalent_lines(block: str, company: str) -> list[str]:
    """Strip image alt-text, boilerplate, and footer lines from a text block."""
    prefix = company.lower().split()[0]
    lines = []
    for ln in block.splitlines():
        ln = ln.strip()
        if not ln or _CLINCHTALENT_NOISE.match(ln):
            continue
        if ln.lower().startswith(prefix + " is"):
            break  # company description — everything after is noise
        lines.append(ln)
    return lines


def _parse_clinchtalent(body_text: str, company: str) -> list[dict]:
    """Parse ClinchTalent job alert plain text (e.g. Waymo).

    Structure: [title lines] [location lines] [company blurb] Read More » [tracking url]
    Real job URL is URL-encoded as a `url=` param in the ClinchTalent tracking link.
    """
    jobs: list[dict] = []
    seen_urls: set[str] = set()

    tracking_re  = re.compile(r'https://api\.clinchtalent\.com/[^\s)]+[?&]url=(https[^\s)]+)')
    url_matches  = list(tracking_re.finditer(body_text))
    rm_positions = [m.start() for m in re.finditer(r'Read More', body_text)]

    for i, rm_pos in enumerate(rm_positions):
        # Find the real job URL for this "Read More" block
        real_url = None
        for um in url_matches:
            if um.start() > rm_pos:
                decoded = unquote(um.group(1)).rstrip(")").strip()
                if "/jobs/" in decoded:
                    key = decoded.split("?")[0]
                    if key not in seen_urls:
                        seen_urls.add(key)
                        real_url = decoded
                break
        if not real_url:
            continue

        # Extract the text block immediately before this "Read More"
        prev_end = rm_positions[i - 1] + len("Read More") if i > 0 else 0
        block    = body_text[prev_end:rm_pos]
        lines    = _clean_clinchtalent_lines(block, company)
        if not lines:
            continue

        # Separate title (no location pattern) from location (City, State)
        title_parts, loc_parts = [], []
        for ln in lines:
            if _LOCATION_RE.search(ln) or re.search(r'\bUnited States\b', ln):
                loc_parts.append(ln)
            elif not loc_parts:
                title_parts.append(ln)

        title    = " ".join(title_parts).strip()
        location = loc_parts[0] if loc_parts else ""
        if title:
            jobs.append({"title": title, "company": company, "location": location, "url": real_url})

    return jobs


def _parse_icims(body_html: str, company: str) -> list[dict]:
    """Parse iCIMS job alert HTML (e.g. GitHub).

    Direct job URLs appear as <a href="https://[domain]/jobs/NNNN">Title</a>.
    """
    soup = BeautifulSoup(body_html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()
    job_url_re = re.compile(r'https://[^/]+/jobs/(\d+)')

    for a in soup.find_all("a", href=True):
        m = job_url_re.match(a["href"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title = a.get_text(strip=True)
        if not title or len(title) < 4:
            continue

        parent = a.find_parent(["td", "tr", "div", "p", "table"])
        location = ""
        if parent:
            candidate = parent.get_text(" ", strip=True).replace(title, "").strip("·- \xa0").strip()
            if candidate and len(candidate) < 80:
                location = candidate

        # Reconstruct clean URL from job ID using the original domain
        domain_m = re.match(r'(https://[^/]+)', a["href"])
        base = domain_m.group(1) if domain_m else ""
        jobs.append({"title": title, "company": company, "location": location,
                     "url": f"{base}/jobs/{m.group(1)}"})

    return jobs


def _parse_generic(body_html: str, company: str) -> list[dict]:
    """Best-effort parser: scrapes any job-like <a> links from unknown formats."""
    soup = BeautifulSoup(body_html, "html.parser")
    jobs: list[dict] = []
    seen: set[str] = set()
    job_re = re.compile(r'/jobs?/|/careers?/|/openings?/|/positions?/', re.IGNORECASE)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not job_re.search(href) or href in seen:
            continue
        seen.add(href)
        title = a.get_text(strip=True)
        if not title or len(title) < 6 or len(title) > 120:
            continue
        jobs.append({"title": title, "company": company, "location": "", "url": href})

    return jobs


# ── JD fetching ───────────────────────────────────────────────────────────────

def _fetch_jd(url: str) -> str:
    try:
        r = requests.get(url, headers=_HEADERS, timeout=_JD_TIMEOUT_S, allow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        for el in soup(["script", "style", "nav", "header", "footer"]):
            el.decompose()
        return soup.get_text(" ", strip=True)[:8000]
    except Exception as e:
        log.warning("JD fetch failed for %s: %s", url, e)
        return ""


# ── IMAP helpers ──────────────────────────────────────────────────────────────

def _fetch_emails_from(imap, sender: str, since_dt: datetime.datetime) -> list[dict]:
    date_str = since_dt.strftime("%d-%b-%Y")
    _, data = imap.search(None, f'FROM "{sender}" SINCE "{date_str}"')
    results = []
    for mid in data[0].split():
        try:
            _, raw = imap.fetch(mid, "(RFC822)")
            msg = _email_lib.message_from_bytes(raw[0][1])
            body_text = body_html = ""
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/plain":
                    body_text = part.get_payload(decode=True).decode("utf-8", errors="replace")
                elif ct == "text/html":
                    body_html = part.get_payload(decode=True).decode("utf-8", errors="replace")
            results.append({"text": body_text, "html": body_html})
        except Exception as e:
            log.warning("Failed to read email %s from %s: %s", mid, sender, e)
    return results


# ── Main entry point ──────────────────────────────────────────────────────────

def fetch_email_alerts(
    since_dt: datetime.datetime,
    sources: list[dict],
    *,
    gmail_address: str,
    gmail_app_password: str,
) -> list[dict]:
    """Fetch and parse job alerts from non-LinkedIn email sources.

    Returns dicts matching the pipeline job schema:
    title, company, location, job_id, url, jd, source.
    """
    if not sources:
        return []

    all_jobs: list[dict] = []

    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(gmail_address, gmail_app_password)
            imap.select("INBOX")

            for src in sources:
                company = src.get("company", "")
                sender  = src.get("sender", "")
                fmt     = src.get("format") or detect_format(sender)
                if not sender:
                    continue

                emails = _fetch_emails_from(imap, sender, since_dt)
                log.info("email-alerts: %d email(s) from %s (%s)", len(emails), sender, company)

                for em in emails:
                    if fmt == "wellfound":
                        parsed = _parse_wellfound(em["html"], company)
                    elif fmt == "ladders":
                        parsed = _parse_ladders(em["text"], em["html"], company)
                    elif fmt == "clinchtalent":
                        parsed = _parse_clinchtalent(em["text"], company)
                    elif fmt == "icims":
                        parsed = _parse_icims(em["html"], company)
                    elif fmt == "llm":
                        parsed = _parse_with_llm(em["html"], em["text"], company)
                    else:
                        parsed = _parse_generic(em["html"] or em["text"], company)

                    for job in parsed:
                        # Best-effort JD fetch; fall back to synthetic jd from email fields
                        jd = ""
                        if job.get("url"):
                            jd = _fetch_jd(job["url"])
                            time.sleep(_FETCH_DELAY_S)
                        if not jd:
                            jd = _synthetic_jd(job)

                        all_jobs.append({
                            "title":    job["title"],
                            "company":  job.get("company") or company,
                            "location": job.get("location", ""),
                            "job_id":   f"{(job.get('company') or company).lower()}:{job['title'].lower()}",
                            "url":      job.get("url", ""),
                            "jd":       jd,
                            "source":   "email_alert",
                        })

    except imaplib.IMAP4.error as e:
        log.error("IMAP error fetching email alerts: %s", e)

    return all_jobs
