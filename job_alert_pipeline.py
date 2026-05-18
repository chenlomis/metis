#!/usr/bin/env python3
"""
job_alert_pipeline.py — Lomis Job Alert Pipeline

Four stages:
  1. Ingest  — IMAP reads new LinkedIn job alert emails (Gmail App Password)
  2. Enrich  — HTTP fetches LinkedIn job pages in parallel (no API cost)
  3. Score   — Single batched Claude call with cached system prompt
  4. Deliver — HTML digest via SMTP

~$0.06–0.08/run vs ~$0.40/run for the per-job web_search approach.

Setup:
  pip install -r requirements.txt

  .env:
    ANTHROPIC_API_KEY=sk-ant-...
    GMAIL_ADDRESS=chenlomis@gmail.com
    GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx   # myaccount.google.com/apppasswords
    RECIPIENT_EMAIL=chenlomis@gmail.com

  Schedule (twice daily):
    bash setup_cron.sh
"""

import os, re, json, imaplib, smtplib, email, datetime, logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv(override=True)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "chenlomis@gmail.com")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", "chenlomis@gmail.com")
MODEL              = "claude-sonnet-4-6"
MAX_JOBS_PER_RUN   = int(os.getenv("MAX_JOBS_PER_RUN", "20"))  # cap before JD fetch

DATA_DIR  = Path.home() / ".job_pipeline"
LOG_DIR   = DATA_DIR / "logs"
SEEN_FILE = DATA_DIR / "seen_ids.json"

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / f"{datetime.date.today()}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lomis profile — used as cached system prompt
# ---------------------------------------------------------------------------
_LOMIS_PROFILE = """
CANDIDATE: Lomis Chen — Senior PM actively searching (as of May 2026)
TARGET LEVEL: Staff PM / Principal PM / Lead PM (~10 years experience)
LOCATION: Redmond WA; SF Bay Area also doable; remote-friendly preferred

SCORE CALIBRATION:
For Staff/Principal PM roles, 75-82% IS a strong match — these are senior roles
with genuinely high bars. Do not inflate. 75%+ = apply. 87%+ is exceptional and rare.

TITLE-LEVEL RULE (apply before scoring):
If the role title does NOT contain Staff / Lead / Principal / Director / Head / VP / GM,
deduct 10 points from the raw score. A "Senior PM" title at any company is a lateral or
downward move from the target level. Account for that deduction explicitly.

CORE STRENGTHS:
- Full-stack AI product ownership: both ML architecture AND UX (rare for PM)
- Production-grade model evaluation: precision/recall, 2,000+ custom models
- 0-1 builder: Custom Extractions (DocuSign), Suggested Fields, FieldsExplorer, Azure CLI
- AI-assisted product work: Suggested Fields, AZ Next, autoplacing — counts as AI even if
  not cutting-edge research; "claudifying" is less differentiating now but still a real fit
- Enterprise SaaS: fine to work with; monetization through enterprise deals
- Agentic AI, human-in-the-loop design, trust in AI outputs
- Developer platform: Azure CLI, external APIs, PyCon China, SDK/API/CLI, OSS-adjacent
- Data analytics, search, recsys, personalization — strong ML-driven UX/ranking background
- Healthcare AI academic grounding (Deep Learning for Healthcare, UIUC)
- Microsoft cultural fluency (4+ years PM2), cross-geo: US, UK, IST
- A/B experimentation, beta program architecture, influence without authority
- ECE background (BS) + Apple hardware/software codesign (iPhone 7 RF) — hardware-adjacent
  roles are not out of reach; pure hardware without PM/software angle is a stretch

AI/ML SCOPE — preference, not requirement:
  AI/ML is broad. Fits include: frontier research labs (OpenAI, DeepMind, xAI, Mistral),
  data/analytics platforms, search, agentic workflows, orchestration, dev tooling/platform.
  Does NOT have to be AI-first if the role has strong technical depth and 0-1 scope.

EXPERIENCE:
- DocuSign (Jan 2022 – Mar 2026): Senior PM, Navigator AI Platform
- Microsoft (Sept 2017 – Dec 2021): PM2 (later rebranded from Program Manager), Azure CLI + Azure ML
- Apple Intern (Jan–May 2016): RF Design Engineering, iPhone 7 WiFi (hardware/software codesign)
- HydroOne Intern (Sept 2014–May 2015): Telecom Engineering, 300+ station designs
- GE Hitachi Junior PM (May 2013–Jan 2014): QC procedures, ~$1M revenue impact

GREEN FLAGS: lean team, genuine 0-1, AI/ML or data/search/agentic/dev-tooling, technical
             depth, mission-driven, strategic ownership, async/remote, SF Bay Area also OK,
             frontier research labs, SaaS with real product depth

YELLOW FLAGS: large org matrix, GTM-heavy, heavy evangelism, on-site requirements,
              hardware-adjacent without software/PM angle, pure AI-assisted SaaS with no
              deeper ML or platform scope, regulatory/compliance/privacy focus

RED FLAGS: pure execution/order-taking, cybersecurity/IT systems (no product angle),
           KTLO/maintenance, loudest-voice culture, bureaucracy, TPM roles,
           T&S with no AI or product angle

GAPS (not red flags — evaluate on merit, not auto-skip):
  fintech infra, mortgage servicing, HPC infra, pure growth PM, DevRel primary
"""

