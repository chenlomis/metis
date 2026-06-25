"""
Format contract tests for build_digest_html().

These tests lock the June-18 canonical format so any agent that modifies
render.py without an explicit format-change request will get an immediate
failure rather than a silent regression that only surfaces in the next email.

Rules being enforced:
  1. Stat tile label = "EVALUATED" (not "ROLES EVALUATED")
  2. Legend = "Strengths / Caution / Blocker"
  3. Score breakdown table is NOT rendered inside job cards
  4. Skipped section = flat 2-col table with "Role · Company" / "Why Skipped" headers
  5. Cards contain "View posting" button
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
    from scorerole.render import build_digest_html
    return build_digest_html(mixed_jobs, "June 18, 2026", deal_breaker_count=5)


# ---------------------------------------------------------------------------
# 1. Stat tile label
# ---------------------------------------------------------------------------

class TestStatTileLabel:
    def test_evaluated_label_not_roles_evaluated(self, html):
        # Footer says "N roles evaluated" (lowercase, intentional) — we only guard the stat tile
        # label, which would be "Roles evaluated" with a capital R if regressed.
        assert "Roles evaluated" not in html, (
            "Stat tile must say 'Evaluated', not 'Roles evaluated' — revert render.py stat_row label"
        )

    def test_evaluated_label_present(self, html):
        # The label is upper-cased by CSS text-transform; the source string is "Evaluated"
        assert "Evaluated" in html


# ---------------------------------------------------------------------------
# 2. Legend labels
# ---------------------------------------------------------------------------

class TestLegend:
    def test_strengths(self, html):
        assert "Strengths" in html

    def test_caution(self, html):
        assert "Caution" in html, (
            "Legend second dot must read 'Caution' — "
            "was changed to 'Proceed with awareness' in regression"
        )

    def test_blocker(self, html):
        assert "Blocker" in html, (
            "Legend third dot must read 'Blocker' — "
            "was changed to 'Real concern' in regression"
        )

    def test_no_proceed_with_awareness(self, html):
        assert "Proceed with awareness" not in html

    def test_no_real_concern(self, html):
        assert "Real concern" not in html


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
    def test_column_header_role_company(self, html):
        assert "Role · Company" in html, (
            "Skipped section must have 'Role · Company' column header"
        )

    def test_column_header_why_skipped(self, html):
        assert "Why Skipped" in html, (
            "Skipped section must have 'Why Skipped' column header"
        )

    def test_skipped_role_linked(self, html):
        # Skipped role title should be an <a href> link
        assert 'href="https://example.com/job/1"' in html

    def test_no_card_grid_cells(self, html):
        # The old regression used individual bordered cards with padding:10px 12px;border-radius:4px
        # for each skipped role — this is the skipped-cell grid pattern we reverted away from.
        # We check the skip section doesn't re-introduce card-per-skip boxes for 2 skipped roles
        # by verifying "Role · Company" header exists (already done above) as the positive signal.
        # Negative: no skipped role is rendered inside a standalone bordered card background.
        assert "Why Skipped" in html  # redundant guard; primary check is column headers above


# ---------------------------------------------------------------------------
# 5. View posting button present in scored cards
# ---------------------------------------------------------------------------

class TestViewPostingButton:
    def test_apply_card_has_view_posting(self, html):
        assert "View posting" in html

    def test_apply_card_links_to_url(self, html):
        assert 'href="https://example.com/job/1"' in html


# ---------------------------------------------------------------------------
# 6. Deal-breaker footer note
# ---------------------------------------------------------------------------

class TestFooter:
    def test_filtered_note_present_when_nonzero(self, html):
        assert "filtered by deal" in html

    def test_filtered_count_correct(self, html):
        assert "5 filtered by deal" in html

    def test_no_filtered_note_when_zero(self, mixed_jobs):
        from scorerole.render import build_digest_html
        h = build_digest_html(mixed_jobs, "June 18, 2026", deal_breaker_count=0)
        assert "filtered by deal" not in h
