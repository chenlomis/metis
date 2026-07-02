"""Profile normalization helpers for init.

This module owns deterministic post-processing for freeform init input. The
LLM extraction layer should preserve raw evidence; this layer maps common
signals into controlled profile fields without relying on provider behavior.
"""
from __future__ import annotations

import re
from typing import Any


ROLE_FAMILIES = {
    "product",
    "engineering",
    "design",
    "research",
    "sales",
    "support",
    "finance",
    "marketing",
    "IT",
    "HR",
    "operations",
    "legal",
    "data",
    "security",
    "other",
    "unknown",
}

ROLE_FAMILY_PATTERNS: list[tuple[str, str]] = [
    ("product", r"\b(product manager|product management|technical pm|growth pm|group pm|principal pm|staff pm|\bpm\b)\b"),
    ("engineering", r"\b(software engineer|backend engineer|frontend engineer|full[-\s]?stack|sre|site reliability|devops|mobile engineer|qa engineer|engineering manager)\b"),
    ("design", r"\b(product designer|ux designer|ui designer|interaction designer|visual designer|design lead|creative director)\b"),
    ("data", r"\b(data scientist|data analyst|data engineer|machine learning engineer|ml engineer|analytics engineer|bi developer|mle)\b"),
    ("research", r"\b(research scientist|researcher|user researcher|ux researcher|quantitative researcher)\b"),
    ("sales", r"\b(account executive|sales|sdr|bdr|sales manager|vp of sales|customer success)\b"),
    ("support", r"\b(support engineer|help desk|technical support|customer support)\b"),
    ("finance", r"\b(finance manager|financial analyst|accountant|controller|fp&a|cfo)\b"),
    ("marketing", r"\b(product marketing|growth marketer|seo|content marketer|demand generation|brand marketing)\b"),
    ("IT", r"\b(systems administrator|sysadmin|network engineer|it manager|it support|database administrator|cloud architect)\b"),
    ("HR", r"\b(hr business partner|human resources|people ops|recruiter|talent acquisition)\b"),
    ("operations", r"\b(operations manager|business operations|revenue operations|strategy and ops|strategy & ops|chief of staff)\b"),
    ("legal", r"\b(legal counsel|attorney|lawyer|paralegal|compliance counsel|general counsel)\b"),
    ("security", r"\b(security engineer|security analyst|ciso|penetration tester|grc|appsec|infosec)\b"),
]

LEVEL_PATTERNS: list[tuple[str, str]] = [
    ("executive", r"\b(vp|svp|evp|c[-\s]?(?:suite|level)|cto|cpo|cfo|cmo|cio|chief)\b"),
    ("director", r"\b(director|head of)\b"),
    ("principal", r"\b(principal|l7)\b"),
    ("staff", r"\b(staff|l6)\b"),
    ("lead", r"\b(lead pm|lead product manager|lead engineer|lead designer|lead data scientist|tech lead)\b"),
    ("senior", r"\b(senior|sr\.?|l5)\b"),
    ("junior", r"\b(junior|associate|entry[-\s]?level|new grad|l3)\b"),
    ("mid", r"\b(mid[-\s]?level|l4)\b"),
]

PRODUCT_ROLE_LABELS = {
    "executive": "Product Executive",
    "director": "Director of Product",
    "principal": "Principal PM",
    "staff": "Staff PM",
    "lead": "Lead PM",
    "senior": "Senior PM",
    "mid": "Product Manager",
    "junior": "Associate PM",
}

ENGINEERING_ROLE_LABELS = {
    "executive": "Engineering Executive",
    "director": "Director of Engineering",
    "principal": "Principal Engineer",
    "staff": "Staff Engineer",
    "lead": "Lead Engineer",
    "senior": "Senior Software Engineer",
    "mid": "Software Engineer",
    "junior": "Junior Software Engineer",
}

DESIGN_ROLE_LABELS = {
    "executive": "Design Executive",
    "director": "Design Director",
    "principal": "Principal Designer",
    "staff": "Staff Designer",
    "lead": "Design Lead",
    "senior": "Senior Designer",
    "mid": "Product Designer",
    "junior": "Junior Designer",
}