SCORE_SYSTEM = f"""{_LOMIS_PROFILE}

You are a job fit evaluator for Lomis Chen.

Given a batch of job listings, return a JSON array — one object per job, same order as input:
[
  {{
    "score": <integer 0-100>,
    "verdict": "apply" | "consider" | "skipped",
    "leveragePoints": ["<short phrase ≤5 words>", ...],
    "frictionPoints": ["<short phrase ≤5 words>", ...],
    "tags": [
      {{"text": "<≤5 words>", "sentiment": "green" | "amber" | "red" | "orange"}}
    ]
  }},
  ...
]

leveragePoints: 1-2 match explanations. Each must name what the JD specifically requires AND
  what in the candidate background satisfies it. Format: "<JD need> → <candidate background>",
  10 words max. Be concrete — reference actual JD language and actual resume items.
  BAD: "agentic AI fit"           GOOD: "JD needs agentic workflow design → DocuSign Navigator agentic flows"
  BAD: "healthcare AI grounding"  GOOD: "JD wants ML depth → UIUC Deep Learning for Healthcare + model eval"
  BAD: "0-1 PRD authoring"        GOOD: "JD requires 0-1 build → DocuSign Custom Extractions from zero"

frictionPoints: 1 honest concern in same format. Use [] if no real friction.
  NEVER write placeholder text ("none", "n/a", "none material", "no concerns", etc.).
  BAD: "none material"             GOOD: []
  BAD: "no significant gaps"       GOOD: "JD expects crawl/index infra exp → no direct background"

tags: Up to 4 highlight tags. "green" = clear JD↔background match, "amber" = caution,
      "red" = real blocker, "orange" = domain gap. ≤5 words each.

Thresholds (calibrated for Staff/Principal PM, after title-level deduction):
  score >= 75  →  apply
  score 60-74  →  consider
  score < 60   →  skipped

Be honest. 75% IS strong at this level. Do not inflate.
Return ONLY valid JSON array — no markdown fences, no preamble."""


# ---------------------------------------------------------------------------
# Stage 1: Ingest — Gmail via IMAP + App Password
# ---------------------------------------------------------------------------

def load_seen_ids() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen_ids(ids: set):
    SEEN_FILE.write_text(json.dumps(list(ids)))


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


# ---------------------------------------------------------------------------
# Stage 2: Enrich — parallel HTTP fetch of LinkedIn job pages (free)
# ---------------------------------------------------------------------------

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


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


# ---------------------------------------------------------------------------
# Stage 3: Score — single batched Claude call, system prompt cached
# ---------------------------------------------------------------------------

