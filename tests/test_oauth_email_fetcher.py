"""Tests for OAuth email fetcher — provider routing, token storage, email parsing."""
from __future__ import annotations

import json
import base64
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import datetime
import pytest


# ── email_fetcher provider routing ────────────────────────────────────────────

class TestGetProvider:
    def _provider_state(self, token_dir: Path):
        return patch.multiple(
            "metis.auth.state",
            DATA_DIR=token_dir,
            ACTIVE_PROVIDER_PATH=token_dir / "email_provider.json",
        )

    def test_returns_imap_when_no_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "imap"

    def test_returns_gmail_oauth_when_token_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        (token_dir / "gmail_token.json").write_text('{"access_token": "x"}')
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "gmail_oauth"

    def test_returns_outlook_oauth_when_token_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        (token_dir / "outlook_token.json").write_text('{"access_token": "x"}')
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "outlook_oauth"

    def test_active_provider_takes_priority_over_existing_tokens(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        (token_dir / "gmail_token.json").write_text('{"access_token": "x"}')
        (token_dir / "outlook_token.json").write_text('{"access_token": "y"}')
        (token_dir / "email_provider.json").write_text('{"provider": "outlook_oauth"}')
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "outlook_oauth"

    def test_newest_token_wins_without_active_provider(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        gmail = token_dir / "gmail_token.json"
        outlook = token_dir / "outlook_token.json"
        gmail.write_text('{"access_token": "x"}')
        outlook.write_text('{"access_token": "y"}')
        os.utime(gmail, (1, 1))
        os.utime(outlook, (2, 2))
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "outlook_oauth"

    def test_env_override_respected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "outlook_oauth")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "outlook_oauth"

    def test_env_override_imap_forces_imap(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "imap")
        token_dir = tmp_path / ".job_pipeline"
        token_dir.mkdir()
        (token_dir / "gmail_token.json").write_text('{"access_token": "x"}')
        with self._provider_state(token_dir):
            from metis.sources.email_fetcher import get_provider
            assert get_provider() == "imap"


class TestFetchEmailsFromSender:
    def test_routes_to_gmail_oauth(self, tmp_path, monkeypatch):
        monkeypatch.setenv("METIS_EMAIL_PROVIDER", "gmail_oauth")
        mock_fetch = MagicMock(return_value=[{"text": "hello", "html": ""}])
        with patch("metis.sources.email_fetcher.get_provider", return_value="gmail_oauth"), \
             patch("metis.auth.gmail_oauth.fetch_emails", mock_fetch):
            from metis.sources.email_fetcher import fetch_emails_from_sender
            since = datetime.datetime(2026, 1, 1)
            result = fetch_emails_from_sender("jobs@wellfound.com", since)
            mock_fetch.assert_called_once_with("jobs@wellfound.com", since)
            assert result == [{"text": "hello", "html": ""}]

    def test_routes_to_outlook_oauth(self, tmp_path, monkeypatch):
        mock_fetch = MagicMock(return_value=[{"text": "", "html": "<p>job</p>"}])
        with patch("metis.sources.email_fetcher.get_provider", return_value="outlook_oauth"), \
             patch("metis.auth.outlook_oauth.fetch_emails", mock_fetch):
            from metis.sources.email_fetcher import fetch_emails_from_sender
            since = datetime.datetime(2026, 1, 1)
            result = fetch_emails_from_sender("jobs@wellfound.com", since)
            assert result == [{"text": "", "html": "<p>job</p>"}]

    def test_falls_back_to_imap_when_no_credentials(self, monkeypatch):
        monkeypatch.delenv("GMAIL_ADDRESS", raising=False)
        monkeypatch.delenv("GMAIL_APP_PASSWORD", raising=False)
        with patch("metis.sources.email_fetcher.get_provider", return_value="imap"):
            from metis.sources.email_fetcher import fetch_emails_from_sender
            since = datetime.datetime(2026, 1, 1)
            result = fetch_emails_from_sender("jobs@wellfound.com", since)
            assert result == []


# ── gmail_oauth token storage ─────────────────────────────────────────────────

class TestGmailTokenStorage:
    def test_is_connected_false_when_no_token(self, tmp_path):
        with patch("metis.auth.gmail_oauth.TOKEN_PATH", tmp_path / "gmail_token.json"):
            from metis.auth.gmail_oauth import is_connected
            assert is_connected() is False

    def test_is_connected_true_when_token_exists(self, tmp_path):
        token_path = tmp_path / "gmail_token.json"
        token_path.write_text('{"access_token": "abc", "refresh_token": "xyz"}')
        with patch("metis.auth.gmail_oauth.TOKEN_PATH", token_path):
            from metis.auth.gmail_oauth import is_connected
            assert is_connected() is True

    def test_save_token_writes_json_and_sets_permissions(self, tmp_path):
        token_path = tmp_path / "gmail_token.json"
        token = {"access_token": "abc", "refresh_token": "xyz"}
        with patch("metis.auth.gmail_oauth.TOKEN_PATH", token_path):
            from metis.auth.gmail_oauth import _save_token, _load_token
            _save_token(token)
            assert token_path.exists()
            assert token_path.stat().st_mode & 0o777 == 0o600
            loaded = _load_token()
            assert loaded == token

    def test_load_token_returns_none_when_missing(self, tmp_path):
        with patch("metis.auth.gmail_oauth.TOKEN_PATH", tmp_path / "missing.json"):
            from metis.auth.gmail_oauth import _load_token
            assert _load_token() is None

    def test_load_token_returns_none_on_corrupt_json(self, tmp_path):
        token_path = tmp_path / "gmail_token.json"
        token_path.write_text("not valid json {{{")
        with patch("metis.auth.gmail_oauth.TOKEN_PATH", token_path):
            from metis.auth.gmail_oauth import _load_token
            assert _load_token() is None


# ── outlook_oauth token storage ───────────────────────────────────────────────

class TestOutlookTokenStorage:
    def test_is_connected_false_when_no_token(self, tmp_path):
        with patch("metis.auth.outlook_oauth.TOKEN_PATH", tmp_path / "outlook_token.json"):
            from metis.auth.outlook_oauth import is_connected
            assert is_connected() is False

    def test_is_connected_true_when_token_exists(self, tmp_path):
        token_path = tmp_path / "outlook_token.json"
        token_path.write_text('{"access_token": "abc", "refresh_token": "xyz"}')
        with patch("metis.auth.outlook_oauth.TOKEN_PATH", token_path):
            from metis.auth.outlook_oauth import is_connected
            assert is_connected() is True

    def test_save_and_load_roundtrip(self, tmp_path):
        token_path = tmp_path / "outlook_token.json"
        token = {"access_token": "t1", "refresh_token": "r1"}
        with patch("metis.auth.outlook_oauth.TOKEN_PATH", token_path):
            from metis.auth.outlook_oauth import _save_token, _load_token
            _save_token(token)
            assert token_path.stat().st_mode & 0o777 == 0o600
            assert _load_token() == token

    def test_load_token_returns_none_on_corrupt_json(self, tmp_path):
        token_path = tmp_path / "outlook_token.json"
        token_path.write_text("???")
        with patch("metis.auth.outlook_oauth.TOKEN_PATH", token_path):
            from metis.auth.outlook_oauth import _load_token
            assert _load_token() is None

    def test_scopes_include_user_read_for_me_lookup(self):
        from metis.auth.outlook_oauth import _SCOPES
        assert "User.Read" in _SCOPES

    def test_auth_flow_forces_account_selection(self):
        from metis.auth.outlook_oauth import _build_auth_url
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(_build_auth_url("client", "state-1", "challenge-1")).query)
        assert qs["prompt"] == ["select_account"]
        assert qs["state"] == ["state-1"]
        assert qs["code_challenge"] == ["challenge-1"]
        assert qs["code_challenge_method"] == ["S256"]

    def test_exchange_code_sends_pkce_verifier(self, monkeypatch, tmp_path):
        from metis.auth import outlook_oauth

        captured = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"access_token": "token", "refresh_token": "refresh"}

        monkeypatch.setattr(outlook_oauth, "TOKEN_PATH", tmp_path / "outlook_token.json")
        monkeypatch.setattr(
            outlook_oauth.requests,
            "post",
            lambda *args, **kwargs: captured.update(kwargs) or Response(),
        )

        outlook_oauth._exchange_code_for_token("code-1", "client-1", "verifier-1")

        assert captured["data"]["code_verifier"] == "verifier-1"


