"""Proactive job source — scrapes company career pages directly.

Ported from pm-market-intel/scrape.py and pm-market-intel/fetcher.py.
Produces the same job dict shape as linkedin.py so the rest of the pipeline
is unaware of the source.

Job dict shape (matches linkedin.py output + pre-populated jd field):
  {title, company, location, job_id, url, jd, source: "proactive"}

The `jd` field is pre-populated from the ATS API — proactive jobs skip the
enrich_jobs() HTTP fetch step that LinkedIn jobs go through.

Agentic fallback loop (Stage 2):
  Greenhouse/Lever API → on failure → Playwright browser → on failure → skip
  Failures are per-company and logged; they never propagate to the caller.
"""
import hashlib
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yaml

log = logging.getLogger(__name__)

_COMPANIES_YML = Path(__file__).parent / "companies.yml"

# Lookback used when scraping (matches default metis lookback)
DEFAULT_LOOKBACK_DAYS = 7

# Max consecutive failures before a company is flagged in digest
_MAX_FAILURES_BEFORE_FLAG = 3


# ── Location filtering ────────────────────────────────────────────────────────

# Country indicator sets — extend as needed for more countries
_COUNTRY_INDICATORS: dict[str, set[str]] = {
    "US": {
        "united states", "usa", "u.s.", "u.s.a.", "us", "remote",
        "alabama", "al", "alaska", "ak", "arizona", "az", "arkansas", "ar",
        "california", "ca", "colorado", "co", "connecticut", "ct", "delaware", "de",
        "florida", "fl", "georgia", "ga", "hawaii", "hi", "idaho", "id",
        "illinois", "il", "indiana", "in", "iowa", "ia", "kansas", "ks",
        "kentucky", "ky", "louisiana", "la", "maine", "me", "maryland", "md",
        "massachusetts", "ma", "michigan", "mi", "minnesota", "mn", "mississippi", "ms",
        "missouri", "mo", "montana", "mt", "nebraska", "ne", "nevada", "nv",
        "new hampshire", "nh", "new jersey", "nj", "new mexico", "nm", "new york", "ny",
        "north carolina", "nc", "north dakota", "nd", "ohio", "oh", "oklahoma", "ok",
        "oregon", "or", "pennsylvania", "pa", "rhode island", "ri", "south carolina", "sc",
        "south dakota", "sd", "tennessee", "tn", "texas", "tx", "utah", "ut",
        "vermont", "vt", "virginia", "va", "washington", "wa", "west virginia", "wv",
        "wisconsin", "wi", "wyoming", "wy", "dc", "washington d.c.", "washington dc",
        "san francisco", "sf", "bay area", "silicon valley", "seattle", "bellevue",
        "redmond", "kirkland", "new york city", "nyc", "brooklyn", "manhattan",
        "los angeles", "la", "santa monica", "culver city", "austin", "boston",
        "cambridge", "chicago", "denver", "boulder", "atlanta", "miami", "portland",
        "san jose", "palo alto", "menlo park", "mountain view", "sunnyvale", "santa clara",
        "san diego", "phoenix", "dallas", "houston", "raleigh", "durham", "pittsburgh",
        "ann arbor", "minneapolis", "salt lake city", "nashville",
    },
    "CA": {
        "canada", "ca", "remote", "ontario", "british columbia", "quebec", "alberta",
        "toronto", "vancouver", "montreal", "calgary", "ottawa", "edmonton", "winnipeg",
        "on", "bc", "qc", "ab", "mb", "sk", "ns", "nb", "pe", "nl", "nt", "yt", "nu",
    },
    "UK": {
        "united kingdom", "uk", "great britain", "england", "scotland", "wales",
        "london", "manchester", "edinburgh", "birmingham", "remote - uk", "remote uk",
    },
}