def score_jobs_batch(client: anthropic.Anthropic, jobs: list[dict]) -> list[dict]:
    job_blocks = "\n\n---\n\n".join(
        "JOB {n}: {title} at {company} ({location})\n{jd_line}".format(
            n=i + 1,
            title=j["title"],
            company=j["company"],
            location=j["location"],
            jd_line=(
                "JD:\n" + j["jd"][:1500]
                if j.get("jd")
                else "(No JD retrieved — score from title/company only)"
            ),
        )
        for i, j in enumerate(jobs)
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SCORE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{
            "role": "user",
            "content": (
                f"Score all {len(jobs)} jobs for Lomis. "
                f"Return a JSON array of exactly {len(jobs)} objects.\n\n"
                f"{job_blocks}"
            ),
        }],
    )

    usage = response.usage
    log.info(
        f"Scoring — input: {usage.input_tokens} tokens "
        f"(cache_write: {getattr(usage, 'cache_creation_input_tokens', 0)}, "
        f"cache_read: {getattr(usage, 'cache_read_input_tokens', 0)}), "
        f"output: {usage.output_tokens} tokens"
    )

    raw = re.sub(
        r"^```(?:json)?\s*|\s*```$", "", response.content[0].text.strip(), flags=re.MULTILINE
    ).strip()
    try:
        evals = json.loads(raw)
        if isinstance(evals, dict):
            evals = [evals]
    except json.JSONDecodeError:
        log.warning("Batch score JSON parse failed — all jobs marked skip")
        evals = []

    for i, job in enumerate(jobs):
        job["eval"] = evals[i] if i < len(evals) else {
            "score": 0, "verdict": "skipped", "leveragePoints": [], "frictionPoints": ["Scoring parse error"], "tags": []
        }
    return jobs


# ---------------------------------------------------------------------------
# Stage 4: Build Gmail-safe HTML digest + send via SMTP
# All styles inline, table-based layout, no CSS classes or blocks.
# ---------------------------------------------------------------------------

_FONT     = "-apple-system, 'Helvetica Neue', Arial, sans-serif"
_C_MUTED  = "#888780"
_C_BORDER = "#e5e5e5"
_C_BODY   = "#5F5E5A"

_TAG_THEME = {
    "green":  ("#EAF3DE", "#3B6D11"),
    "amber":  ("#FAEEDA", "#854F0B"),
    "red":    ("#FCEBEB", "#A32D2D"),
    "orange": ("#FAECE7", "#993C1D"),
}


def _tag(text: str, sentiment: str, size: int = 11) -> str:
    bg, fg = _TAG_THEME.get(sentiment, ("#f5f5f3", _C_BODY))
    return (
        f'<span style="background:{bg};color:{fg};font-size:{size}px;'
        f'padding:2px 8px;border-radius:20px;display:inline-block;'
        f'margin:0 4px 4px 0;font-family:{_FONT}">'
        f'{text}</span>'
    )


def _render_tags(tags: list, max_tags: int = 5, size: int = 11) -> str:
    return "".join(_tag(t["text"], t.get("sentiment", "green"), size) for t in tags[:max_tags])


def _leverage_friction(leverage_pts: list, friction_pts: list) -> str:
    html = ""
    if leverage_pts:
        html += (
            f'<p style="margin:0 0 3px 0;font-size:13px;line-height:1.6;font-family:{_FONT}">'
            f'<span style="color:{_C_MUTED}">&#8593; Leverage: </span>'
            f'<span style="color:{_C_BODY}">{"; ".join(leverage_pts)}</span></p>'
        )
    if friction_pts:
        html += (
            f'<p style="margin:0 0 10px 0;font-size:13px;line-height:1.6;font-family:{_FONT}">'
            f'<span style="color:{_C_MUTED}">&#8595; Friction: </span>'
            f'<span style="color:#854F0B">{"; ".join(friction_pts)}</span></p>'
        )
    if html and not friction_pts:
        html = html.replace('margin:0 0 3px 0', 'margin:0 0 10px 0')
    return html or f'<p style="margin:0 0 10px 0;font-size:13px;color:{_C_BODY};line-height:1.6;font-family:{_FONT}">&nbsp;</p>'


