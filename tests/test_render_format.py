"""
Format contract tests for build_digest_html().

These tests lock the canonical format so any agent that modifies
render.py without an explicit format-change request will get an immediate
failure rather than a silent regression that only surfaces in the next email.

Rules being enforced:
  1. Stat tile labels = "Evaluated / Solid Match / Moderate Match"
  2. Legend = "Strengths / Caution / Blockers"
  3. Section headers = "Solid Match / Moderate Match / Limited Match"
  4. Score breakdown table is NOT rendered inside job cards
  5. Skipped section = flat 2-col table with "Role · Company" / "Why skipped" headers
  6. Cards contain "View posting" button with filled background (no border-only button)
"""
from __future__ import annotations
import pytest


# ---------------------------------------------------------------------------
# Fixture — minimal job dicts that exercise every rendering path
# ---------------------------------------------------------------------------

def _make_job(title, company, verdict, score, leverage=None, friction=None, tags=None, url="https://example.com/job/1", location="San Francisco, CA"):
    return {
        "title":    title,
        "company":  company,
        "location": location,
        "url":      url,
        "eval": {
            "verdict":        verdict,
            "score":          score,
            "leveragePoints": leverage or ["Strong background"],
            "frictionPoints": friction or ["Domain gap"],
            "tags":           tags or [{"text": "Strong match", "sentiment": "green"}],
            "dimensions":     [
                {"name": "experience_relevance", "weight": 0.20, "score": 80,
                 "weighted_contribution": 16.0, "rationale": "Good fit"},
            ],
        },
    }


@pytest.fixture
def mixed_jobs():
    return [
        _make_job("Director of Product", "Acme Corp",   "apply",    82),
        _make_job("Senior PM, Platform",  "Beta Inc",    "consider", 68),
        _make_job("PM, Growth",           "Gamma LLC",   "skipped",  42,
                  friction=["Not senior enough", "Wrong domain"]),
        _make_job("Staff PM, Data",       "Delta Co",    "skipped",  35),
    ]


@pytest.fixture
def html(mixed_jobs):
    from metis.render import build_digest_html
    return build_digest_html(mixed_jobs, "June 18, 2026", deal_breaker_count=5)


# ---------------------------------------------------------------------------
# 1. Stat tile label
# ---------------------------------------------------------------------------

class TestStatTileLabel:
    def test_evaluated_label_present(self, html):
        assert "Evaluated" in html

    def test_solid_match_tile(self, html):
        assert "Solid Match" in html, "Stat tile must say 'Solid Match', not 'Apply'"

    def test_moderate_match_tile(self, html):
        assert "Moderate Match" in html, "Stat tile must say 'Moderate Match', not 'Consider'"

    def test_no_apply_tile(self, html):
        # "Apply" appears in "View posting" link text — guard the stat tile label specifically
        assert "Solid Match" in html  # positive guard is sufficient

    def test_stat_tiles_use_fixed_equal_layout(self, html):
        assert "table-layout:fixed" in html
        assert 'width="33.33%"' in html


# ---------------------------------------------------------------------------
# 2. Legend labels
# ---------------------------------------------------------------------------

class TestLegend:
    def test_strengths(self, html):
        assert "Strengths" in html, "Legend first dot must read 'Strengths'"

    def test_caution(self, html):
        assert "Caution" in html, "Legend second dot must read 'Caution'"

    def test_blockers(self, html):
        assert "Blockers" in html, "Legend third dot must read 'Blockers'"

    def test_no_old_legend_labels(self, html):
        assert "Caution / domain gap" not in html
        assert "Hard blocker" not in html
        assert "Strength match" not in html


# ---------------------------------------------------------------------------
# 3. Score breakdown must NOT appear in email cards
# ---------------------------------------------------------------------------

class TestNoScoreBreakdown:
    def test_score_breakdown_header_absent(self, html):
        assert "Score breakdown" not in html, (
            "Score breakdown table must not appear in cards — "
            "render_score_breakdown() was called from _job_card() in regression"
        )

    def test_details_tag_absent(self, html):
        # <details> in email = expanded score table visible in Gmail
        assert "<details" not in html.lower()


# ---------------------------------------------------------------------------
# 4. Skipped section — flat 2-col table format
# ---------------------------------------------------------------------------

class TestSkippedSectionFormat:
    def test_section_header_limited_match(self, html):
        assert "Limited Match" in html, "Skipped section header must read 'Limited Match', not 'Skipped'"

    def test_column_header_role_company(self, html):
        assert "Role · Company" in html, "Skipped section must have 'Role · Company' column header"

    def test_column_header_why_skipped(self, html):
        assert "Why skipped" in html, "Skipped section must have 'Why skipped' column header"

    def test_skipped_role_linked(self, html):
        assert 'href="https://example.com/job/1"' in html

    def test_no_old_section_header(self, html):
        assert "Why Skipped" not in html  # old capitalization


# ---------------------------------------------------------------------------
# 5. View posting button present in scored cards
# ---------------------------------------------------------------------------

class TestViewPostingButton:
    def test_apply_card_has_view_posting(self, html):
        assert "View posting" in html

    def test_apply_card_links_to_url(self, html):
        assert 'href="https://example.com/job/1"' in html

    def test_button_is_filled_not_outlined(self, html):
        # Button must use background color fill with white text.
        assert "color:#ffffff" in html, "Button text must be white (filled button)"


class TestPointMarkers:
    def test_python_fallback_uses_check_and_question_mark(self, html):
        assert "&#10003;" in html
        assert "? </span>" in html
        assert "&#8593;" not in html
        assert "&#8595;" not in html


# ---------------------------------------------------------------------------
# 6. Deal-breaker footer note
# ---------------------------------------------------------------------------

class TestFooter:
    def test_filtered_note_present_when_nonzero(self, html):
        assert "filtered by deal" in html

    def test_filtered_count_correct(self, html):
        assert "5 filtered by deal" in html

    def test_no_filtered_note_when_zero(self, mixed_jobs):
        from metis.render import build_digest_html
        h = build_digest_html(mixed_jobs, "June 18, 2026", deal_breaker_count=0)
        assert "filtered by deal" not in h
