"""scorerole sources — manage company career-page sources.

Commands:
  scorerole sources           show active sources (alias for 'list')
  scorerole sources list      show active sources
  scorerole sources add NAME  add a company by name
  scorerole sources remove    interactive removal
  scorerole sources on        enable curated sources (if currently disabled)
  scorerole sources off       disable curated sources without losing extra companies
"""
from __future__ import annotations

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import requests
import yaml

from .theme import THEME

log = logging.getLogger(__name__)

_COMPANIES_YML = Path(__file__).parent / "sources" / "companies.yml"
_PROFILE_PATH  = Path.home() / ".job_pipeline" / "profile.yaml"

# ── YAML helpers ──────────────────────────────────────────────────────────────

def _load_yml() -> dict:
    with open(_COMPANIES_YML) as f:
        return yaml.safe_load(f) or {}


def _load_profile() -> dict:
    if not _PROFILE_PATH.exists():
        return {}
    try:
        return yaml.safe_load(_PROFILE_PATH.read_text()) or {}
    except Exception:
        return {}


def _save_profile(profile: dict):
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILE_PATH.write_text(yaml.dump(profile, default_flow_style=False, allow_unicode=True))
    _PROFILE_PATH.chmod(0o600)


# ── Name → ATS resolution ─────────────────────────────────────────────────────

def _slug_candidates(name: str) -> list[str]:
    """Generate likely ATS slug candidates from a company name."""
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    words = re.sub(r"[^a-z0-9 ]", "", name.lower()).split()
    candidates = [base]
    if words:
        candidates.append("".join(words))
        candidates.append(words[0])
    return list(dict.fromkeys(candidates))  # deduplicated, order preserved


def _try_greenhouse(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=8,
        )
        return r.status_code == 200
    except Exception:
        return False


def _try_lever(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1",
            timeout=8,
        )
        return r.status_code == 200
    except Exception:
        return False


def _try_ashby(slug: str) -> bool:
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
            timeout=8,
        )
        if r.status_code != 200:
            return False
        data = r.json()
        # A valid org returns a dict with jobPostings key; 404-equivalent returns error
        return isinstance(data, dict) and "jobPostings" in data
    except Exception:
        return False


def resolve_company(name: str) -> dict | None:
    """Try to resolve a company name to {name, ats, slug}.

    Checks companies.yml first (exact + fuzzy), then probes ATS APIs.
    Returns None if unresolvable.
    """
    cfg = _load_yml()
    name_lower = name.lower().strip()

    all_companies = (
        [(c, "greenhouse") for c in cfg.get("greenhouse_companies", [])]
        + [(c, "lever")      for c in cfg.get("lever_companies", [])]
        + [(c, "ashby")      for c in cfg.get("ashby_companies", [])]
    )

    # Exact match in companies.yml
    for co, ats in all_companies:
        if co["name"].lower() == name_lower:
            return {"name": co["name"], "ats": ats, "slug": co["slug"]}

    # Fuzzy: name contains or is contained by
    for co, ats in all_companies:
        co_lower = co["name"].lower()
        if name_lower in co_lower or co_lower in name_lower:
            return {"name": co["name"], "ats": ats, "slug": co["slug"]}

    # Live ATS probe
    slugs = _slug_candidates(name)
    for slug in slugs:
        if _try_greenhouse(slug):
            return {"name": name.strip(), "ats": "greenhouse", "slug": slug}
        if _try_lever(slug):
            return {"name": name.strip(), "ats": "lever", "slug": slug}
        if _try_ashby(slug):
            return {"name": name.strip(), "ats": "ashby", "slug": slug}
        time.sleep(0.2)

    return None


# ── Display helpers ───────────────────────────────────────────────────────────

def _ats_label(ats: str) -> str:
    return {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}.get(ats, ats)


