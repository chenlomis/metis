"""scorerole/types.py — shared TypedDicts for the eval pipeline.

Single source of truth for the dict shapes that flow between score.py,
extract.py, render.py, trace.py, and feedback.py.

score.py emits EvalResult inside each job dict under the "eval" key.
render.py consumes it. Both import from here — mismatches become type
errors at development time, not silent rendering failures at runtime.
"""
from __future__ import annotations

from typing import List, Literal, Optional
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Scoring dimension (one of six per scored role)
# ---------------------------------------------------------------------------

class Dimension(TypedDict):
    name: str           # e.g. "seniority_scope", "experience_relevance"
    score: int          # 0–100
    rationale: str      # ≤12 words, specific evidence from JD vs profile
    weight: float       # scoring weight (0.0–1.0), assigned by rank_jobs()
    weighted_contribution: float  # score * weight, assigned by rank_jobs()


# ---------------------------------------------------------------------------
# Tag (rendered as a pill on the digest card)
# ---------------------------------------------------------------------------

class Tag(TypedDict):
    text: str
    sentiment: Literal["green", "amber", "red"]


# ---------------------------------------------------------------------------
# EvalResult — emitted by score.py, consumed by render.py and trace.py
# ---------------------------------------------------------------------------

class EvalResult(TypedDict):
    score: int                                              # 0–100 weighted total
    verdict: Literal["apply", "consider", "skipped",       # scored verdicts
                     "filtered",                           # hard gate (deal-breaker / extract gate)
                     "prescreened"]                        # Haiku pre-screen filtered
    dimensions: List[Dimension]                            # exactly 6 for scored; [] for filtered
    leveragePoints: List[str]                              # exactly 2 for scored; [] for filtered
    frictionPoints: List[str]                              # exactly 1 for scored; [] for filtered
    tags: List[Tag]                                        # rendered pills
    gate: Optional[str]                                    # gate name for filtered roles (e.g. "jd_blank")
    summary: Optional[str]                                 # optional free-text rationale


# ---------------------------------------------------------------------------
# Job dict — the envelope that flows through the full pipeline
#
# Not exhaustive — just the fields that cross module boundaries.
# Source fields (title, company, url, jd, source) are set by sources/*.py.
# Extraction fields live under job["extraction"] (see extract.py schema).
# Eval fields live under job["eval"] (EvalResult above).
# ---------------------------------------------------------------------------

class JobDict(TypedDict, total=False):
    title: str
    company: str
    location: str
    job_id: str
    url: str
    apply_url: str
    jd: str
    source: str                     # "linkedin" | "proactive"
    extraction: dict                # 27-field Haiku extraction output
    eval: EvalResult                # scoring output
    alumni_count: Optional[int]     # injected by proactive source if available
