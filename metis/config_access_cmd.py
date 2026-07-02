"""metis config access — connect or reconnect your inbox (Gmail or Outlook)."""
from __future__ import annotations

from .prompt_utils import ask_yes_no
from .theme import THEME, INQUIRER_STYLE, console

_NO_INBOX_MSG = (
    "  [{warning}]⚠[/]  Inbox not connected — email alert sources will be skipped.\n"
    "  [dim]Run [bold]metis config access[/bold] anytime to connect.[/dim]"
)


def _oauth_flow(provider_choice: str, inquirer) -> None:
    """Run the browser OAuth flow with one retry on timeout."""
    from .auth import gmail_oauth, outlook_oauth

    for attempt in range(2):
        console.print("\n  [dim]Opening your browser…[/dim]\n")
        try:
            if provider_choice == "gmail":
                email_addr = gmail_oauth.connect()
                console.print(f"\n  [{THEME['success']}]✓[/]  Gmail connected ({email_addr})\n")
            else:
                email_addr = outlook_oauth.connect()
                console.print(f"\n  [{THEME['success']}]✓[/]  Outlook connected ({email_addr})\n")
            return
        except Exception as e:
            if attempt == 0:
                if isinstance(e, TimeoutError):
                    console.print(f"\n  [{THEME['warning']}]⚠[/]  OAuth timed out — no response from browser.")
                else:
                    console.print(f"\n  [{THEME['warning']}]⚠[/]  OAuth connection failed: {e}")
                retry = ask_yes_no(
                    message="  › Try again?",
                    default=True,
                    style=INQUIRER_STYLE,
                )
                if not retry:
                    break
            else:
                console.print(
                    f"\n  [{THEME['warning']}]⚠[/]  Could not connect inbox.\n"
                    f"  [dim]{e}\n"
                    "  Run [bold]metis config access[/bold] anytime to connect — it only takes a minute.[/dim]"
                )


def run_config_access() -> None:
    """Flow 4 + 5: standalone metis config access command."""
    from .auth import gmail_oauth, outlook_oauth
    from .auth.state import infer_connected_provider, provider_label, provider_token_path
    from InquirerPy import inquirer
    from InquirerPy.base.control import Choice

    gmail_connected   = gmail_oauth.is_connected()
    outlook_connected = outlook_oauth.is_connected()

    connected_provider = infer_connected_provider()
    if connected_provider:
        provider   = provider_label(connected_provider)
        token_path = provider_token_path(connected_provider)
        import json
        try:
            stored = json.loads(token_path.read_text())
            email_addr = stored.get("email", "")
        except Exception:
            email_addr = ""
        status = f"{provider} — {email_addr}" if email_addr else provider
        console.print(f"\n  [{THEME['success']}]✓[/]  Inbox connected — {status}")
        reconnect = ask_yes_no(
            message="  › Reconnect with a different account?",
            default=False,
            style=INQUIRER_STYLE,
        )
        if not reconnect:
            return

    # Default to the opposite of whatever is currently connected
    connected_provider = infer_connected_provider()
    if connected_provider == "gmail_oauth":
        ordered = [
            Choice(value="outlook", name="Outlook (includes Hotmail/Live)"),
            Choice(value="gmail",   name="Gmail"),
        ]
    else:
        ordered = [
            Choice(value="gmail",   name="Gmail"),
            Choice(value="outlook", name="Outlook (includes Hotmail/Live)"),
        ]
    provider_choice = inquirer.select(
        message="  › Which inbox?",
        choices=ordered,
        style=INQUIRER_STYLE,
    ).execute()

    _oauth_flow(provider_choice, inquirer)