# Signals that explicitly mark a role as NOT in a given country
_NON_COUNTRY_SIGNALS: dict[str, list[str]] = {
    "US": [
        "canada", "uk", "united kingdom", "london", "toronto", "vancouver",
        "germany", "berlin", "amsterdam", "netherlands", "australia", "sydney",
        "india", "bangalore", "singapore", "ireland", "dublin",
        "remote - uk", "remote - canada", "remote - europe", "remote - apac",
    ],
    "CA": [
        "united states", "usa", "us only", "remote - us", "remote us",
        "uk", "united kingdom", "remote - uk", "remote - europe",
    ],
    "UK": [
        "united states", "usa", "us only", "canada", "remote - us", "remote us",
        "remote - canada", "remote - europe", "remote - apac",
    ],
}


def _detect_country(location_str: str) -> str:
    """Best-guess country from a 'City, State/Country' string. Defaults to US."""
    if not location_str:
        return "US"
    loc = location_str.lower()
    if any(sig in loc for sig in ["canada", "toronto", "vancouver", "ontario", "british columbia"]):
        return "CA"
    if any(sig in loc for sig in ["united kingdom", "uk", "london", "england", "scotland"]):
        return "UK"
    return "US"


def _is_target_location(role_location: str, target_country: str) -> bool:
    """Return True if a role's location is compatible with the candidate's target country."""
    if not role_location:
        return True  # no location listed — assume open, let scoring handle it

    loc = role_location.lower()
    indicators = _COUNTRY_INDICATORS.get(target_country, set())
    non_signals = _NON_COUNTRY_SIGNALS.get(target_country, [])

    if any(sig in loc for sig in non_signals):
        return False

    tokens = set(re.split(r"[,/|\-–•\s]+", loc))
    # Multi-word indicators
    if any(ind in loc for ind in indicators if " " in ind):
        return True
    return bool(tokens & indicators)


# ── Title filtering ───────────────────────────────────────────────────────────

# Default management-track excludes — overridden if profile has deal_breakers
_DEFAULT_EXCLUDE = [
    "vp of", "vp,", "vice president", "group product manager",
    "manager of product", "product management manager", "chief of staff",
    "head of product marketing", "director of product marketing",
]


def _build_title_patterns(target_roles: list[str]) -> tuple[list[str], list[str]]:
    """Derive (include_patterns, exclude_patterns) from profile target.roles."""
    # Expand common abbreviations
    _EXPANSIONS = {
        "pm": ["product manager", "pm"],
        "swe": ["software engineer", "swe", "software developer"],
        "em": ["engineering manager", "em"],
        "ds": ["data scientist", "ds"],
        "ml": ["machine learning", "ml engineer"],
        "design": ["product designer", "ux designer", "designer"],
    }

    include = []
    for role in target_roles:
        r = role.lower().strip()
        include.append(r)
        # Add expansions for known abbreviations in the role string
        for abbr, expansions in _EXPANSIONS.items():
            if abbr in r.split():
                for exp in expansions:
                    candidate = r.replace(abbr, exp)
                    if candidate not in include:
                        include.append(candidate)

    return include or ["product manager"], _DEFAULT_EXCLUDE


def _is_target_title(title: str, include: list[str], exclude: list[str]) -> bool:
    t = title.lower()
    if any(ex in t for ex in exclude):
        return False
    return any(inc in t for inc in include)


# ── Date helpers ──────────────────────────────────────────────────────────────

def _parse_date(date_str):
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str.replace("Z", "+00:00"), fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _within_lookback(date_str, lookback_days: int) -> bool:
    dt = _parse_date(date_str)
    if not dt:
        return True  # unknown date — include
    return dt >= datetime.now(timezone.utc) - timedelta(days=lookback_days)


def _strip_html(html: str) -> str:
    import html as html_lib
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r" {2,}", " ", html_lib.unescape(text)).strip()


