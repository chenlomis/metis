import os, stat, json, logging, smtplib, subprocess, tempfile, datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

log = logging.getLogger(__name__)


def _make_greeting(name: str, apply_count: int) -> str:
    """Generate a time-aware, count-sensitive greeting for the digest header."""
    hour = datetime.datetime.now().hour
    if 5 <= hour < 12:
        prefix = f"Good morning, {name}."
    elif 12 <= hour < 17:
        prefix = f"Good afternoon, {name}."
    else:
        prefix = f"Evening, {name}."

    if apply_count >= 1:
        s = 's' if apply_count != 1 else ''
        return f"{prefix} {apply_count} role{s} worth your time today."
    return f"{prefix} Quiet batch today — nothing in the apply tier."


# Credentials needed by send_digest — read from env directly
GMAIL_ADDRESS      = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
RECIPIENT_EMAIL    = os.getenv("RECIPIENT_EMAIL", GMAIL_ADDRESS)

_FONT     = "-apple-system, 'Helvetica Neue', Arial, sans-serif"
_C_MUTED  = "#888780"
_C_BORDER = "#e5e5e5"
_C_BODY   = "#5F5E5A"

_TAG_THEME = {
    "green": ("#EAF3DE", "#3B6D11"),
    "amber": ("#FAEEDA", "#854F0B"),
    "red":   ("#FCEBEB", "#A32D2D"),
    # Legacy fallbacks — orange collapsed into amber
    "orange": ("#FAEEDA", "#854F0B"),
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


def _coerce_list(val) -> list:
    """Ensure val is a list — coerces bare strings Claude occasionally returns."""
    if isinstance(val, list):
        return val
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    return []


def _leverage_friction(leverage_pts, friction_pts) -> str:
    leverage_pts = _coerce_list(leverage_pts)
    friction_pts = _coerce_list(friction_pts)
    html = ""
    for pt in leverage_pts:
        html += (
            f'<p style="margin:0 0 4px 0;font-size:13px;line-height:1.6;font-family:{_FONT}">'
            f'<span style="color:{_C_MUTED}">&#8593; </span>'
            f'<span style="color:{_C_BODY}">{pt}</span></p>'
        )
    for pt in friction_pts:
        html += (
            f'<p style="margin:0 0 10px 0;font-size:13px;line-height:1.6;font-family:{_FONT}">'
            f'<span style="color:{_C_MUTED}">&#8595; </span>'
            f'<span style="color:#854F0B">{pt}</span></p>'
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


def render_score_breakdown(job: dict) -> str:
    """Return a self-contained <details> block showing the 6-dimension score table.

    Returns "" when no dimensions are present (old runs, parse errors, filtered roles)
    so callers can safely concatenate without an if-guard.
    """
    dims = job.get("eval", {}).get("dimensions", [])
    if not dims:
        return ""

    rows = ""
    for d in dims:
        name    = d.get("name", "").replace("_", " ").title()
        weight  = d.get("weight", 0.0)
        score   = d.get("score", 0)
        contrib = d.get("weighted_contribution", 0.0)
        rat     = d.get("rationale", "")
        bar_w   = max(2, int(score * 0.6))  # max bar width ~60px
        bar_col = "#3B6D11" if score >= 75 else ("#854F0B" if score >= 55 else "#A32D2D")
        rows += (
            f'<tr style="border-bottom:1px solid #f0efeb">'
            f'<td style="padding:6px 8px 6px 0;font-size:11px;color:#5F5E5A;'
            f'font-family:{_FONT};white-space:nowrap;width:140px">{name}</td>'
            f'<td style="padding:6px 4px;text-align:center;font-size:11px;'
            f'color:{_C_MUTED};font-family:{_FONT};white-space:nowrap">'
            f'{int(weight * 100)}%</td>'
            f'<td style="padding:6px 4px">'
            f'<div style="background:#f0efeb;border-radius:2px;height:6px;width:60px">'
            f'<div style="background:{bar_col};border-radius:2px;height:6px;width:{bar_w}px"></div>'
            f'</div></td>'
            f'<td style="padding:6px 4px;text-align:right;font-size:11px;font-weight:500;'
            f'color:{bar_col};font-family:{_FONT};white-space:nowrap">{score}</td>'
            f'<td style="padding:6px 0 6px 8px;font-size:11px;color:{_C_MUTED};'
            f'font-family:{_FONT};line-height:1.4">{rat}</td>'
            f'</tr>'
        )

    return (
        f'<details style="margin:8px 0 4px 0">'
        f'<summary style="font-size:11px;color:{_C_MUTED};cursor:pointer;'
        f'font-family:{_FONT};list-style:none;outline:none">'
        f'&#9656; Score breakdown</summary>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin-top:8px;border-top:1px solid #eeece5">'
        f'{rows}'
        f'</table>'
        f'</details>'
    )


def _job_card(job: dict, bg: str, pill_bg: str, pill_color: str) -> str:
    ev          = job["eval"]
    score       = ev.get("score", 0)
    tags_html   = _render_tags(ev.get("tags", []))
    rationale   = _leverage_friction(ev.get("leveragePoints", []), ev.get("frictionPoints", []))
    link_url    = job.get("url", "#")
    alumni      = job.get("alumni_count")
    alumni_html = (
        f'<span style="font-size:11px;color:{_C_MUTED};font-family:{_FONT}">'
        f'{alumni} alumni</span>'
        if alumni else ""
    )
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
        f'{job["company"]}'
        f'{(" · " + job["location"]) if job.get("location") else ""}'
        f'</div>'
        # Row 3 — rationale (leverage / friction)
        f'{rationale}'
        # Row 4 — tags
        f'<div style="margin-bottom:6px">{tags_html}</div>'
        # Row 5 — expandable score breakdown
        f'{render_score_breakdown(job)}'
        # Row 6 — footer: alumni count left, view link right
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td style="font-size:11px;color:{_C_MUTED};font-family:{_FONT}">{alumni_html}</td>'
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
    friction = _coerce_list(ev.get("frictionPoints", []))
    first = friction[0] if friction else ""
    skip_tags = [t for t in ev.get("tags", []) if t.get("sentiment") in ("red", "amber", "orange")]
    tags = _render_tags(skip_tags, max_tags=3, size=10)
    return (
        f'<td valign="top" style="background:#f5f5f3;padding:10px 12px;border-radius:4px;width:50%">'
        f'<div style="font-size:12px;font-weight:500;color:#333;margin-bottom:2px;font-family:{_FONT}">'
        f'{job["title"]}</div>'
        f'<div style="font-size:11px;color:{_C_MUTED};margin-bottom:6px;font-family:{_FONT}">'
        f'{job["company"]}'
        f'{(" · " + job["location"]) if job.get("location") else ""}'
        f'</div>'
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


def build_digest_payload(
    jobs: list[dict],
    run_date: str,
    deal_breaker_count: int = 0,
    candidate_name: str = "",
    greeting: str = "",
) -> dict:
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
    return {
        "date":             run_date,
        "totalEvaluated":   len(jobs),
        "candidateName":    candidate_name,
        "greeting":         greeting,
        "dealBreakerCount": deal_breaker_count,
        "jobs":             result_jobs,
    }


def render_html(jobs: list[dict], run_date: str, deal_breaker_count: int = 0) -> str:
    from .profile import load_profile_yaml
    profile        = load_profile_yaml() or {}
    candidate_name = profile.get("candidate", {}).get("name", "")
    apply_count    = sum(1 for j in jobs if j.get("eval", {}).get("verdict") == "apply")
    greeting       = _make_greeting(candidate_name, apply_count) if candidate_name else ""

    pipeline_dir  = Path(__file__).parent.parent  # scorerole/ → project root
    ts_node       = pipeline_dir / "node_modules" / ".bin" / "ts-node"
    render_script = pipeline_dir / "render.ts"

    if ts_node.exists() and render_script.exists():
        payload = build_digest_payload(jobs, run_date, deal_breaker_count, candidate_name, greeting)
        # ts-node reads the payload file after Python closes the fd, so we use mkstemp
        # (delete=False equivalent) and restrict permissions before writing any data.
        fd, payload_path = tempfile.mkstemp(suffix=".json")
        os.chmod(fd, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 — contains scored job data
        with os.fdopen(fd, "w") as f:               # fdopen takes ownership of fd
            json.dump(payload, f)
        # fd is now closed; payload_path is written. Clean up regardless of what follows.
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
    return build_digest_html(jobs, run_date, deal_breaker_count, candidate_name, greeting)


def build_digest_html(jobs: list[dict], run_date: str, deal_breaker_count: int = 0, candidate_name: str = "", greeting: str = "") -> str:
    # `jobs` contains only scored roles (apply / consider / skipped).
    # Deal-breaker filtered roles are removed upstream in pipeline.py before render;
    # their count is passed in as deal_breaker_count and shown only in the footer.
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
        f'<td style="font-size:12px;color:{_C_MUTED};padding:0 0 0 5px;font-family:{_FONT}">Real concern</td>'
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
    filtered_note = (
        f' &middot; <span style="color:#A32D2D">'
        f'{deal_breaker_count} filtered by deal&#8209;breaker</span>'
        if deal_breaker_count else ""
    )
    footer = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td height="1" style="background:{_C_BORDER};font-size:0;line-height:0">&nbsp;</td></tr>'
        f'<tr><td style="padding-top:12px;font-size:11px;color:#aaa;text-align:center;'
        f'font-family:{_FONT}">ScoreRole &middot; powered by Claude '
        f'&middot; {len(jobs)} roles evaluated{filtered_note}</td></tr>'
        f'</table>'
    )

    greeting_html = (
        f'<p style="font-size:14px;color:{_C_MUTED};margin:0 0 6px 0;'
        f'font-family:{_FONT};line-height:1.5">{greeting}</p>'
        if greeting else ""
    )
    wordmark_row = (
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-bottom:1px solid #eeeeee;margin-bottom:0">'
        f'<tr>'
        f'<td style="padding:12px 0">'
        f'<table cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="8" height="8" style="background:#1f2118;border-radius:2px;font-size:0;line-height:0">&nbsp;</td>'
        f'<td style="padding-left:7px;font-size:12px;font-weight:500;color:#1f2118;font-family:{_FONT}">ScoreRole</td>'
        f'</tr></table>'
        f'</td>'
        f'<td style="padding:12px 0;text-align:right;font-size:11px;color:{_C_MUTED};font-family:{_FONT}">{run_date}</td>'
        f'</tr></table>'
    )

    import json as _json
    _job_payload = [
        {
            "title":      j["title"],
            "company":    j["company"],
            "postingUrl": j.get("url", ""),
            "score":      j["eval"].get("score", 0),
            "verdict":    j["eval"].get("verdict", "skipped"),
        }
        for j in jobs
        if j["eval"].get("verdict") in ("apply", "consider")
    ]

    return (
        f'<!DOCTYPE html><html><head>'
        f'<meta charset="UTF-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        # Machine-readable data island — parsed by backfill_from_digests() in track.py
        f'<script type="application/json" id="scorerole-data">'
        f'{_json.dumps({"date": run_date, "jobs": _job_payload})}'
        f'</script>'
        f'</head>'
        f'<body style="margin:0;padding:0;background:#f5f5f3;font-family:{_FONT}">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#f5f5f3">'
        f'<tr><td align="center">'
        f'<table width="600" cellpadding="0" cellspacing="0" border="0" align="center" '
        f'style="max-width:600px;width:100%">'
        f'<tr><td style="padding:16px 12px">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="background:#ffffff;border:1px solid #e5e5e5;border-radius:8px">'
        f'<tr><td style="padding:0">'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="padding:0 20px">'
        f'<tr><td>{wordmark_row}</td></tr>'
        f'<tr><td style="padding:16px 0 0">'
        f'{greeting_html}'
        f'<h1 style="font-size:18px;font-weight:500;color:#1f2118;margin:0 0 14px 0;'
        f'font-family:{_FONT}">Personalized job alert digest</h1>'
        f'{stat_row}'
        f'</td></tr>'
        f'</table>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-top:1px solid #eeeeee;padding:10px 20px">'
        f'<tr><td>{legend}</td></tr>'
        f'</table>'
        f'</td></tr></table>'
        f'<table width="100%" cellpadding="0" cellspacing="0" border="0" style="padding:12px 0">'
        f'<tr><td>'
        f'{apply_html}'
        f'{consider_html}'
        f'{skip_html}'
        f'{footer}'
        f'</td></tr></table>'
        f'</td></tr></table>'
        f'</td></tr></table>'
        f'</body></html>'
    )


def send_digest(html: str, run_date: str, label: str = ""):
    msg = MIMEMultipart("alternative")
    prefix = f"[{label}] " if label else ""
    msg["Subject"] = f"{prefix}Personalized Job Alert Digest — {run_date}"
    msg["From"]    = GMAIL_ADDRESS
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        log.error("DIGEST NOT DELIVERED — Gmail authentication failed. Check GMAIL_APP_PASSWORD in .env: %s", e)
        raise
    except smtplib.SMTPException as e:
        log.error("DIGEST NOT DELIVERED — SMTP error: %s", e)
        raise
    log.info(f"Digest sent to {RECIPIENT_EMAIL}")