def _print_sources(cfg: dict, profile: dict):
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box as rich_box
        console = Console()
    except ImportError:
        _print_sources_plain(cfg, profile)
        return

    ps = profile.get("proactive_sources", {})
    enabled = ps.get("enabled", False)
    tiers   = set(ps.get("tiers", ["S", "A"]))
    extras  = {e["name"].lower() for e in (ps.get("extra_companies") or [])}
    excludes = {n.lower() for n in (ps.get("exclude_companies") or [])}

    status_str = f"[{THEME['success']}]enabled[/]" if enabled else "[dim]disabled[/dim]"
    console.print()
    console.print(f"  Company sources: {status_str}")
    if not enabled:
        console.print("  [dim]Run `scorerole sources on` to enable.[/dim]")
        console.print()

    table = Table(box=rich_box.SIMPLE, show_header=True, header_style="dim")
    table.add_column("Company",  style="bold", min_width=20)
    table.add_column("ATS",      style="dim",  min_width=12)
    table.add_column("Status",   min_width=12)

    all_companies = (
        [(c, "greenhouse") for c in cfg.get("greenhouse_companies", [])]
        + [(c, "lever")      for c in cfg.get("lever_companies", [])]
        + [(c, "ashby")      for c in cfg.get("ashby_companies", [])]
    )

    curated_shown = False
    for co, ats in sorted(all_companies, key=lambda x: x[0]["name"].lower()):
        name_lower = co["name"].lower()
        if name_lower in excludes:
            table.add_row(co["name"], _ats_label(ats), "[dim]excluded[/dim]")
        elif co.get("tier") in tiers:
            if not curated_shown:
                curated_shown = True
            table.add_row(co["name"], _ats_label(ats), f"[{THEME['success']}]active[/]" if enabled else "[dim]inactive[/dim]")

    for extra in (ps.get("extra_companies") or []):
        table.add_row(extra["name"], _ats_label(extra.get("ats", "")), f"[{THEME['accent']}]added[/]" if enabled else "[dim]inactive[/dim]")

    console.print(table)
    console.print(
        "  [dim]scorerole sources add \\<name>    add a company\n"
        "  scorerole sources remove            remove a company[/dim]"
    )
    console.print()


def _print_sources_plain(cfg: dict, profile: dict):
    ps = profile.get("proactive_sources", {})
    enabled = ps.get("enabled", False)
    tiers   = set(ps.get("tiers", ["S", "A"]))
    excludes = {n.lower() for n in (ps.get("exclude_companies") or [])}

    print(f"\nCompany sources: {'enabled' if enabled else 'disabled'}\n")
    all_companies = (
        [(c, "greenhouse") for c in cfg.get("greenhouse_companies", [])]
        + [(c, "lever")      for c in cfg.get("lever_companies", [])]
        + [(c, "ashby")      for c in cfg.get("ashby_companies", [])]
    )
    for co, ats in sorted(all_companies, key=lambda x: x[0]["name"].lower()):
        name_lower = co["name"].lower()
        if name_lower in excludes:
            status = "excluded"
        elif co.get("tier") in tiers:
            status = "active" if enabled else "inactive"
        else:
            continue
        print(f"  {co['name']:<25} {_ats_label(ats):<12} {status}")
    for extra in (ps.get("extra_companies") or []):
        status = "added (active)" if enabled else "added (inactive)"
        print(f"  {extra['name']:<25} {_ats_label(extra.get('ats','')):<12} {status}")
    print()


# ── Subcommand handlers ───────────────────────────────────────────────────────

def cmd_list():
    cfg     = _load_yml()
    profile = _load_profile()
    _print_sources(cfg, profile)


def cmd_add(name: str):
    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        console = None

    def _print(msg):
        if console:
            console.print(msg)
        else:
            print(msg)

    profile = _load_profile()
    ps = profile.setdefault("proactive_sources", {"enabled": True, "tiers": ["S", "A"], "extra_companies": [], "exclude_companies": []})
    extras: list[dict] = ps.setdefault("extra_companies", [])
    excludes: list[str] = ps.setdefault("exclude_companies", [])

    # Already in extra_companies?
    if any(e["name"].lower() == name.lower() for e in extras):
        _print(f"  [{THEME['warning']}]{name}[/] is already in your sources.")
        return

    # Remove from exclude list if previously excluded
    before = len(excludes)
    ps["exclude_companies"] = [n for n in excludes if n.lower() != name.lower()]
    if len(ps["exclude_companies"]) < before:
        _print(f"  Removed [bold]{name}[/bold] from your exclusion list.")

    # Check if it's already in the curated list
    cfg = _load_yml()
    all_companies = (
        [(c, "greenhouse") for c in cfg.get("greenhouse_companies", [])]
        + [(c, "lever")      for c in cfg.get("lever_companies", [])]
        + [(c, "ashby")      for c in cfg.get("ashby_companies", [])]
    )
    for co, ats in all_companies:
        if co["name"].lower() == name.lower() or name.lower() in co["name"].lower():
            active_tiers = set(ps.get("tiers", ["S", "A"]))
            if co.get("tier") in active_tiers:
                _print(f"  [{THEME['success']}]✓[/]  [bold]{co['name']}[/bold] is already in the curated list and active.")
            else:
                extras.append({"name": co["name"], "ats": ats, "slug": co["slug"]})
                _save_profile(profile)
                _print(f"  [{THEME['success']}]✓[/]  Added [bold]{co['name']}[/bold] ({_ats_label(ats)}) to your sources.")
            return

    # Try ATS resolution
    _print(f"  Looking up [bold]{name}[/bold]…")
    resolved = resolve_company(name)
    if resolved:
        extras.append(resolved)
        _save_profile(profile)
        _print(
            f"  [{THEME['success']}]✓[/]  Added [bold]{resolved['name']}[/bold] "
            f"({_ats_label(resolved['ats'])}, slug: {resolved['slug']})."
        )
    else:
        _print(
            f"\n  [{THEME['warning']}]Couldn't find [bold]{name}[/bold] automatically.[/]\n\n"
            f"  [{THEME['muted']}]We search Greenhouse, Lever, and Ashby — the most common job platforms.\n"
            f"  {name} may use a different or proprietary system.[/]\n\n"
            f"  [{THEME['muted']}]If they do use one of those platforms, find their company ID in\n"
            f"  the URL of their jobs page — it's usually the part right after the domain:[/]\n\n"
            f"  [{THEME['dim']}]  greenhouse.io/[bold]apple[/bold]/jobs   →  slug is 'apple'\n"
            f"  lever.co/[bold]apple[/bold]             →  slug is 'apple'[/]\n\n"
            f"  [{THEME['muted']}]Then add them to [bold]~/.job_pipeline/profile.yaml[/bold]:[/]\n\n"
            f"  [{THEME['dim']}]  proactive_sources:\n"
            f"    extra_companies:\n"
            f"      - name: \"{name}\"\n"
            f"        ats: greenhouse   # or lever / ashby\n"
            f"        slug: their-slug[/]\n"
        )