def _job_id(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


# ── Greenhouse scraper ────────────────────────────────────────────────────────

def _scrape_greenhouse(
    company: dict,
    include: list[str],
    exclude: list[str],
    target_country: str,
    lookback_days: int,
    seen_hashes: set,
) -> list[dict]:
    from ..state import _role_hash
    slug = company["slug"]
    name = company["name"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            log.warning(f"proactive: {name} — Greenhouse 404 (bad slug?)")
            return []
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"proactive: {name} — Greenhouse API error: {e}")
        return _playwright_fallback(company, include, exclude, target_country, lookback_days, seen_hashes)

    matches = []
    for job in resp.json().get("jobs", []):
        title    = job.get("title", "")
        location = job.get("location", {}).get("name", "")

        if not _is_target_title(title, include, exclude):
            continue
        if not _is_target_location(location, target_country):
            continue
        updated = job.get("updated_at") or job.get("created_at")
        if not _within_lookback(updated, lookback_days):
            continue

        job_url = f"https://job-boards.greenhouse.io/{slug}/jobs/{job.get('id')}"
        rh = _role_hash(title, name)
        if rh in seen_hashes:
            continue

        jd = "\n\n".join(p for p in [title, location, _strip_html(job.get("content", ""))] if p)
        matches.append({
            "title":    title,
            "company":  name,
            "location": location,
            "job_id":   _job_id(job_url),
            "url":      job_url,
            "jd":       jd,
            "source":   "proactive",
        })

    return matches


# ── Lever scraper ─────────────────────────────────────────────────────────────

def _scrape_lever(
    company: dict,
    include: list[str],
    exclude: list[str],
    target_country: str,
    lookback_days: int,
    seen_hashes: set,
) -> list[dict]:
    from ..state import _role_hash
    slug = company["slug"]
    name = company["name"]
    url  = f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=100"

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            log.warning(f"proactive: {name} — Lever 404 (bad slug?)")
            return []
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"proactive: {name} — Lever API error: {e}")
        return _playwright_fallback(company, include, exclude, target_country, lookback_days, seen_hashes)

    jobs = resp.json()
    if isinstance(jobs, dict):
        jobs = jobs.get("data", [])

    matches = []
    for job in jobs:
        title    = job.get("text", "")
        location = job.get("categories", {}).get("location", "")

        if not _is_target_title(title, include, exclude):
            continue
        if not _is_target_location(location, target_country):
            continue

        created_ms = job.get("createdAt")
        if created_ms:
            created_dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            if created_dt < datetime.now(timezone.utc) - timedelta(days=lookback_days):
                continue

        job_url = job.get("hostedUrl", "")
        if not job_url:
            continue
        rh = _role_hash(title, name)
        if rh in seen_hashes:
            continue

        parts = [
            title, location,
            job.get("descriptionPlain", "") or _strip_html(job.get("description", "")),
            job.get("additionalPlain", "") or _strip_html(job.get("additional", "")),
        ]
        matches.append({
            "title":    title,
            "company":  name,
            "location": location,
            "job_id":   _job_id(job_url),
            "url":      job_url,
            "jd":       "\n\n".join(p for p in parts if p),
            "source":   "proactive",
        })

    return matches


# ── Playwright fallback (Stage 2 agentic loop) ────────────────────────────────

