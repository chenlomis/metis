"""metis sources email — manage non-LinkedIn email alert sources.

Commands:
  metis sources email         list email alert sources
  metis sources email list    list email alert sources
  metis sources email add     interactive wizard: add a source
  metis sources email remove  interactive removal
"""
from __future__ import annotations

import imaplib
import logging

from .sources.email_alerts import (
    detect_format,
    format_label,
    load_email_sources,
    save_email_sources,
    EMAIL_SOURCES_PATH,
)
from rich import box as rich_box
from rich.table import Table

from .prompt_utils import ask_yes_no
from .theme import THEME, console

log = logging.getLogger(__name__)

# LinkedIn senders are built-in — shown in list but not user-managed
_BUILTIN = [
    {"company": "LinkedIn", "sender": "jobalerts-noreply@linkedin.com", "builtin": True},
    {"company": "LinkedIn", "sender": "jobs-noreply@linkedin.com",      "builtin": True},
    {"company": "LinkedIn", "sender": "jobs-listings@linkedin.com",     "builtin": True},
]


# ── Display ───────────────────────────────────────────────────────────────────

def cmd_email_list() -> None:
    sources = load_email_sources()
    all_rows = _BUILTIN + sources

    console.print()
    table = Table(box=rich_box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("Company",  style="bold", min_width=16)
    table.add_column("Sender",   min_width=36)
    table.add_column("Format",   style="dim", min_width=12)
    table.add_column("Status",   min_width=10)

    for row in all_rows:
        fmt = "built-in" if row.get("builtin") else format_label(
            row.get("format") or detect_format(row["sender"])
        )
        status = "[dim]built-in[/dim]" if row.get("builtin") else f"[{THEME['success']}]active[/]"
        table.add_row(row["company"], row["sender"], fmt, status)

    console.print(table)
    console.print(
        "  [dim]metis sources email add     add an email alert source\n"
        "  metis sources email remove  remove a source[/dim]\n"
    )


# ── Add wizard ────────────────────────────────────────────────────────────────

def _check_inbox(sender: str, gmail_address: str, gmail_app_password: str) -> int:
    """Return count of emails from sender in INBOX (best-effort)."""
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(gmail_address, gmail_app_password)
            imap.select("INBOX")
            _, data = imap.search(None, f'FROM "{sender}"')
            return len(data[0].split())
    except Exception:
        return -1  # -1 = couldn't check


def _preview_jobs(sender: str, gmail_address: str, gmail_app_password: str) -> list[dict]:
    """Fetch the most recent email from sender and parse jobs from it for preview."""
    import datetime
    from .sources.email_alerts import (
        _fetch_emails_from,
        detect_format,
        _parse_wellfound,
        _parse_ladders,
        _parse_clinchtalent,
        _parse_icims,
        _parse_with_llm,
        _parse_generic,
    )

    since = datetime.datetime.now() - datetime.timedelta(days=90)
    try:
        with imaplib.IMAP4_SSL("imap.gmail.com") as imap:
            imap.login(gmail_address, gmail_app_password)
            imap.select("INBOX")
            emails = _fetch_emails_from(imap, sender, since)
    except Exception as e:
        log.warning("Preview fetch failed: %s", e)
        return []

    if not emails:
        return []

    em  = emails[-1]
    fmt = detect_format(sender)

    if fmt == "wellfound":
        return _parse_wellfound(em["html"], sender)
    if fmt == "ladders":
        return _parse_ladders(em["text"], em["html"], sender)
    if fmt == "clinchtalent":
        return _parse_clinchtalent(em["text"], sender)
    if fmt == "icims":
        return _parse_icims(em["html"], sender)
    if fmt == "llm":
        return _parse_with_llm(em["html"], em["text"], sender)
    return _parse_generic(em["html"] or em["text"], sender)


def cmd_email_add(sender_arg: str | None = None) -> None:
    """Add an email alert source.

    If sender_arg is provided ('metis sources email add team@hi.wellfound.com'),
    skips the interactive wizard: fetches a recent email, previews parsed jobs,
    and asks for a single confirmation.

    Without sender_arg, runs the full interactive wizard.
    """
    import os
    gmail_address      = os.getenv("GMAIL_ADDRESS", "")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    # ── Non-interactive path ──────────────────────────────────────────────────
    if sender_arg:
        sender = sender_arg.strip()
        fmt    = detect_format(sender)

        existing = load_email_sources()
        if any(e["sender"].lower() == sender.lower() for e in existing):
            console.print(f"\n  [{THEME['warning']}]⚠[/]  {sender} is already configured.")
            return

        console.print(f"\n  Fetching a recent email from [bold]{sender}[/bold]…")

        jobs: list[dict] = []
        if gmail_address and gmail_app_password:
            jobs = _preview_jobs(sender, gmail_address, gmail_app_password)

        if jobs:
            console.print(f"  Found [bold]{len(jobs)}[/bold] job(s):\n")
            for j in jobs[:8]:
                parts = [f"[bold]{j['title']}[/bold]"]
                if j.get("company"):
                    parts.append(j["company"])
                if j.get("location"):
                    parts.append(j["location"])
                if j.get("salary"):
                    parts.append(j["salary"])
                console.print("    · " + "  —  ".join(parts))
            if len(jobs) > 8:
                console.print(f"    … and {len(jobs) - 8} more")
        else:
            console.print(
                f"  [{THEME['warning']}]⚠[/]  No recent emails found from this sender "
                "(they'll be picked up once they arrive)."
            )

        console.print(
            f"\n  Parser: [bold]{format_label(fmt)}[/bold]  |  Sender: [dim]{sender}[/dim]"
        )

        try:
            save = ask_yes_no(message="Register this source?", default=True)
        except ImportError:
            ans = input("  Register this source? [Y/n] ").strip().lower()
            save = ans in ("", "y", "yes")

        if not save:
            console.print("  [dim]Cancelled.[/dim]")
            return

        companies = [j.get("company", "") for j in jobs if j.get("company")]
        label = companies[0] if len(set(companies)) == 1 else format_label(fmt)

        existing.append({"company": label, "sender": sender, "format": fmt})
        save_email_sources(existing)
        console.print(
            f"\n  [{THEME['success']}]✓[/]  Added [bold]{label}[/bold] "
            f"({format_label(fmt)}, {sender}).\n"
            f"  Run [bold]metis[/bold] to include these in your next digest.\n"
        )
        return

    # ── Interactive wizard ────────────────────────────────────────────────────
    try:
        from InquirerPy import inquirer
    except ImportError:
        print("InquirerPy required. Run: pip install InquirerPy")
        return

    console.print("")

    company = inquirer.text(
        message="Company or source name:",
        validate=lambda v: len(v.strip()) > 0,
        invalid_message="Name is required.",
    ).execute()
    if not company:
        return
    company = company.strip()

    sender = inquirer.text(
        message="Sender address (FROM filter):",
        long_instruction="  Tip: paste the exact From: address shown in the email, e.g. team@hi.wellfound.com",
        validate=lambda v: len(v.strip()) > 0,
        invalid_message="Sender address is required.",
    ).execute()
    if not sender:
        return
    sender = sender.strip()

    fmt = detect_format(sender)
    console.print(f"\n  Parser: [bold]{format_label(fmt)}[/bold]")

    if gmail_address and gmail_app_password:
        console.print(f"  Fetching a recent email from [bold]{sender}[/bold]…")
        jobs = _preview_jobs(sender, gmail_address, gmail_app_password)
        if jobs:
            console.print(f"  Found [bold]{len(jobs)}[/bold] job(s):\n")
            for j in jobs[:5]:
                parts = [f"[bold]{j['title']}[/bold]"]
                if j.get("company"):
                    parts.append(j["company"])
                if j.get("location"):
                    parts.append(j["location"])
                console.print("    · " + "  —  ".join(parts))
            if len(jobs) > 5:
                console.print(f"    … and {len(jobs) - 5} more")
        else:
            console.print(
                f"  [{THEME['warning']}]⚠[/]  No recent emails from this sender yet."
            )

    existing = load_email_sources()
    if any(e["sender"].lower() == sender.lower() for e in existing):
        console.print(f"\n  [{THEME['warning']}]{company}[/] ({sender}) is already configured.")
        return

    save = ask_yes_no(message="Save this source?", default=True)
    if not save:
        console.print("  [dim]Cancelled.[/dim]")
        return

    existing.append({"company": company, "sender": sender, "format": fmt})
    save_email_sources(existing)
    console.print(
        f"\n  [{THEME['success']}]✓[/]  Added [bold]{company}[/bold] "
        f"({format_label(fmt)}, {sender}).\n"
        f"  Run [bold]metis[/bold] to include these in your next digest.\n"
    )


# ── Remove wizard ─────────────────────────────────────────────────────────────

def cmd_email_remove() -> None:
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice as IChoice
    except ImportError:
        print("InquirerPy required. Run: pip install InquirerPy")
        return

    sources = load_email_sources()
    if not sources:
        console.print("  No user-configured email sources to remove.")
        console.print("  [dim](LinkedIn sources are built-in and cannot be removed.)[/dim]")
        return

    choices = [
        IChoice(
            name=f"{s['company']}  —  {s['sender']}",
            value=i,
        )
        for i, s in enumerate(sources)
    ]

    selected = inquirer.checkbox(
        message="Select sources to remove (Space to toggle, Enter to confirm):",
        choices=choices,
    ).execute()

    if not selected:
        console.print("  Nothing changed.")
        return

    keep = [s for i, s in enumerate(sources) if i not in selected]
    save_email_sources(keep)
    removed_names = [sources[i]["company"] for i in selected]
    console.print(f"  [{THEME['success']}]✓[/]  Removed: {', '.join(removed_names)}")


# ── Entry point ───────────────────────────────────────────────────────────────

def run_email_sources(action: str | None, sender_arg: str | None = None) -> None:
    if action in (None, "list"):
        cmd_email_list()
    elif action == "add":
        cmd_email_add(sender_arg=sender_arg)
    elif action == "remove":
        cmd_email_remove()
    else:
        console.print(f"  Unknown action: {action!r}")
        console.print("  Usage: metis sources email [list | add [sender] | remove]")