def cmd_remove():
    try:
        from InquirerPy import inquirer
        from InquirerPy.base.control import Choice as IChoice
    except ImportError:
        print("InquirerPy required for interactive remove. Edit ~/.job_pipeline/profile.yaml directly.")
        return

    try:
        from rich.console import Console
        console = Console()
    except ImportError:
        console = None

    def _print(msg):
        if console:
            console.print(msg)
        else:
            print(msg)

    profile = _load_profile()
    ps      = profile.get("proactive_sources", {})
    tiers   = set(ps.get("tiers", ["S", "A"]))
    extras  = ps.get("extra_companies") or []
    excludes = set(ps.get("exclude_companies") or [])

    cfg = _load_yml()
    all_companies = (
        [(c, "greenhouse") for c in cfg.get("greenhouse_companies", [])]
        + [(c, "lever")      for c in cfg.get("lever_companies", [])]
        + [(c, "ashby")      for c in cfg.get("ashby_companies", [])]
    )

    active_curated = [
        co for co, _ in all_companies
        if co.get("tier") in tiers and co["name"].lower() not in excludes
    ]
    active_extras = [e for e in extras if e["name"].lower() not in excludes]

    choices = []
    for co in sorted(active_curated, key=lambda c: c["name"]):
        choices.append(IChoice(name=f"{co['name']}  (curated)", value=("curated", co["name"])))
    for e in active_extras:
        choices.append(IChoice(name=f"{e['name']}  (added by you)", value=("extra", e["name"])))

    if not choices:
        _print("  No active sources to remove.")
        return

    selected = inquirer.checkbox(
        message="Select companies to remove (Space to toggle, Enter to confirm):",
        choices=choices,
    ).execute()

    if not selected:
        _print("  Nothing changed.")
        return

    extra_names  = {e["name"].lower() for e in extras}
    new_excludes = list(ps.get("exclude_companies") or [])
    new_extras   = list(extras)

    for kind, name in selected:
        if kind == "curated":
            if name.lower() not in {n.lower() for n in new_excludes}:
                new_excludes.append(name)
        elif kind == "extra":
            new_extras = [e for e in new_extras if e["name"].lower() != name.lower()]

    ps["exclude_companies"] = new_excludes
    ps["extra_companies"]   = new_extras
    _save_profile(profile)

    removed = [name for _, name in selected]
    _print(f"  [{THEME['success']}]✓[/]  Removed: {', '.join(removed)}")


def cmd_on():
    profile = _load_profile()
    ps = profile.setdefault("proactive_sources", {})
    ps["enabled"] = True
    if "tiers" not in ps:
        ps["tiers"] = ["S", "A"]
    _save_profile(profile)
    try:
        from rich.console import Console
        Console().print(f"  [{THEME['success']}]✓[/]  Company sources enabled.")
    except ImportError:
        print("  Company sources enabled.")


def cmd_off():
    profile = _load_profile()
    ps = profile.setdefault("proactive_sources", {})
    ps["enabled"] = False
    _save_profile(profile)
    try:
        from rich.console import Console
        Console().print("  [dim]Company sources disabled. Your source list is preserved.[/dim]")
    except ImportError:
        print("  Company sources disabled.")


# ── Entry point ───────────────────────────────────────────────────────────────

def run_sources(action: str | None, name: str | None = None):
    if action in (None, "list"):
        cmd_list()
    elif action == "add":
        if not name:
            print("Usage: scorerole sources add <company name>")
            return
        cmd_add(name)
    elif action == "remove":
        cmd_remove()
    elif action == "on":
        cmd_on()
    elif action == "off":
        cmd_off()
    else:
        print(f"Unknown sources action: {action!r}")
        print("Usage: scorerole sources [list | add <name> | remove | on | off]")