DATA_ROLE_LABELS = {
    "executive": "Data Executive",
    "director": "Director of Data",
    "principal": "Principal Data Scientist",
    "staff": "Staff Data Scientist",
    "lead": "Lead Data Scientist",
    "senior": "Senior Data Scientist",
    "mid": "Data Scientist",
    "junior": "Junior Data Analyst",
}


def _low(text: str | None) -> str:
    return (text or "").lower()


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _append_unique(items: list, value: str | None) -> None:
    if value and value not in items:
        items.append(value)


def classify_role_family(text: str | None) -> str:
    """Classify role family from freeform role/search text."""
    low = _low(text)
    if not low.strip():
        return "unknown"
    for family, pattern in ROLE_FAMILY_PATTERNS:
        if re.search(pattern, low, re.IGNORECASE):
            return family
    return "other"


def normalize_level(text: str | None, roles: list[str] | None = None) -> str:
    low = " ".join([text or "", " ".join(roles or [])]).lower()
    if not low.strip():
        return "unknown"
    rank = {
        "junior": 0,
        "mid": 1,
        "senior": 2,
        "lead": 3,
        "staff": 4,
        "principal": 5,
        "director": 6,
        "executive": 7,
    }
    matches = [
        level for level, pattern in LEVEL_PATTERNS
        if re.search(pattern, low, re.IGNORECASE)
    ]
    if matches:
        return min(matches, key=lambda level: rank[level])
    return "unknown"


def normalize_track(text: str | None, role_family: str = "unknown") -> str:
    low = _low(text)
    management = bool(re.search(r"\b(manage a team|lead a team|director|vp|head of)\b", low))
    no_management = bool(re.search(r"\b(no people management|avoid people management|staying away from people management|individual contributor|\bic\b|close to the code)\b", low))
    ic_level = bool(re.search(r"\b(staff|principal|distinguished|senior|lead)\b", low))

    if management and no_management:
        return "flexible"
    if no_management:
        return "ic"
    if management:
        return "management"
    if role_family in {"product", "engineering", "design", "data", "research"} and ic_level:
        return "ic"
    return "unknown"


def normalize_location_preference(text: str | None) -> str:
    low = _low(text)
    if not low.strip():
        return "unknown"
    if re.search(r"\b(remote[-\s]?first|remote|remotely|wfh|work from home|distributed|100%\s+remote|work remotely|operates remotely)\b", low):
        return "remote"
    if re.search(r"\b(hybrid|in[-\s]?office\s+\d|commutable|onsite\s+\d|office\s+\d)\b", low):
        return "hybrid"
    if re.search(r"\b(onsite|on[-\s]?site|in office|office based)\b", low):
        return "onsite"
    if re.search(r"\b(flexible|open to remote|open to hybrid|remote or hybrid)\b", low):
        return "flexible"
    return "unknown"


def normalize_company_stage(text: str | None) -> str:
    low = _low(text)
    if not low.strip():
        return "unknown"
    if re.search(r"\b(pre[-\s]?seed|seed|series a|scrappy startup|early[-\s]?stage)\b", low):
        return "early-stage"
    if re.search(r"\b(series b|series c|growth[-\s]?stage|growth stage|growth|scaling|scale[-\s]?up|post[-\s]?pmf|passed its initial seed)\b", low):
        return "growth-stage"
    if re.search(r"\b(mature|public|fortune 500|faang|large tech|established|enterprise company)\b", low):
        return "mature"
    return "unknown"


def normalize_company_scale(text: str | None) -> str:
    low = _low(text)
    if not low.strip():
        return "unknown"
    if re.search(r"\b(smb|small business|small businesses|small customers)\b", low):
        return "SMB"
    if re.search(r"\b(mid[-\s]?market|middle market)\b", low):
        return "mid-market"
    if re.search(r"\b(enterprise clients|enterprise customers|enterprise buyers|large customers)\b", low):
        return "enterprise"
    return "unknown"