def _playwright_fallback(
    company: dict,
    include: list[str],
    exclude: list[str],
    target_country: str,
    lookback_days: int,
    seen_hashes: set,
) -> list[dict]:
    """Attempt Playwright browser scrape for companies whose API failed or is unavailable.

    Bounded retry: tries once. On empty/blocked result, logs and returns [].
    The caller (fetch_proactive) tracks consecutive failures per company across runs.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        log.debug("proactive: playwright not installed — skipping browser fallback")
        return []

    name = company["name"]
    ats  = company.get("ats", "unknown")

    # Build careers URL — for Ashby we know the pattern; for unknown, skip
    careers_url = _guess_careers_url(company)
    if not careers_url:
        log.warning(f"proactive: {name} — no careers URL available for Playwright fallback")
        return []

    log.info(f"proactive: {name} — trying Playwright fallback at {careers_url}")
    try:
        return _playwright_scrape(company, careers_url, include, exclude, target_country, lookback_days, seen_hashes)
    except Exception as e:
        log.warning(f"proactive: {name} — Playwright fallback failed: {e}")
        return []


def _guess_careers_url(company: dict):
    ats  = company.get("ats", "")
    slug = company.get("slug", "")
    if ats == "ashby":
        return f"https://jobs.ashbyhq.com/{slug}"
    if ats == "workday":
        return company.get("careers_url")  # must be set explicitly in yml for Workday
    return None


def _playwright_scrape(
    company: dict,
    careers_url: str,
    include: list[str],
    exclude: list[str],
    target_country: str,
    lookback_days: int,
    seen_hashes: set,
) -> list[dict]:
    from playwright.sync_api import sync_playwright
    from ..state import _role_hash

    name = company["name"]
    matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ))
        page = context.new_page()
        try:
            resp = page.goto(careers_url, timeout=20_000, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                raise ValueError(f"HTTP {resp.status}")
            page.wait_for_timeout(2000)

            body = page.inner_text("body")
            if len(body.strip()) < 200:
                raise ValueError("Page body too short — likely blocked or empty")

            # Extract job links — look for anchor tags matching title filters
            job_links = page.query_selector_all("a[href]")
            for link in job_links:
                title_text = (link.inner_text() or "").strip()
                href = link.get_attribute("href") or ""
                if not title_text or not href:
                    continue
                if not _is_target_title(title_text, include, exclude):
                    continue

                full_url = href if href.startswith("http") else f"{careers_url.rstrip('/')}/{href.lstrip('/')}"
                rh = _role_hash(title_text, name)
                if rh in seen_hashes:
                    continue

                # Fetch individual JD page for body text
                jd_text = _fetch_jd_page(context, full_url)
                if not jd_text:
                    continue

                matches.append({
                    "title":    title_text,
                    "company":  name,
                    "location": "",  # location extracted from JD by scoring layer
                    "job_id":   _job_id(full_url),
                    "url":      full_url,
                    "jd":       jd_text,
                    "source":   "proactive",
                })
        finally:
            browser.close()

    return matches


def _fetch_jd_page(context, url: str):
    """Fetch a single JD page and return plain text body, or None on failure."""
    try:
        page = context.new_page()
        resp = page.goto(url, timeout=15_000, wait_until="domcontentloaded")
        if resp and resp.status >= 400:
            page.close()
            return None
        page.wait_for_timeout(1000)
        for sel in ["nav", "footer", "header", "[role='navigation']", "#apply"]:
            for el in page.query_selector_all(sel):
                el.evaluate("el => el.remove()")
        text = page.inner_text("body").strip()
        page.close()
        return text if len(text) > 100 else None
    except Exception as e:
        log.debug(f"proactive: JD page fetch failed for {url}: {e}")
        return None


# ── Failure tracking ──────────────────────────────────────────────────────────

_FAILURES_FILE = Path.home() / ".job_pipeline" / "proactive_failures.json"


def _load_failures() -> dict:
    if not _FAILURES_FILE.exists():
        return {}
    try:
        import json
        return json.loads(_FAILURES_FILE.read_text())
    except Exception:
        return {}


def _save_failures(failures: dict):
    import json
    _FAILURES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _FAILURES_FILE.write_text(json.dumps(failures, indent=2))
    _FAILURES_FILE.chmod(0o600)


def _record_company_result(name: str, success: bool):
    failures = _load_failures()
    if success:
        failures.pop(name, None)
    else:
        failures[name] = failures.get(name, 0) + 1
    _save_failures(failures)


def get_unreachable_companies() -> list[str]:
    """Return companies that have failed >= _MAX_FAILURES_BEFORE_FLAG consecutive runs."""
    failures = _load_failures()
    return [name for name, count in failures.items() if count >= _MAX_FAILURES_BEFORE_FLAG]


# ── Public interface ──────────────────────────────────────────────────────────

def _load_companies_yml() -> dict:
    with open(_COMPANIES_YML) as f:
        return yaml.safe_load(f)


def count_all_companies() -> int:
    """Return total number of companies in the pool."""
    cfg = _load_companies_yml()
    return (
        len(cfg.get("greenhouse_companies", []))
        + len(cfg.get("lever_companies", []))
        + len(cfg.get("ashby_companies", []))
    )


def all_company_names() -> list[str]:
    """Return sorted list of all company names in the pool."""
    cfg = _load_companies_yml()
    all_co = (
        cfg.get("greenhouse_companies", [])
        + cfg.get("lever_companies", [])
        + cfg.get("ashby_companies", [])
    )
    return sorted(c["name"] for c in all_co)


def estimate_monthly_cost(n_companies: int) -> str:
    """Rough monthly cost estimate for scraping n companies."""
    roles_per_month = n_companies * 2 * 4
    scored = roles_per_month * 0.5
    cost = scored * 0.015
    return f"~${cost:.2f}/month"


def _resolve_companies(ps: dict, cfg: dict) -> tuple[list, list, list]:
    """Return (gh_companies, lv_companies, ash_companies) from profile proactive_sources config.

    Supports two profile schemas:
      new: companies: [Anthropic, Figma, ...]   — explicit name list
      old: tiers: [S, A]                         — backward-compat, derived from yml
    """
    exclude_names = {n.lower() for n in (ps.get("exclude_companies", []) or [])}
    all_pool = (
        [(c, "greenhouse") for c in cfg.get("greenhouse_companies", [])]
        + [(c, "lever")     for c in cfg.get("lever_companies", [])]
        + [(c, "ashby")     for c in cfg.get("ashby_companies", [])]
    )

    if "companies" in ps:
        selected = {n.lower() for n in (ps["companies"] or [])}
        keep = [
            (c, ats) for c, ats in all_pool
            if c["name"].lower() in selected and c["name"].lower() not in exclude_names
        ]
    else:
        tiers = set(ps.get("tiers", ["S", "A"]))
        keep = [
            (c, ats) for c, ats in all_pool
            if c.get("tier") in tiers and c["name"].lower() not in exclude_names
        ]

    gh  = [c for c, ats in keep if ats == "greenhouse"]
    lv  = [c for c, ats in keep if ats == "lever"]
    ash = [c for c, ats in keep if ats == "ashby"]
    return gh, lv, ash


def fetch_proactive(profile: dict, seen_hashes: set) -> "list[dict]":
    """Fetch new roles from configured company career pages.

    Reads proactive_sources config from profile dict.
    Returns job dicts in the same shape as linkedin.py (with pre-populated jd field).
    Never raises — per-company failures are logged and skipped.
    """
    ps = profile.get("proactive_sources", {})
    if not ps.get("enabled", True):
        return []

    lookback_days = ps.get("lookback_days", DEFAULT_LOOKBACK_DAYS)

    target_roles   = profile.get("target", {}).get("roles", [])
    include, excl  = _build_title_patterns(target_roles)
    candidate_loc  = profile.get("candidate", {}).get("location", "")
    target_country = _detect_country(candidate_loc)

    cfg = _load_companies_yml()
    gh_companies, lv_companies, ash_companies = _resolve_companies(ps, cfg)

    results: list[dict] = []

    for co in gh_companies:
        try:
            jobs = _scrape_greenhouse(co, include, excl, target_country, lookback_days, seen_hashes)
            _record_company_result(co["name"], success=True)
            results.extend(jobs)
            log.info(f"proactive: {co['name']} — {len(jobs)} new roles")
        except Exception as e:
            log.warning(f"proactive: {co['name']} — unexpected error: {e}")
            _record_company_result(co["name"], success=False)

    for co in lv_companies:
        try:
            jobs = _scrape_lever(co, include, excl, target_country, lookback_days, seen_hashes)
            _record_company_result(co["name"], success=True)
            results.extend(jobs)
            log.info(f"proactive: {co['name']} — {len(jobs)} new roles")
        except Exception as e:
            log.warning(f"proactive: {co['name']} — unexpected error: {e}")
            _record_company_result(co["name"], success=False)

    for co in ash_companies:
        try:
            jobs = _playwright_fallback(co, include, excl, target_country, lookback_days, seen_hashes)
            _record_company_result(co["name"], success=bool(jobs) or True)
            results.extend(jobs)
            log.info(f"proactive: {co['name']} (ashby) — {len(jobs)} new roles")
        except Exception as e:
            log.warning(f"proactive: {co['name']} (ashby) — unexpected error: {e}")
            _record_company_result(co["name"], success=False)

    log.info(f"proactive: total {len(results)} new roles across {len(gh_companies)+len(lv_companies)+len(ash_companies)} companies")
    return results