def _stat_cell(number: int, label: str, color: str) -> str:
    return (
        f'<td valign="top" style="background:#f5f5f3;padding:10px 12px;border-radius:4px">'
        f'<div style="font-size:24px;font-weight:500;color:{color};line-height:1;font-family:{_FONT}">'
        f'{number}</div>'
        f'<div style="font-size:11px;color:{_C_MUTED};text-transform:uppercase;'
        f'letter-spacing:0.04em;margin-top:2px;font-family:{_FONT}">{label}</div>'
        f'</td>'
    )


def _section_header(label: str, count_text: str, bar_color: str, label_color: str) -> str:
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-bottom:1px solid #eeece5;margin-bottom:10px">'
        f'<tr>'
        f'<td width="3" style="background:{bar_color};border-radius:2px;font-size:0;line-height:0">&nbsp;</td>'
        f'<td width="8">&nbsp;</td>'
        f'<td style="font-size:13px;font-weight:500;color:{label_color};'
        f'font-family:{_FONT};padding:8px 0">{label}</td>'
        f'<td style="font-size:12px;color:{_C_MUTED};text-align:right;'
        f'font-family:{_FONT};padding:8px 0">{count_text}</td>'
        f'</tr></table>'
    )


def _job_card(job: dict, bg: str, pill_bg: str, pill_color: str) -> str:
    ev        = job["eval"]
    score     = ev.get("score", 0)
    tags_html = _render_tags(ev.get("tags", []))
    rationale = _leverage_friction(ev.get("leveragePoints", []), ev.get("frictionPoints", []))
    link_url  = job.get("url", "#")
    return (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:{bg};border:1px solid {_C_BORDER};border-radius:4px">'
        f'<tr><td style="padding:16px">'
        # Row 1 — title + score pill
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:4px">'
        f'<tr>'
        f'<td style="font-size:15px;font-weight:500;color:#222;font-family:{_FONT}">'
        f'{job["title"]}</td>'
        f'<td width="1" style="white-space:nowrap;padding-left:8px;vertical-align:top">'
        f'<span style="background:{pill_bg};color:{pill_color};font-size:12px;font-weight:500;'
        f'padding:3px 10px;border-radius:20px;font-family:{_FONT};white-space:nowrap">'
        f'{score}%</span>'
        f'</td></tr></table>'
        # Row 2 — company · location
        f'<div style="font-size:13px;color:{_C_MUTED};margin-bottom:8px;font-family:{_FONT}">'
        f'{job["company"]} · {job["location"]}</div>'
        # Row 3 — rationale (leverage / friction)
        f'{rationale}'
        # Row 4 — tags
        f'<div style="margin-bottom:10px">{tags_html}</div>'
        # Row 5 — footer: view link right-aligned
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-size:11px;color:#aaa;font-family:{_FONT}">&nbsp;</td>'
        f'<td style="text-align:right">'
        f'<a href="{link_url}" style="font-size:12px;font-weight:500;color:#185FA5;'
        f'text-decoration:none;border:1px solid #ddd;padding:5px 12px;'
        f'border-radius:4px;font-family:{_FONT};display:inline-block">'
        f'View posting &#8594;</a>'
        f'</td></tr></table>'
        f'</td></tr></table>'
    )


def _skipped_cell(job: dict) -> str:
    ev = job["eval"]
    friction = ev.get("frictionPoints", [])
    first = friction[0] if friction else ""
    skip_tags = [t for t in ev.get("tags", []) if t.get("sentiment") in ("red", "orange")]
    tags = _render_tags(skip_tags, max_tags=3, size=10)
    return (
        f'<td valign="top" style="background:#f5f5f3;padding:10px 12px;border-radius:4px;width:50%">'
        f'<div style="font-size:12px;font-weight:500;color:#333;margin-bottom:2px;font-family:{_FONT}">'
        f'{job["title"]}</div>'
        f'<div style="font-size:11px;color:{_C_MUTED};margin-bottom:6px;font-family:{_FONT}">'
        f'{job["company"]} · {job["location"]}</div>'
        f'<div style="font-size:11px;color:{_C_MUTED};line-height:1.5;margin-bottom:6px;font-family:{_FONT}">'
        f'{first}</div>'
        f'<div>{tags}</div>'
        f'</td>'
    )