def normalize_team_environment(text: str | None) -> str:
    low = _low(text)
    if not low.strip():
        return "unknown"
    if re.search(
        r"\b(small[-\s]?team|small team footprint|tight[-\s]?knit|lean team|"
        r"relatively small|small,?\s+agile|small team environment|small crew|"
        r"small,?\s+growth-stage team)\b",
        low,
    ):
        return "small-team"
    if re.search(r"\b(mid[-\s]?size team|medium[-\s]?team|moderate team)\b", low):
        return "medium-team"
    if re.search(r"\b(large org|large organization|big company|corporate|enterprise team)\b", low):
        return "large-org"
    return "unknown"


def normalize_company_types(text: str | None) -> list[str]:
    low = _low(text)
    types: list[str] = []
    signals = [
        ("ai-infrastructure", r"\b(ai infra|ai infrastructure)\b"),
        ("developer-tools", r"\b(developer tools?|dev tools?|dev-tools|devtools|for devs|for developers|building for developers|developer-facing platforms?|developer-centric|developer products?)\b"),
        ("cloud-infrastructure", r"\b(cloud infrastructure|cloud platform|iaas)\b"),
        ("data-platform", r"\b(data platform|data infrastructure)\b"),
        ("analytics", r"\b(analytics|business intelligence|bi)\b"),
        ("cybersecurity", r"\b(cybersecurity|security platform|infosec|appsec)\b"),
        ("devops", r"\b(devops|ci/cd|developer productivity)\b"),
        ("saas", r"\b(saas)\b"),
        ("fintech", r"\b(fintech|financial technology|payments|banking)\b"),
        ("healthtech", r"\b(healthtech|healthcare tech|digital health)\b"),
        ("edtech", r"\b(edtech|education technology)\b"),
        ("hrtech", r"\b(hrtech|people tech|talent platform)\b"),
        ("legaltech", r"\b(legaltech|legal technology)\b"),
        ("climatetech", r"\b(climatetech|climate tech|sustainability)\b"),
        ("govtech", r"\b(govtech|government technology)\b"),
        ("retailtech", r"\b(retailtech|commerce platform)\b"),
        ("logistics", r"\b(logistics|supply chain|transportation)\b"),
        ("marketplace", r"\b(marketplace)\b"),
        ("consumer", r"\b(consumer social|consumer app|consumer product|gaming)\b"),
        ("enterprise-software", r"\b(enterprise software)\b"),
    ]
    for label, pattern in signals:
        if re.search(pattern, low, re.IGNORECASE):
            _append_unique(types, label)
    return types


def normalize_customer_types(text: str | None) -> list[str]:
    low = _low(text)
    types: list[str] = []
    if re.search(r"\b(developer tools?|devtools?|devs|developers?|engineers?|engineering personas?|technical users?|developer-centric|developer products?)\b", low):
        _append_unique(types, "developer")
    if re.search(r"\b(b2b|enterprise clients?|enterprise customers?|saas buyers?|business customers?)\b", low):
        _append_unique(types, "b2b")
    if re.search(r"\b(b2c|consumer|everyday users?)\b", low):
        _append_unique(types, "b2c")
    if re.search(r"\b(marketplace)\b", low):
        _append_unique(types, "marketplace")
    if re.search(r"\b(internal tools?|internal platform|employees?|operators?)\b", low):
        _append_unique(types, "internal")
    if re.search(r"\b(smb|small business|small businesses)\b", low):
        _append_unique(types, "smb")
    if re.search(r"\b(enterprise)\b", low):
        _append_unique(types, "enterprise")
    return types


def normalize_direction(text: str | None) -> str | None:
    source = (text or "").strip()
    if not source:
        return None
    m = re.search(
        r"\b(?:excited by|excited about|interested in|fascinated by|obsessed with|"
        r"focus(?:ed)? on|advance|passionate about)\s+(.+?)(?:\.|$)",
        source,
        re.IGNORECASE,
    )
    if m:
        return m.group(1).strip()

    low = source.lower()
    focus: list[str] = []
    if re.search(r"\bagentic ai\b|\bautonomous agents?\b", low):
        _append_unique(focus, "agentic AI")
    if re.search(r"\bllm infrastructure\b|\bllm infra\b|\bllm stack\b|\bplumbing of llms\b", low):
        _append_unique(focus, "LLM infrastructure")
    if focus:
        return ", ".join(focus)
    return None


