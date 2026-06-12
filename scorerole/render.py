import os, json, logging, smtplib, subprocess, tempfile
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

log = logging.getLogger(__name__)

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
    skip_tags = [t for t in ev.get("tags", []) if t.get("sentiment") in ("red", "amber", "orange")]
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
    pipeline_dir  = Path(__file__).parent.parent  # scorerole/ → project root
    ts_node       = pipeline_dir / "node_modules" / ".bin" / "ts-node"
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
        f'font-family:{_FONT}">scorerole &middot; powered by Claude '
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
