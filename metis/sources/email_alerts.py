"""Non-LinkedIn email alert fetching (ClinchTalent, iCIMS, generic).

Each configured sender is queried via IMAP. Emails are parsed by a
format-specific parser to extract job title, location, and URL, then
each job's JD is fetched before being returned to the pipeline.

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
    """Infer parser format from sender address."""
    s = sender.lower()
    if "clinchtalent" in s:
        return "clinchtalent"
    if "icims" in s:
        return "icims"
    return "generic"


def format_label(fmt: str) -> str:
    return {"clinchtalent": "ClinchTalent", "icims": "iCIMS", "generic": "Generic"}.get(fmt, fmt)


# ── Parsers ───────────────────────────────────────────────────────────────────

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
                    if fmt == "clinchtalent":
                        parsed = _parse_clinchtalent(em["text"], company)
                    elif fmt == "icims":
                        parsed = _parse_icims(em["html"], company)
                    else:
                        parsed = _parse_generic(em["html"] or em["text"], company)

                    for job in parsed:
                        jd = _fetch_jd(job["url"])
                        time.sleep(_FETCH_DELAY_S)
                        all_jobs.append({
                            "title":    job["title"],
                            "company":  job["company"],
                            "location": job["location"],
                            "job_id":   f"{company.lower()}:{job['title'].lower()}",
                            "url":      job["url"],
                            "jd":       jd,
                            "source":   "email_alert",
                        })

    except imaplib.IMAP4.error as e:
        log.error("IMAP error fetching email alerts: %s", e)

    return all_jobs
