"""metis sources — manage all job sources.

Two source types:
  alerts     Email-based alerts (LinkedIn, etc.) fetched via Gmail IMAP
  companies  Direct career-site scraping from the company pool

Commands:
  metis sources                    show all active sources
  metis sources list               show all active sources
  metis sources add                interactive — choose alert or company
  metis sources add --all          add every company in the pool
  metis sources add <name>         add a named company from the pool or resolve it live
  metis sources remove             interactive removal
  metis sources on                 enable company scraping
  metis sources off                disable company scraping
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import requests
import yaml

from .theme import THEME, INQUIRER_STYLE, console, print_section

log = logging.getLogger(__name__)

_COMPANIES_YML = Path(__file__).parent / "sources" / "companies.yml"
_PROFILE_PATH  = Path.home() / ".job_pipeline" / "profile.yaml"


# ── Pool helpers ──────────────────────────────────────────────────────────────

def _load_pool() -> list[dict]:
    """Return sorted flat list of all pool companies with {name, ats, slug}."""
    with open(_COMPANIES_YML) as f:
        cfg = yaml.safe_load(f) or {}
    result = []
    for co in cfg.get("greenhouse_companies", []):
        result.append({"name": co["name"], "ats": "greenhouse", "slug": co["slug"]})
    for co in cfg.get("lever_companies", []):
        result.append({"name": co["name"], "ats": "lever", "slug": co["slug"]})
    for co in cfg.get("ashby_companies", []):
        result.append({"name": co["name"], "ats": "ashby", "slug": co["slug"]})
    return sorted(result, key=lambda c: c["name"].lower())


def _pool_by_name() -> dict[str, dict]:
    return {c["name"].lower(): c for c in _load_pool()}


# ── Profile helpers ───────────────────────────────────────────────────────────

def _load_profile() -> dict:
    if not _PROFILE_PATH.exists():
        return {}
    try:
        return yaml.safe_load(_PROFILE_PATH.read_text()) or {}
    except Exception:
        return {}


def _save_profile(profile: dict) -> None:
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(yaml.dump(profile, default_flow_style=False, allow_unicode=True))
    _PROFILE_PATH.chmod(0o600)


def _get_ps(profile: dict) -> dict:
    """Return proactive_sources block, migrating old tiers schema to companies list."""
    ps = dict(profile.get("proactive_sources", {}))
    if "tiers" in ps and "companies" not in ps:
        # Old schema used tiers for auto-selection; tiers are gone from the pool.
        # Migrate to explicit companies list using all pool companies as the default.
        ps["companies"] = [c["name"] for c in _load_pool()]
        del ps["tiers"]
    ps.setdefault("companies", [])
    ps.setdefault("enabled", False)
    return ps


# ── Live ATS resolution (for names not in pool) ───────────────────────────────

def _slug_candidates(name: str) -> list[str]:
    base  = re.sub(r"[^a-z0-9]", "", name.lower())
    words = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    candidates = [base]
    if words:
        candidates.append("".join(words))
        candidates.append(words[0])
    return list(dict.fromkeys(candidates))


def _try_greenhouse(slug: str) -> bool:
    try:
        r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def _try_lever(slug: str) -> bool:
    try:
        r = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1", timeout=8)
        return r.status_code == 200
    except Exception:
        return False


def _try_ashby(slug: str) -> bool:
    try:
        r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", timeout=8)
        if r.status_code != 200:
            return False
        data = r.json()
        return isinstance(data, dict) and "jobPostings" in data
    except Exception:
        return False


def resolve_company(name: str) -> dict | None:
    """Try to resolve a company not in the pool. Returns {name, ats, slug} or None."""
    for slug in _slug_candidates(name):
        if _try_greenhouse(slug):
            return {"name": name.strip(), "ats": "greenhouse", "slug": slug}
        if _try_lever(slug):
            return {"name": name.strip(), "ats": "lever", "slug": slug}
        if _try_ashby(slug):
            return {"name": name.strip(), "ats": "ashby", "slug": slug}
        time.sleep(0.2)
    return None


# ── Display ───────────────────────────────────────────────────────────────────

def _ats_label(ats: str) -> str:
    return {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}.get(ats, ats)


def _get_alert_sources() -> list[dict]:
    """Return configured email alert sources from environment."""
    import os
    alerts = []
    gmail = os.environ.get("GMAIL_ADDRESS", "")
    if gmail:
        alerts.append({"type": "LinkedIn", "account": gmail, "via": "Gmail IMAP"})
    return alerts


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_list() -> None:
    profile  = _load_profile()
    ps       = _get_ps(profile)
    pool     = _pool_by_name()
    enabled  = ps.get("enabled", False)
    selected = ps.get("companies", [])

    console.print()
    print_section("→", "Sources", "all active job sources")
    console.print()

    console.print(f"  [{THEME['muted']}]Alerts  (email)[/]")
    alerts = _get_alert_sources()
    if alerts:
        for a in alerts:
            console.print(
                f"  [{THEME['success']}]✓[/]  [{THEME['bright']}]{a['type']}[/]"
                f"  [{THEME['dim']}]·  {a['account']}  via {a['via']}[/]"
            )
    else:
        console.print(
            f"  [{THEME['dim']}]No email alerts configured."
            f"  Run `metis sources add` and choose Alert for setup instructions.[/]"
        )
    console.print()

    status_str = f"[{THEME['success']}]enabled[/]" if enabled else f"[{THEME['dim']}]disabled[/]"
    console.print(f"  [{THEME['muted']}]Companies  (career sites)  {status_str}[/]")
    if not selected:
        console.print(
            f"  [{THEME['dim']}]No companies selected."
            f"  Run `metis sources add` to pick from the pool.[/]"
        )
    else:
        for name in sorted(selected):
            co  = pool.get(name.lower())
            ats = _ats_label(co["ats"]) if co else "custom"
            console.print(
                f"  [{THEME['success']}]✓[/]  [{THEME['bright']}]{name}[/]"
                f"  [{THEME['dim']}]{ats}[/]"
            )

    console.print()
    console.print(
        f"  [{THEME['dim']}]metis sources add           add a company or alert\n"
        f"  metis sources add --all     add every company in the pool\n"
        f"  metis sources remove        remove sources[/]"
    )
    console.print()


def cmd_add(name: str | None, add_all: bool = False) -> None:
    profile = _load_profile()
    ps      = _get_ps(profile)
    pool    = _pool_by_name()

    if add_all:
        all_names = [c["name"] for c in _load_pool()]
        current   = {n.lower() for n in ps["companies"]}
        new_names = [n for n in all_names if n.lower() not in current]
        if not new_names:
            console.print(f"\n  [{THEME['dim']}]All pool companies are already selected.[/]\n")
            return
        ps["companies"].extend(new_names)
        ps["enabled"] = True
        profile["proactive_sources"] = ps
        _save_profile(profile)
        console.print(
            f"\n  [{THEME['success']}]✓[/]  Added all {len(new_names)} companies from the pool."
            f"  [{THEME['dim']}]({len(ps['companies'])} total selected)[/]\n"
        )
        return

    if not name:
        _cmd_add_interactive(profile, ps, pool)
        return

    _cmd_add_company(name, profile, ps, pool)


def _cmd_add_interactive(profile: dict, ps: dict, pool: dict) -> None:
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
    except ImportError:
        console.print(f"  [{THEME['error']}]InquirerPy required for interactive mode.[/]")
        return

    console.print()
    source_type = inquirer.select(
        message="What type of source?",
        qmark="",
        choices=[
            Choice("company", "Company  — scrape career pages directly"),
            Choice("alert",   "Alert    — set up an email alert source"),
        ],
        style=INQUIRER_STYLE,
    ).execute()

    if source_type == "alert":
        _cmd_add_alert()
        return

    current = {n.lower() for n in ps.get("companies", [])}
    choices = [
        Choice(
            value=co["name"],
            name=f"{co['name']}  [{_ats_label(co['ats'])}]",
            enabled=co["name"].lower() in current,
        )
        for co in _load_pool()
    ]
    console.print(f"  [{THEME['dim']}]Space to toggle · Enter to confirm[/]")
    selected = inquirer.checkbox(
        message="Select companies",
        qmark="",
        choices=choices,
        style=INQUIRER_STYLE,
    ).execute()

    if selected is None:
        return

    ps["companies"] = selected
    ps["enabled"]   = bool(selected)
    profile["proactive_sources"] = ps
    _save_profile(profile)
    n = len(selected)
    console.print(
        f"\n  [{THEME['success']}]✓[/]  {n} compan{'y' if n == 1 else 'ies'} selected.\n"
    )


def _cmd_add_company(name: str, profile: dict, ps: dict, pool: dict) -> None:
    name_lower = name.lower().strip()
    current    = {n.lower() for n in ps.get("companies", [])}

    if name_lower in current:
        console.print(f"\n  [{THEME['warning']}]{name}[/] is already in your sources.\n")
        return

    match = next(
        (co for key, co in pool.items() if name_lower in key or key in name_lower),
        None,
    )
    if match:
        ps["companies"].append(match["name"])
        ps["enabled"] = True
        profile["proactive_sources"] = ps
        _save_profile(profile)
        console.print(
            f"\n  [{THEME['success']}]✓[/]  Added [{THEME['bright']}]{match['name']}[/]"
            f"  [{THEME['dim']}]{_ats_label(match['ats'])}[/]\n"
        )
        return

    console.print(f"\n  [{THEME['dim']}]Not in pool — looking up {name}…[/]")
    resolved = resolve_company(name)
    if resolved:
        ps["companies"].append(resolved["name"])
        ps["enabled"] = True
        profile["proactive_sources"] = ps
        _save_profile(profile)
        console.print(
            f"  [{THEME['success']}]✓[/]  Added [{THEME['bright']}]{resolved['name']}[/]"
            f"  [{THEME['dim']}]{_ats_label(resolved['ats'])}  (resolved live)[/]\n"
        )
        console.print(
            f"  [{THEME['dim']}]Tip — contribute this entry to sources/companies.yml "
            f"so others can use it.[/]\n"
        )
    else:
        console.print(
            f"  [{THEME['warning']}]Could not resolve[/] [{THEME['bright']}]{name}[/].\n"
            f"  [{THEME['dim']}]Add it manually to sources/companies.yml with its ATS slug.[/]\n"
        )


def _cmd_add_alert() -> None:
    console.print()
    console.print(
        f"  [{THEME['muted']}]Email alerts are configured via your .env file.[/]\n"
        f"\n"
        f"  [{THEME['dim']}]Required settings:[/]\n"
        f"  [{THEME['dim']}]  GMAIL_ADDRESS=you@gmail.com[/]\n"
        f"  [{THEME['dim']}]  GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx[/]\n"
        f"\n"
        f"  [{THEME['dim']}]Once set, create a LinkedIn Job Alert and have it sent to that address.\n"
        f"  metis picks up all LinkedIn alert emails automatically on each run.[/]\n"
        f"\n"
        f"  [{THEME['dim']}]See README → Gmail setup for step-by-step instructions.[/]\n"
    )


def cmd_remove() -> None:
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice
    except ImportError:
        console.print(f"  [{THEME['error']}]InquirerPy required for interactive remove.[/]")
        return

    profile  = _load_profile()
    ps       = _get_ps(profile)
    pool     = _pool_by_name()
    selected = ps.get("companies", [])

    if not selected:
        console.print(f"\n  [{THEME['dim']}]No companies to remove.[/]\n")
        return

    choices = [
        Choice(
            value=name,
            name=f"{name}  [{_ats_label(pool.get(name.lower(), {}).get('ats', 'custom'))}]",
        )
        for name in sorted(selected)
    ]

    console.print()
    to_remove = inquirer.checkbox(
        message="Select companies to remove",
        qmark="",
        choices=choices,
        style=INQUIRER_STYLE,
    ).execute()

    if not to_remove:
        console.print(f"\n  [{THEME['dim']}]Nothing changed.[/]\n")
        return

    remove_set      = {n.lower() for n in to_remove}
    ps["companies"] = [n for n in selected if n.lower() not in remove_set]
    profile["proactive_sources"] = ps
    _save_profile(profile)
    console.print(f"\n  [{THEME['success']}]✓[/]  Removed: {', '.join(to_remove)}\n")


def cmd_on() -> None:
    profile = _load_profile()
    ps      = _get_ps(profile)
    ps["enabled"] = True
    profile["proactive_sources"] = ps
    _save_profile(profile)
    console.print(f"\n  [{THEME['success']}]✓[/]  Company sources enabled.\n")


def cmd_off() -> None:
    profile = _load_profile()
    ps      = _get_ps(profile)
    ps["enabled"] = False
    profile["proactive_sources"] = ps
    _save_profile(profile)
    console.print(f"\n  [{THEME['dim']}]Company sources disabled. Your selection is preserved.[/]\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def run_sources(action: str | None, name: str | None = None, add_all: bool = False,
                email_action: str | None = None) -> None:
    if action == "email":
        from .email_sources_cmd import cmd_email_list, cmd_email_add, cmd_email_remove
        if email_action in (None, "list"):
            cmd_email_list()
        elif email_action == "add":
            cmd_email_add()
        elif email_action == "remove":
            cmd_email_remove()
        else:
            console.print(f"  [{THEME['error']}]Unknown email action: {email_action!r}[/]")
    elif action in (None, "list"):
        cmd_list()
    elif action == "add":
        cmd_add(name, add_all=add_all)
    elif action == "remove":
        cmd_remove()
    elif action == "on":
        cmd_on()
    elif action == "off":
        cmd_off()
    else:
        console.print(f"  [{THEME['error']}]Unknown sources action: {action!r}[/]")
        console.print(
            f"  [{THEME['dim']}]Usage: metis sources [list | add [--all | <name>] | remove | on | off | email][/]"
        )
