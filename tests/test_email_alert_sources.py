from __future__ import annotations

import datetime


def test_ladders_parser_keeps_tracking_urls_by_row_order():
    from metis.sources.email_alerts import _parse_ladders

    text = (
        "Jobs That Fit You\n"
        "Senior Product Manager, Lodging Connectivity / Seattle, WA / $173K - $277K* \n"
        "Lead Product Manager - AI Assistant / Virtual / Travel / $154K - $199K* Remote\n"
        "Product Manager/Sr. Product Manager - Developer Experience & Platform, Mulesoft\n"
        "/ Seattle, WA / $148K - $260K*\n"
    )
    html = """
    <html><body>
      <a href="https://t.ladders.co/f/a/first">Senior Product Manager, Lodging Connectivity</a>
      <a href="https://t.ladders.co/f/a/second">Lead Product Manager - AI Assistant</a>
      <a href="https://t.ladders.co/f/a/third">Product Manager/Sr. Product Manager - Developer Experience &amp; Platform, Mulesoft</a>
    </body></html>
    """

    jobs = _parse_ladders(text, html, "Ladders")

    assert [j["title"] for j in jobs] == [
        "Senior Product Manager, Lodging Connectivity",
        "Lead Product Manager - AI Assistant",
        "Product Manager/Sr. Product Manager - Developer Experience & Platform, Mulesoft",
    ]
    assert jobs[0]["url"] == "https://t.ladders.co/f/a/first"
    assert jobs[1]["url"] == "https://t.ladders.co/f/a/second"
    assert jobs[2]["location"] == "Seattle, WA"
    assert jobs[2]["url"] == "https://t.ladders.co/f/a/third"


def test_fetch_jd_rejects_blocked_pages(monkeypatch):
    from metis.sources import email_alerts

    class Response:
        status_code = 403
        text = "<html><title>Attention Required</title><body>Cloudflare challenge</body></html>"
        headers = {"cf-mitigated": "challenge"}

    monkeypatch.setattr(email_alerts.requests, "get", lambda *args, **kwargs: Response())

    assert email_alerts._fetch_jd("https://example.com/job") == ""


def test_fetch_email_alerts_falls_back_to_synthetic_jd(monkeypatch):
    from metis.sources import email_alerts

    emails = [{
        "text": "Senior Product Manager / Seattle, WA / $173K - $277K* Remote",
        "html": '<a href="https://t.ladders.co/f/a/first">Senior Product Manager</a>',
    }]

    monkeypatch.setattr(email_alerts, "_fetch_jd", lambda _url: "")
    monkeypatch.setattr(
        "metis.sources.email_fetcher.fetch_emails_from_sender",
        lambda sender, since_dt: emails,
    )

    jobs = email_alerts.fetch_email_alerts(
        datetime.datetime(2026, 6, 30),
        [{"company": "Ladders", "sender": "jobs@my.theladders.com", "format": "ladders"}],
    )

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Product Manager"
    assert jobs[0]["company"] == "Ladders"
    assert jobs[0]["url"] == "https://t.ladders.co/f/a/first"
    assert "Senior Product Manager" in jobs[0]["jd"]
    assert "$173K - $277K" in jobs[0]["jd"]
