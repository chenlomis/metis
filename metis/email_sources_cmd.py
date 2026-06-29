"""scorerole sources email — manage non-LinkedIn email alert sources.

Commands:
  scorerole sources email         list email alert sources
  scorerole sources email list    list email alert sources
  scorerole sources email add     interactive wizard: add a source
  scorerole sources email remove  interactive removal
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
        "  [dim]scorerole sources email add     add an email alert source\n"
        "  scorerole sources email remove  remove a source[/dim]\n"
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


def cmd_email_add() -> None:
    try:
        from InquirerPy import inquirer
    except ImportError:
        print("InquirerPy required. Run: pip install InquirerPy")
        return

    import os
    gmail_address      = os.getenv("GMAIL_ADDRESS", "")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD", "")

    console.print("")

    company = inquirer.text(
        message="Company name:",
        validate=lambda v: len(v.strip()) > 0,
        invalid_message="Company name is required.",
    ).execute()
    if not company:
        return
    company = company.strip()

    sender = inquirer.text(
        message="Sender address or domain (FROM filter):",
        long_instruction="  Tip: paste the exact From: address shown in the email, e.g. githubinc@nurture.icims.com",
        validate=lambda v: len(v.strip()) > 0,
        invalid_message="Sender address is required.",
    ).execute()
    if not sender:
        return
    sender = sender.strip()

    # Auto-detect format; let user confirm or override
    detected = detect_format(sender)
    fmt_choices = ["ClinchTalent", "iCIMS", "Generic (link scraping)"]
    fmt_map     = {"ClinchTalent": "clinchtalent", "iCIMS": "icims", "Generic (link scraping)": "generic"}

    detected_label = format_label(detected)
    console.print(f"\n  Detected format: [bold]{detected_label}[/bold]")

    confirm_fmt = inquirer.confirm(
        message=f"Use {detected_label} parser?",
        default=True,
    ).execute()

    if not confirm_fmt:
        choice = inquirer.select(
            message="Select parser:",
            choices=fmt_choices,
        ).execute()
        fmt = fmt_map[choice]
    else:
        fmt = detected

    # Check inbox
    if gmail_address and gmail_app_password:
        console.print(f"\n  Checking inbox for emails from [bold]{sender}[/bold]…")
        count = _check_inbox(sender, gmail_address, gmail_app_password)
        if count > 0:
            console.print(f"  [{THEME['success']}]✓[/]  Found {count} email(s) — looks good!")
        elif count == 0:
            console.print(f"  [{THEME['warning']}]⚠[/]  No emails found from this sender yet. "
                   "They'll be picked up once they arrive.")
        else:
            console.print("  [dim]Couldn't verify inbox (will try on next run).[/dim]")

    # Check duplicate
    existing = load_email_sources()
    if any(e["sender"].lower() == sender.lower() for e in existing):
        console.print(f"\n  [{THEME['warning']}]{company}[/] ({sender}) is already configured.")
        return

    save = inquirer.confirm(message="Save this source?", default=True).execute()
    if not save:
        console.print("  [dim]Cancelled.[/dim]")
        return

    existing.append({"company": company, "sender": sender, "format": fmt})
    save_email_sources(existing)
    console.print(
        f"\n  [{THEME['success']}]✓[/]  Added [bold]{company}[/bold] "
        f"({format_label(fmt)}, {sender}).\n"
        f"  Run [bold]scorerole[/bold] to include these in your next digest.\n"
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

def run_email_sources(action: str | None) -> None:
    if action in (None, "list"):
        cmd_email_list()
    elif action == "add":
        cmd_email_add()
    elif action == "remove":
        cmd_email_remove()
    else:
        console.print(f"  Unknown action: {action!r}")
        console.print("  Usage: scorerole sources email [list | add | remove]")