class TestGmailOAuthSecurity:
    def test_auth_url_includes_state_pkce_and_account_selection(self):
        from metis.auth.gmail_oauth import _build_auth_url
        from urllib.parse import parse_qs, urlparse

        qs = parse_qs(urlparse(_build_auth_url("client", "state-1", "challenge-1")).query)
        assert qs["prompt"] == ["consent select_account"]
        assert qs["state"] == ["state-1"]
        assert qs["code_challenge"] == ["challenge-1"]
        assert qs["code_challenge_method"] == ["S256"]

    def test_exchange_code_sends_pkce_verifier(self, monkeypatch, tmp_path):
        from metis.auth import gmail_oauth

        captured = {}

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"access_token": "token", "refresh_token": "refresh"}

        monkeypatch.setattr(gmail_oauth, "TOKEN_PATH", tmp_path / "gmail_token.json")
        monkeypatch.setattr(
            gmail_oauth.requests,
            "post",
            lambda *args, **kwargs: captured.update(kwargs) or Response(),
        )

        gmail_oauth._exchange_code_for_token("code-1", "client-1", "secret-1", "verifier-1")

        assert captured["data"]["code_verifier"] == "verifier-1"


# ── gmail_oauth email body extraction ─────────────────────────────────────────

class TestGmailExtractBody:
    def _encode(self, text: str) -> str:
        return base64.urlsafe_b64encode(text.encode()).decode()

    def test_extracts_plain_text_part(self):
        from metis.auth.gmail_oauth import _extract_body
        msg = {"payload": {"parts": [
            {"mimeType": "text/plain", "body": {"data": self._encode("hello plain")}},
        ]}}
        result = _extract_body(msg)
        assert result["text"] == "hello plain"
        assert result["html"] == ""

    def test_extracts_html_part(self):
        from metis.auth.gmail_oauth import _extract_body
        msg = {"payload": {"parts": [
            {"mimeType": "text/html", "body": {"data": self._encode("<b>hello</b>")}},
        ]}}
        result = _extract_body(msg)
        assert result["html"] == "<b>hello</b>"
        assert result["text"] == ""

    def test_extracts_both_parts(self):
        from metis.auth.gmail_oauth import _extract_body
        msg = {"payload": {"parts": [
            {"mimeType": "text/plain", "body": {"data": self._encode("plain")}},
            {"mimeType": "text/html",  "body": {"data": self._encode("<p>html</p>")}},
        ]}}
        result = _extract_body(msg)
        assert result["text"] == "plain"
        assert result["html"] == "<p>html</p>"

    def test_handles_nested_parts(self):
        from metis.auth.gmail_oauth import _extract_body
        msg = {"payload": {"parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": self._encode("nested plain")}},
            ]},
        ]}}
        result = _extract_body(msg)
        assert result["text"] == "nested plain"

    def test_handles_missing_parts(self):
        from metis.auth.gmail_oauth import _extract_body
        msg = {"payload": {"body": {"data": self._encode("top level")}}}
        result = _extract_body(msg)
        assert result["text"] == "top level"

    def test_handles_single_part_html_payload(self):
        from metis.auth.gmail_oauth import _extract_body
        msg = {"payload": {
            "mimeType": "text/html",
            "body": {"data": self._encode("<p>top level html</p>")},
        }}
        result = _extract_body(msg)
        assert result["text"] == ""
        assert result["html"] == "<p>top level html</p>"


