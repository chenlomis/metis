from __future__ import annotations

from InquirerPy import inquirer

from .application_profile import ENV_FIELDS, application_profile_path, env_application_profile, load_application_profile, save_application_profile
from .theme import INQUIRER_STYLE, console


_LABELS = {
    "first_name": "First name", "last_name": "Last name", "email": "Application email",
    "phone": "Phone", "location": "Current location", "linkedin": "LinkedIn URL",
    "github": "GitHub URL", "pronouns": "Pronouns", "current_employer": "Current employer",
    "gender_identity": "Gender identity", "transgender": "Identify as transgender",
    "sexual_orientation": "Sexual orientation",
    "hispanic_latino": "Hispanic/Latino", "race": "Race/ethnicity",
    "veteran_status": "Protected veteran", "disability": "Disability",
    "work_authorized": "Authorized to work", "sponsorship_required": "Sponsorship required",
    "willing_to_relocate": "Willing to relocate", "referral_source": "Default referral source",
    "default_resume": "Fallback resume path", "chrome_profile_dir": "Chrome profile directory",
    "chrome_profile_name": "Chrome profile name",
}


def run_config_apply(*, show: bool = False) -> None:
    current = {**env_application_profile(), **load_application_profile()}
    path = application_profile_path()
    if show:
        console.print(f"[bold]Application profile[/bold]  {path}")
        for field, label in _LABELS.items():
            value = current.get(field, "")
            console.print(f"  {label}: {value or '[dim]not set[/dim]'}")
        return
    console.print("[bold]Application autofill settings[/bold]")
    console.print("[dim]Saved locally with owner-only permissions. Press Enter to keep each value.[/dim]")
    updated = dict(current)
    for field, label in _LABELS.items():
        updated[field] = inquirer.text(
            message=label, default=str(current.get(field, "")), style=INQUIRER_STYLE,
        ).execute().strip()
    save_application_profile({field: updated.get(field, "") for field in ENV_FIELDS})
    console.print(f"[green]Saved application settings to {path}[/green]")
