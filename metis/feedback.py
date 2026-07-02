"""metis feedback — interactive calibration feedback capture.

Each confirmed entry is appended to ~/.job_pipeline/feedback.md and logged to
feedback_log.jsonl. The .md file is injected verbatim into the Layer 2 Sonnet
system prompt on every subsequent run (score.py → build_score_system).

Subcommands (routed by pipeline.py):
  metis feedback        — collect → Claude parse → confirm → save
  metis feedback list   — show recent entries
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Optional

from rich.markup import escape as _escape
from rich.padding import Padding
from rich.style import Style
from rich.text import Text

from .prompt_utils import ask_yes_no
from .state import DATA_DIR, LAST_RUN_FILE, FEEDBACK_FILE, FEEDBACK_LOG_FILE
from .theme import (
    THEME, INQUIRER_STYLE, console,
    print_section, print_hint, print_eg, print_confirmed,
)

log = logging.getLogger(__name__)


_FEEDBACK_HEADER = (
    "# Scoring Feedback\n\n"
    "Free-form calibration notes — injected into every scoring run.\n"
    "Add entries via `metis feedback` or edit this file directly.\n"
)


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------

def _feedback_id() -> str:
    """Generate a unique entry ID: fb_YYYYMMDD_<6hex>."""
    return f"fb_{datetime.date.today().strftime('%Y%m%d')}_{secrets.token_hex(3)}"


# ---------------------------------------------------------------------------
# Last-run I/O — written by pipeline, read here for display context
# ---------------------------------------------------------------------------

def save_last_run(scored_jobs: list[dict], run_date: str, filtered_count: int = 0) -> None:
    """Persist a compact run summary to last_run.json for the feedback prompt."""
    apply_roles    = [j for j in scored_jobs if j["eval"].get("verdict") == "apply"]
    consider_roles = [j for j in scored_jobs if j["eval"].get("verdict") == "consider"]
    skipped_count  = sum(1 for j in scored_jobs if j["eval"].get("verdict") == "skipped")
    display_roles  = sorted(
        [j for j in scored_jobs if j["eval"].get("verdict") in ("apply", "consider")],
        key=lambda x: -x["eval"].get("score", 0),
    )
    payload = {
        "run_date":        run_date,
        "run_timestamp":   datetime.datetime.now().isoformat(),
        "total_evaluated": len(scored_jobs),
        "apply_count":     len(apply_roles),
        "consider_count":  len(consider_roles),
        "skipped_count":   skipped_count,
        "filtered_count":  filtered_count,
        "roles": [
            {
                "title":   j["title"],
                "company": j["company"],
                "score":   j["eval"].get("score", 0),
                "verdict": j["eval"].get("verdict"),
            }
            for j in display_roles
        ],
    }
    try:
        DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        LAST_RUN_FILE.write_text(json.dumps(payload, indent=2))
        LAST_RUN_FILE.chmod(0o600)
    except OSError as exc:
        log.warning("Could not save last_run.json: %s", exc)


def load_last_run() -> Optional[dict]:
    if not LAST_RUN_FILE.exists():
        return None
    try:
        return json.loads(LAST_RUN_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Feedback file I/O
# ---------------------------------------------------------------------------

def append_feedback_entry(
    text: str,
    feedback_id: str,
    run_id: Optional[str],
    meta: Optional[dict] = None,
) -> None:
    """Append a tagged, timestamped entry to ~/.job_pipeline/feedback.md.

    Comment header carries traceability metadata — stripped by Claude at
    inference time (HTML comments are ignored in plain-text context).

    Format:
      <!-- id:fb_20260619_a3f2 | run:June_18_2026 | roles:gitlab,workday | dims:culture_values -->
      ## [user] 2026-06-19

      <text>
    """
    meta     = meta or {}
    date_str = datetime.date.today().isoformat()
    comment  = (
        f"<!-- id:{feedback_id}"
        f" | run:{run_id or 'unknown'}"
        f" | roles:{','.join(meta.get('roles', []))}"
        f" | dims:{','.join(meta.get('dims', []))} -->"
    )
    entry = f"\n{comment}\n## [user] {date_str}\n\n{text}\n"

    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if FEEDBACK_FILE.exists():
        FEEDBACK_FILE.write_text(FEEDBACK_FILE.read_text() + entry)
    else:
        FEEDBACK_FILE.write_text(_FEEDBACK_HEADER + entry)
    FEEDBACK_FILE.chmod(0o600)


def save_feedback_entry(text: str) -> None:
    """Compatibility shim — delegates to append_feedback_entry with no metadata."""
    append_feedback_entry(text=text, feedback_id=_feedback_id(), run_id=None)


def load_feedback_text() -> Optional[str]:
    """Return all feedback.md content for prompt injection.

    No TTL — all entries are included. The feedback represents user intent
    calibration, not time-bounded state. If the file ever grows impractically
    large (>3k tokens), add a summarisation step here before returning.

    Called by score.py → build_score_system() on every scoring run.
    """
    if not FEEDBACK_FILE.exists():
        return None
    content = FEEDBACK_FILE.read_text().strip()
    return content if content else None


def write_feedback_log(
    feedback_id: str,
    run_id: Optional[str],
    raw_text: str,
    roles: list[str],
    dims: list[str],
    action_taken: str = "saved",
    conflict_count: int = 0,
) -> None:
    """Append one audit record to feedback_log.jsonl.

    This file is for regression tracking and history display only — it is
    never injected into scoring prompts.

    action_taken values: "saved" | "cancelled" | "discard" | "profile_only" | "empty_input"
    """
    record = {
        "feedback_id":   feedback_id,
        "run_id":        run_id,
        "timestamp":     datetime.datetime.now().isoformat(),
        "action_taken":  action_taken,
        "conflict_count": conflict_count,
        "roles":         roles,
        "dims":          dims,
        "text_length":   len(raw_text),
    }
    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with FEEDBACK_LOG_FILE.open("a") as fh:
        fh.write(json.dumps(record) + "\n")
    FEEDBACK_LOG_FILE.chmod(0o600)


# ---------------------------------------------------------------------------
# Entry parser — for `metis feedback list`
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--\s*id:(\S+?)\s*\|.*?-->")
_HEADER_RE  = re.compile(r"^## (.+?)$", re.MULTILINE)


def _parse_entries(content: str) -> list[dict]:
    """Parse feedback.md into a list of entry dicts for display.

    Handles both new format (with <!-- id:... --> comment header) and the
    legacy format (bare ## YYYY-MM-DD header from older versions).

    Returns list of {date, id, source, first_line}, oldest first.
    """
    entries: list[dict] = []
    # Pre-scan all comment positions so we can match them to headers without
    # accidentally picking up a comment from a prior entry.
    comment_positions = [
        (m.start(), m.end(), m.group(1))
        for m in _COMMENT_RE.finditer(content)
    ]
    header_positions = [(m.start(), m.group(1).strip()) for m in _HEADER_RE.finditer(content)]

    for i, (pos, header) in enumerate(header_positions):
        end = header_positions[i + 1][0] if i + 1 < len(header_positions) else len(content)

        # Find the nearest comment that ends before this header AND starts after
        # the previous header (so we don't steal another entry's comment).
        prev_bound = header_positions[i - 1][0] if i > 0 else 0
        fb_id = None
        for c_start, c_end, c_id in comment_positions:
            if c_start >= prev_bound and c_end <= pos:
                fb_id = c_id   # last qualifying match wins (closest to header)

        date_str   = re.sub(r"\[(?:user|auto)\]\s*", "", header).strip()
        source     = "[auto]" if "[auto]" in header else "[user]"
        body_text  = content[pos:end]
        body_lines = [
            l.strip() for l in body_text.splitlines()[1:]
            if l.strip() and not l.strip().startswith("<!--")
            ]
        first_line = body_lines[0] if body_lines else ""

        entries.append({
            "date":       date_str,
            "id":         fb_id,
            "source":     source,
            "first_line": first_line,
        })

    return entries


# ---------------------------------------------------------------------------
# Claude processing — parse feedback into structured form
# ---------------------------------------------------------------------------

# User-turn analysis prompt — identity and grounding rules live in
# prompts.FEEDBACK_IDENTITY (injected as system prompt in the API call).
_ANALYSIS_PROMPT = """\
Analyze this feedback. Return JSON only — no prose, no markdown fences.

EXISTING FEEDBACK (may be empty):
{existing}

NEW FEEDBACK:
{new}

Return exactly this shape (empty list [] for fields with no items):
{{
  "roles": [{{"company": "", "title": "", "score": null, "direction": "too_low|too_high|right|unclear", "dim": "", "note": ""}}],
  "general_notes": ["..."],
  "conflicts": [{{"new_statement": "", "existing_statement": "", "description": ""}}],
  "profile_items": [],
  "dims": []
}}
"""


def _claude_process(
    raw_text: str,
    existing_feedback: Optional[str],
    api_key: str,
    candidate_name: str = "the candidate",
) -> Optional[dict]:
    """Parse feedback via Haiku. Returns structured dict or None on any failure.

    Identity and grounding rules are passed as a system prompt (prompts.py).
    The analysis format is the user turn. This separation ensures Claude's
    constraints are anchored before it sees the feedback content.

    Degrades gracefully: on failure the raw text is still saved unanalysed.
    """
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic package not available — saving raw text without analysis")
        return None

    from .prompts import feedback_system_prompt

    model       = os.getenv("PRESCREEN_MODEL", "claude-haiku-4-5-20251001")
    system_text = feedback_system_prompt(candidate_name)
    user_text   = _ANALYSIS_PROMPT.format(
        existing=existing_feedback or "(none)",
        new=raw_text,
    )
    try:
        client   = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_text,
            messages=[{"role": "user", "content": user_text}],
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$",          "", raw)
        result = json.loads(raw)
        for key in ("roles", "general_notes", "conflicts", "profile_items", "dims"):
            result.setdefault(key, [])
        return result
    except Exception as exc:
        log.warning("Feedback processing failed (%s) — saving raw text without analysis", exc)
        return None


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _show_last_run_summary(run: dict) -> None:
    roles    = run.get("roles", [])
    apply_r  = [r for r in roles if r["verdict"] == "apply"]
    consider = [r for r in roles if r["verdict"] == "consider"]

    t = Text()
    t.append("  Last run: ", style=Style(color=THEME["muted"]))
    t.append(run.get("run_date", "unknown"), style=Style(color=THEME["bright"]))
    t.append(
        f"  ·  {run.get('total_evaluated', 0)} evaluated"
        f"  ·  {run.get('apply_count', 0)} apply"
        f"  ·  {run.get('consider_count', 0)} consider",
        style=Style(color=THEME["dim"]),
    )
    console.print(t)
    console.print()

    for r in apply_r[:4]:
        line = Text()
        line.append(f"    {r['score']}%  ", style=Style(color=THEME["accent"]))
        line.append(f"{r['title']} — {r['company']}", style=Style(color=THEME["muted"]))
        console.print(line)
    for r in consider[:2]:
        line = Text()
        line.append(f"    {r['score']}%  ", style=Style(color=THEME["dim"]))
        line.append(f"{r['title']} — {r['company']}", style=Style(color=THEME["dim"]))
        console.print(line)


def _print_parsed(parsed: dict) -> None:
    """Display Claude's extracted understanding of the feedback."""
    console.print()
    print_section("→", "Understood", "what I extracted from your feedback")
    console.print()

    import textwrap as _tw
    prefix2 = "  "
    prefix5 = "     "
    avail2 = max(40, console.width - len(prefix2))

    for r in parsed.get("roles", []):
        direction = r.get("direction", "unclear")
        arrow = "↑" if direction == "too_low" else ("↓" if direction == "too_high" else "·")
        color = (THEME["success"] if direction == "too_low"
                 else (THEME["warning"] if direction == "too_high" else THEME["muted"]))
        note_part = f"  — {r['note']}" if r.get("note") else ""
        first_line = f"{arrow}  {r.get('company', '')} / {r.get('dim', 'general')}{note_part}"
        wrapped = _tw.wrap(first_line, width=avail2) or [first_line]
        for i, wl in enumerate(wrapped):
            if i == 0:
                company = _escape(r.get('company', ''))
                dim     = _escape(r.get('dim', 'general'))
                note    = f"  [{THEME['muted']}]— {_escape(r['note'])}[/]" if r.get("note") else ""
                console.print(
                    f"{prefix2}[{color}]{arrow}[/]  "
                    f"[{THEME['bright']}]{company} /[/] "
                    f"[{THEME['accent']}]{dim}[/]{note}"
                )
            else:
                console.print(f"{prefix5}[{THEME['muted']}]{_escape(wl)}[/]")

    for note in parsed.get("general_notes", []):
        lines = _tw.wrap(note, width=avail2 - 3) or [note]
        for i, wl in enumerate(lines):
            if i == 0:
                console.print(f"{prefix2}[{THEME['dim']}]·[/]  [{THEME['muted']}]{_escape(wl)}[/]")
            else:
                console.print(f"{prefix5}[{THEME['muted']}]{_escape(wl)}[/]")


def _collect_input() -> str:
    """Multiline stdin — blank line to finish, Ctrl+C to cancel."""
    console.print()
    print_hint("blank line to submit  ·  Ctrl+C to cancel")
    console.print()
    lines: list[str] = []
    try:
        while True:
            line = input("  > ")
            if not line.strip():
                break
            lines.append(line)
    except (EOFError, KeyboardInterrupt):
        console.print()
        return ""
    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run_feedback(api_key: Optional[str] = None) -> None:
    """Interactive flow: collect → Claude parse → confirm → save."""
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice

    console.print()
    print_section("→", "Feedback", "calibrate scoring from your last digest")
    console.print()

    run = load_last_run()
    if run:
        _show_last_run_summary(run)
    else:
        console.print(
            "  No previous run on record. You can still add calibration notes.\n",
            style=Style(color=THEME["dim"]),
        )

    existing   = load_feedback_text()
    n_existing = existing.count("\n## ") if existing else 0
    if n_existing:
        console.print(
            f"  ({n_existing} prior entr{'y' if n_existing == 1 else 'ies'} on record"
            " — all injected into future runs)",
            style=Style(color=THEME["dim"], italic=True),
        )
    console.print()

    from .theme import print_section_intro
    print_section_intro("Mention the role and what feels off. You can address multiple roles in one entry.")
    print_eg(
        '"GitLab 86% feels right but cultural fit caution is wrong — I\'m comfortable '
        "in large orgs. Workday 58% too low; MCP/SDK work is core to my LLM background.\""
    )

    raw_text = _collect_input()
    if not raw_text:
        console.print("  (skipped — no changes made)\n", style=Style(color=THEME["dim"]))
        return

    # Shared log helper — called on every exit path so all outcomes are recorded.
    feedback_id = _feedback_id()
    run_data    = load_last_run()
    run_id      = (
        run_data.get("run_date", "").replace(" ", "_").replace(",", "")
        if run_data else None
    )

    def _log(action: str, roles: list[str] = [], dims: list[str] = [], conflicts: int = 0) -> None:
        write_feedback_log(
            feedback_id=feedback_id,
            run_id=run_id,
            raw_text=raw_text,
            roles=roles,
            dims=dims,
            action_taken=action,
            conflict_count=conflicts,
        )

    # Claude processing — optional, degrades gracefully
    parsed: Optional[dict] = None
    if api_key:
        console.print("  Processing…", style=Style(color=THEME["dim"], italic=True))
        from .profile import load_profile_yaml
        _profile    = load_profile_yaml() or {}
        _cand_name  = _profile.get("candidate", {}).get("name", "the candidate")
        parsed = _claude_process(raw_text, existing, api_key, candidate_name=_cand_name)

    roles_meta: list[str] = []
    dims_meta:  list[str] = []
    conflict_count: int   = 0

    if parsed:
        _print_parsed(parsed)
        roles_meta     = [r.get("company", "").lower() for r in parsed.get("roles", []) if r.get("company")]
        dims_meta      = parsed.get("dims", [])
        conflict_count = len(parsed.get("conflicts", []))

        # Conflict resolution
        conflicts = parsed.get("conflicts", [])
        if conflicts:
            console.print()
            import textwrap as _tw
            _avail = max(40, console.width - 2)
            for c in conflicts:
                desc = c.get('description', '')
                for i, wl in enumerate(_tw.wrap(desc, width=_avail - 12) or [desc]):
                    if i == 0:
                        console.print(f"  [{THEME['warning']}]⚠  Conflict[/]  [{THEME['muted']}]{_escape(wl)}[/]")
                    else:
                        console.print(f"       [{THEME['muted']}]{_escape(wl)}[/]")
                for label, key in (("new: ", "new_statement"), ("prev:", "existing_statement")):
                    val = c.get(key, '')
                    for i, wl in enumerate(_tw.wrap(val, width=_avail - 7) or [val]):
                        if i == 0:
                            console.print(f"     [{THEME['dim']}]{label}  {_escape(wl)}[/]")
                        else:
                            console.print(f"           [{THEME['dim']}]{_escape(wl)}[/]")
            console.print()
            resolution = inquirer.select(
                message="How to handle?",
                qmark="?",
                choices=[
                    Choice("new",     "New feedback wins  (overrides prior on this dimension)"),
                    Choice("both",    "Keep both  (Claude reconciles at scoring time)"),
                    Choice("discard", "Discard new  (keep prior only)"),
                ],
                style=INQUIRER_STYLE,
            ).execute()
            if resolution == "discard":
                console.print("  (discarded — no changes made)\n", style=Style(color=THEME["dim"]))
                _log("discard", roles_meta, dims_meta, conflict_count)
                return

        # Profile-level routing
        profile_items = parsed.get("profile_items", [])
        if profile_items:
            console.print()
            console.print(
                f"  [{THEME['accent']}]Profile flag[/]  "
                f"[{THEME['muted']}]These look like permanent preferences — "
                "they belong in your profile:[/]"
            )
            for item in profile_items:
                console.print(f"    [{THEME['dim']}]· {_escape(item)}[/]")
            console.print()
            routing = inquirer.select(
                message="Where should these go?",
                qmark="?",
                choices=[
                    Choice("both",    "Save here AND remind me to update profile"),
                    Choice("feedback", "Keep in feedback.md only"),
                    Choice("profile",  "Skip feedback.md  (I'll update profile now)"),
                ],
                style=INQUIRER_STYLE,
            ).execute()
            if routing == "profile":
                console.print(
                    "  Run `metis init` → Quick edits to update your profile.\n",
                    style=Style(color=THEME["dim"]),
                )
                _log("profile_only", roles_meta, dims_meta, conflict_count)
                return
            if routing == "both":
                console.print(
                    "  Reminder: update your profile via `metis init` → Quick edits.",
                    style=Style(color=THEME["warning"]),
                )
                console.print()

    # Confirm + save
    confirmed = ask_yes_no(
        message="Save this feedback?",
        default=True,
        style=INQUIRER_STYLE,
    )

    if not confirmed:
        console.print("  (cancelled)\n", style=Style(color=THEME["dim"]))
        _log("cancelled", roles_meta, dims_meta, conflict_count)
        return

    append_feedback_entry(
        text=raw_text,
        feedback_id=feedback_id,
        run_id=run_id,
        meta={"roles": roles_meta, "dims": dims_meta},
    )
    _log("saved", roles_meta, dims_meta, conflict_count)

    console.print()
    t = Text()
    t.append("✓  ", style=Style(color=THEME["success"]))
    t.append("Feedback saved  ·  ", style=Style(color=THEME["muted"]))
    t.append(feedback_id, style=Style(color=THEME["bright"]))
    t.append("  ·  feedback.md", style=Style(color=THEME["dim"]))
    console.print(t)
    console.print("  Will apply from next run.\n", style=Style(color=THEME["dim"], italic=True))


def run_feedback_list(n: int = 5) -> None:
    """Display the most recent N entries from feedback.md."""
    existing = load_feedback_text()
    if not existing:
        console.print()
        console.print(
            "  No feedback saved yet. Run `metis feedback` to add some.",
            style=Style(color=THEME["dim"]),
        )
        console.print()
        return

    entries = _parse_entries(existing)
    if not entries:
        console.print()
        console.print("  No entries found in feedback.md.", style=Style(color=THEME["dim"]))
        console.print()
        return

    recent = entries[-n:]
    console.print()
    print_section(
        "→", "Recent feedback",
        f"last {len(recent)} of {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}",
    )
    console.print()

    for e in recent:
        t = Text()
        t.append(f"  {e['date']}  ", style=Style(color=THEME["accent"]))
        if e.get("id"):
            t.append(f"{e['id']}  ", style=Style(color=THEME["dim"]))
        snippet = e.get("first_line", "")
        t.append(snippet[:70], style=Style(color=THEME["muted"]))
        if len(snippet) > 70:
            t.append("…", style=Style(color=THEME["dim"]))
        console.print(t)

    console.print()
    console.print(
        f"  View or edit all entries: {FEEDBACK_FILE}",
        style=Style(color=THEME["dim"], italic=True),
    )
    console.print()