def infer_roles(text: str | None, role_family: str) -> list[str]:
    """Infer role strings only inside a recognized role family."""
    low = _low(text)
    if role_family == "product":
        if not re.search(r"\b(pm|product manager|product management)\b", low):
            return []
        labels = PRODUCT_ROLE_LABELS
    elif role_family == "engineering":
        labels = ENGINEERING_ROLE_LABELS
    elif role_family == "design":
        labels = DESIGN_ROLE_LABELS
    elif role_family == "data":
        labels = DATA_ROLE_LABELS
    else:
        return []

    found = []
    for level, pattern in LEVEL_PATTERNS:
        match = re.search(pattern, low, re.IGNORECASE)
        if match and level in labels:
            found.append((match.start(), labels[level]))

    roles: list[str] = []
    for _, role in sorted(found):
        _append_unique(roles, role)
    if not roles and role_family == "product":
        _append_unique(roles, "Product Manager")
    elif not roles and role_family == "engineering":
        _append_unique(roles, "Software Engineer")
    elif not roles and role_family == "design":
        _append_unique(roles, "Product Designer")
    elif not roles and role_family == "data":
        _append_unique(roles, "Data Scientist")
    return roles


def apply_step_text_backfills(profile: dict, want_text: str, dontwant_text: str) -> dict:
    """Fill blank Step 2/3 profile fields from literal user text."""
    if not profile:
        return profile

    candidate = profile.setdefault("candidate", {})
    target = profile.setdefault("target", {})
    aspirations = profile.setdefault("aspirations", {})
    preferences = profile.setdefault("preferences", {})
    inferred = profile.setdefault("inferred", {})

    if want_text:
        role_family = target.get("role_family") or classify_role_family(want_text)
        if role_family != "unknown":
            target["role_family"] = role_family

        if not target.get("roles"):
            roles = infer_roles(want_text, role_family)
            if roles:
                target["roles"] = roles

        roles = _as_list(target.get("roles"))
        if not target.get("level"):
            level = normalize_level(want_text, roles)
            if level != "unknown":
                target["level"] = level

        if not aspirations.get("track"):
            track = normalize_track(want_text, role_family)
            if track != "unknown":
                aspirations["track"] = track

        if not aspirations.get("direction"):
            direction = normalize_direction(want_text)
            if direction:
                aspirations["direction"] = direction

        if not aspirations.get("company_types"):
            company_types = normalize_company_types(want_text)
            if company_types:
                aspirations["company_types"] = company_types

        if not preferences.get("company_stage"):
            stage = normalize_company_stage(want_text)
            if stage != "unknown":
                preferences["company_stage"] = [stage]

        if not preferences.get("company_scale"):
            scale = normalize_company_scale(want_text)
            if scale != "unknown":
                preferences["company_scale"] = scale

        if not preferences.get("team_environment"):
            team_environment = normalize_team_environment(want_text)
            if team_environment != "unknown":
                preferences["team_environment"] = team_environment
                if not preferences.get("company_size"):
                    preferences["company_size"] = team_environment

        if not candidate.get("location_preference"):
            location = normalize_location_preference(want_text)
            if location != "unknown":
                candidate["location_preference"] = location
                candidate["open_to_remote"] = location in {"remote", "flexible"}

        customer_types = _as_list(inferred.get("customer_types"))
        for customer_type in normalize_customer_types(want_text):
            _append_unique(customer_types, customer_type)
        if customer_types:
            inferred["customer_types"] = customer_types

    notes_parts = []
    if want_text:
        notes_parts.append(want_text.strip())
    if dontwant_text:
        notes_parts.append(dontwant_text.strip())
    if notes_parts:
        existing_notes = (profile.get("notes") or "").strip()
        merged_notes = existing_notes
        for note in notes_parts:
            if note and note not in merged_notes:
                merged_notes = f"{merged_notes}\n\n{note}".strip() if merged_notes else note
        profile["notes"] = merged_notes

    if dontwant_text:
        avoid_types = _as_list(aspirations.get("avoid_company_types"))
        for company_type in normalize_company_types(dontwant_text):
            _append_unique(avoid_types, company_type)
        if avoid_types:
            aspirations["avoid_company_types"] = avoid_types

    return profile