def _score_range(jobs: list[dict]) -> str:
    if not jobs:
        return ""
    lo = min(j["eval"].get("score", 0) for j in jobs)
    hi = max(j["eval"].get("score", 0) for j in jobs)
    n  = len(jobs)
    return f"{lo}–{hi}% match · {n} role{'s' if n != 1 else ''}"


def build_digest_payload(jobs: list[dict], run_date: str) -> dict:
    result_jobs = []
    for job in jobs:
        ev = job.get("eval", {})
        result_jobs.append({
            "title":          job["title"],
            "company":        job["company"],
            "location":       job["location"],
            "score":          ev.get("score", 0),
            "verdict":        ev.get("verdict", "skipped"),
            "leveragePoints": ev.get("leveragePoints", []),
            "frictionPoints": ev.get("frictionPoints", []),
            "tags":           ev.get("tags", []),
            "alumniCount":    job.get("alumni_count"),
            "postingUrl":     job.get("url", "#"),
        })
    return {"date": run_date, "totalEvaluated": len(jobs), "jobs": result_jobs}


def render_html(jobs: list[dict], run_date: str) -> str:
    import subprocess, tempfile
    pipeline_dir = Path(__file__).parent
    ts_node      = pipeline_dir / "node_modules" / ".bin" / "ts-node"
    render_script = pipeline_dir / "render.ts"

    if ts_node.exists() and render_script.exists():
        payload = build_digest_payload(jobs, run_date)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(payload, f)
            payload_path = f.name
        try:
            result = subprocess.run(
                [str(ts_node), str(render_script), payload_path],
                capture_output=True, text=True, timeout=30, cwd=str(pipeline_dir),
            )
            if result.returncode == 0 and result.stdout.strip():
                log.info("HTML rendered via React Email (Node)")
                return result.stdout
            log.warning(f"ts-node render failed (rc={result.returncode}): {result.stderr[:300]}")
        except Exception as e:
            log.warning(f"Node render error: {e}")
        finally:
            Path(payload_path).unlink(missing_ok=True)

    log.info("HTML rendered via Python fallback")
    return build_digest_html(jobs, run_date)