class TestOutlookSendDigest:
    def test_send_digest_uses_boolean_save_to_sent_items(self, monkeypatch):
        from metis.auth import outlook_oauth

        captured = {}

        class Response:
            def raise_for_status(self):
                return None

        monkeypatch.setattr(outlook_oauth, "get_access_token", lambda: "token")
        monkeypatch.setattr(
            outlook_oauth.requests,
            "post",
            lambda *args, **kwargs: captured.update(kwargs) or Response(),
        )

        outlook_oauth.send_digest("<p>Digest</p>", "Metis", "user@example.com")

        assert captured["json"]["saveToSentItems"] is True

# ── get_access_token raises when no token ─────────────────────────────────────

class TestGetAccessToken:
    def test_raises_when_no_gmail_token(self, tmp_path):
        with patch("metis.auth.gmail_oauth.TOKEN_PATH", tmp_path / "missing.json"):
            from metis.auth.gmail_oauth import get_access_token
            with pytest.raises(RuntimeError, match="metis init"):
                get_access_token()

    def test_raises_when_no_outlook_token(self, tmp_path):
        with patch("metis.auth.outlook_oauth.TOKEN_PATH", tmp_path / "missing.json"):
            from metis.auth.outlook_oauth import get_access_token
            with pytest.raises(RuntimeError, match="metis init"):
                get_access_token()
