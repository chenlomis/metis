from __future__ import annotations

import re

import pytest


def test_generate_pkce_pair_shape_and_s256_challenge():
    from metis.auth.oauth_security import generate_pkce_pair

    verifier, challenge = generate_pkce_pair()

    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)
    assert len(challenge) == 43
    assert re.fullmatch(r"[A-Za-z0-9_-]+", challenge)
    assert "=" not in challenge


def test_extract_callback_code_accepts_matching_state():
    from metis.auth.oauth_security import extract_callback_code

    assert extract_callback_code({"state": ["abc"], "code": ["code-1"]}, "abc") == "code-1"


def test_extract_callback_code_rejects_missing_state():
    from metis.auth.oauth_security import extract_callback_code

    with pytest.raises(RuntimeError, match="state mismatch"):
        extract_callback_code({"code": ["code-1"]}, "abc")


def test_extract_callback_code_rejects_mismatched_state():
    from metis.auth.oauth_security import extract_callback_code

    with pytest.raises(RuntimeError, match="state mismatch"):
        extract_callback_code({"state": ["wrong"], "code": ["code-1"]}, "abc")


def test_extract_callback_code_reports_denied_consent():
    from metis.auth.oauth_security import extract_callback_code

    with pytest.raises(RuntimeError, match="access_denied"):
        extract_callback_code(
            {
                "state": ["abc"],
                "error": ["access_denied"],
                "error_description": ["user denied consent"],
            },
            "abc",
        )