def build_digest_html(jobs: list[dict], run_date: str) -> str:
    apply    = [j for j in jobs if j["eval"].get("verdict") == "apply"]
    consider = [j for j in jobs if j["eval"].get("verdict") == "consider"]
    skips    = [j for j in jobs if j["eval"].get("verdict") == "skipped"]

    # --- Stat row ---
    stat_row = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:14px">'
        f'<tr>'
        f'{_stat_cell(len(jobs),    "Roles evaluated", "#5F5E5A")}'
        f'<td width="6">&nbsp;</td>'
        f'{_stat_cell(len(apply),   "Apply now",       "#3B6D11")}'
        f'<td width="6">&nbsp;</td>'
        f'{_stat_cell(len(consider),"Consider",        "#854F0B")}'
        f'</tr></table>'
    )

    # --- Legend ---
    def _dot(bg: str) -> str:
        return (
            f'<td width="8" height="8" style="background:{bg};border-radius:4px;'
            f'font-size:0;line-height:0">&nbsp;</td>'
        )
    legend = (
        f'<table cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px">'
        f'<tr>'
        f'{_dot("#639922")}'
        f'<td style="font-size:12px;color:{_C_MUTED};padding:0 12px 0 5px;font-family:{_FONT}">Strength match</td>'
        f'{_dot("#BA7517")}'
        f'<td style="font-size:12px;color:{_C_MUTED};padding:0 12px 0 5px;font-family:{_FONT}">Proceed with awareness</td>'
        f'{_dot("#A32D2D")}'
        f'<td style="font-size:12px;color:{_C_MUTED};padding:0 12px 0 5px;font-family:{_FONT}">Real concern</td>'
        f'{_dot("#D85A30")}'
        f'<td style="font-size:12px;color:{_C_MUTED};padding:0 0 0 5px;font-family:{_FONT}">Domain gap</td>'
        f'</tr></table>'
    )

    # --- Apply cards ---
    apply_html = ""
    if apply:
        cards = ""
        for i, job in enumerate(apply):
            if i:
                cards += '<tr><td height="12" style="font-size:0;line-height:0">&nbsp;</td></tr>'
            cards += f'<tr><td>{_job_card(job, "#ffffff", "#EAF3DE", "#3B6D11")}</td></tr>'
        apply_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px">'
            f'<tr><td colspan="1" style="padding-bottom:10px">'
            f'{_section_header("Apply", _score_range(apply), "#639922", "#3B6D11")}'
            f'</td></tr>'
            f'{cards}'
            f'</table>'
        )

    # --- Consider cards ---
    consider_html = ""
    if consider:
        cards = ""
        for i, job in enumerate(consider):
            if i:
                cards += '<tr><td height="12" style="font-size:0;line-height:0">&nbsp;</td></tr>'
            cards += f'<tr><td>{_job_card(job, "#fafafa", "#FAEEDA", "#854F0B")}</td></tr>'
        consider_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px">'
            f'<tr><td style="padding-bottom:10px">'
            f'{_section_header("Consider", _score_range(consider), "#BA7517", "#854F0B")}'
            f'</td></tr>'
            f'{cards}'
            f'</table>'
        )

    # --- Skipped 2-column grid ---
    skip_html = ""
    if skips:
        pairs = [skips[i:i + 2] for i in range(0, len(skips), 2)]
        grid_rows = ""
        for pair in pairs:
            grid_rows += '<tr>'
            grid_rows += _skipped_cell(pair[0])
            if len(pair) > 1:
                grid_rows += '<td width="6">&nbsp;</td>'
                grid_rows += _skipped_cell(pair[1])
            else:
                grid_rows += '<td width="6">&nbsp;</td><td style="width:50%">&nbsp;</td>'
            grid_rows += '</tr>'
            grid_rows += '<tr><td colspan="3" height="6" style="font-size:0;line-height:0">&nbsp;</td></tr>'
        skip_html = (
            f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom:20px">'
            f'<tr><td colspan="3" style="padding-bottom:10px">'
            f'{_section_header("Skipped", f"{len(skips)} roles · domain or title mismatch", "#888780", _C_MUTED)}'
            f'</td></tr>'
            f'{grid_rows}'
            f'</table>'
        )

    # --- Footer ---
    footer = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td height="1" style="background:{_C_BORDER};font-size:0;line-height:0">&nbsp;</td></tr>'
        f'<tr><td style="padding-top:12px;font-size:11px;color:#aaa;text-align:center;'
        f'font-family:{_FONT}">Lomis job alert pipeline &middot; powered by Claude Sonnet 4.6 '
        f'&middot; {len(jobs)} roles evaluated</td></tr>'
        f'</table>'
    )

    return (
        f'<!DOCTYPE html><html><head>'
        f'<meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'</head>'
        f'<body style="margin:0;padding:0;background:#ffffff;font-family:{_FONT}">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#ffffff">'
        f'<tr><td align="center">'
        f'<table width="600" cellpadding="0" cellspacing="0" border="0" align="center" '
        f'style="max-width:600px;width:100%">'
        f'<tr><td style="padding:24px">'
        f'<h1 style="font-size:18px;font-weight:500;color:#222;margin:0 0 2px 0;'
        f'font-family:{_FONT}">Personalized Job Alert Digest</h1>'
        f'<p style="font-size:13px;color:{_C_MUTED};margin:0 0 14px 0;'
        f'font-family:{_FONT}">{run_date}</p>'
        f'{stat_row}'
        f'{legend}'
        f'{apply_html}'
        f'{consider_html}'
        f'{skip_html}'
        f'{footer}'
        f'</td></tr></table>'
        f'</td></tr></table>'
        f'</body></html>'
    )


def send_digest(html: str, run_date: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Personalized Job Alert Digest — {run_date}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)
    log.info(f"Digest sent to {RECIPIENT_EMAIL}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_pipeline():
    log.info("=== Pipeline run starting ===")
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    seen   = load_seen_ids()

    # Stage 1: Ingest
    threads = fetch_linkedin_alerts(seen)
    if not threads:
        log.info("No new alert emails. Done.")
        return

    all_jobs: list[dict] = []
    seen_job_ids:  set[str]   = set()
    seen_role_keys: set[tuple] = set()  # dedup same title+company from different locations
    for t in threads:
        jobs_from_thread = extract_jobs(t["body"])
        for job in jobs_from_thread:
            role_key = (job["title"].lower().strip(), job["company"].lower().strip())
            if job["job_id"] not in seen_job_ids and role_key not in seen_role_keys:
                seen_job_ids.add(job["job_id"])
                seen_role_keys.add(role_key)
                all_jobs.append(job)
        if jobs_from_thread:
            seen.add(t["msg_id"])  # only mark seen if we got jobs from it

    if not all_jobs:
        log.info("Emails found but no jobs parsed — run with --debug to inspect email format.")
        return  # don't save seen_ids so emails are retried next run

    if len(all_jobs) > MAX_JOBS_PER_RUN:
        log.info(f"{len(all_jobs)} jobs found, capping at {MAX_JOBS_PER_RUN} (set MAX_JOBS_PER_RUN in .env to change)")
        all_jobs = all_jobs[:MAX_JOBS_PER_RUN]
    else:
        log.info(f"{len(all_jobs)} unique jobs to evaluate")

    # Stage 2: Enrich
    all_jobs = enrich_jobs(all_jobs)

    # Stage 3: Score (one batched call)
    all_jobs = score_jobs_batch(client, all_jobs)
    order = {"apply": 0, "consider": 1, "skipped": 2}
    all_jobs.sort(key=lambda j: (
        order.get(j["eval"].get("verdict", "skipped"), 3),
        -j["eval"].get("score", 0),
    ))

    # Stage 4: Deliver
    run_date = datetime.datetime.now().strftime("%B %d, %Y")
    html = render_html(all_jobs, run_date)
    send_digest(html, run_date)
    save_seen_ids(seen)

    apply_n    = sum(1 for j in all_jobs if j["eval"].get("verdict") == "apply")
    consider_n = sum(1 for j in all_jobs if j["eval"].get("verdict") == "consider")
    log.info(f"=== Done — {len(all_jobs)} evaluated: {apply_n} apply, {consider_n} consider ===")


def debug_emails():
    """Dump the first raw email body to ~/.job_pipeline/debug_email.txt for regex inspection."""
    seen = set()  # ignore seen tracking for debug
    with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
        imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        _, data = imap.search(None, 'FROM "jobalerts-noreply@linkedin.com"')
        all_ids = data[0].split()
        if not all_ids:
            print("No LinkedIn alert emails found.")
            return
        _, raw = imap.fetch(all_ids[-1], "(RFC822)")
        msg = email.message_from_bytes(raw[0][1])
        body = _extract_text(msg)
    out = DATA_DIR / "debug_email.txt"
    out.write_text(body)
    print(f"Raw email body written to: {out}")
    print("--- First 2000 chars ---")
    print(body[:2000])


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug", action="store_true", help="Dump a raw email body to inspect format")
    parser.add_argument("--reset", action="store_true", help="Clear seen-IDs so all emails reprocess")
    args = parser.parse_args()

    if args.reset:
        SEEN_FILE.unlink(missing_ok=True)
        print("Seen IDs cleared — all emails will reprocess on next run.")
    elif args.debug:
        debug_emails()
    else:
        run_pipeline()
