from __future__ import annotations

from unittest.mock import patch


def test_ask_yes_no_accepts_yes_text():
    from metis.prompt_utils import ask_yes_no

    with patch("sys.stdin.isatty", return_value=True), \
         patch("metis.prompt_utils._read_prompt_line", return_value="YES\n"):
        assert ask_yes_no("Continue?", default=False) is True


def test_ask_yes_no_retries_after_invalid_text():
    from metis.prompt_utils import ask_yes_no

    with patch("sys.stdin.isatty", return_value=True), \
         patch("metis.prompt_utils._read_prompt_line", side_effect=["maybe\n", "no\n"]):
        assert ask_yes_no("Continue?", default=True) is False


def test_ask_yes_no_blank_uses_default():
    from metis.prompt_utils import ask_yes_no

    with patch("sys.stdin.isatty", return_value=True), \
         patch("metis.prompt_utils._read_prompt_line", return_value="\n"):
        assert ask_yes_no("Continue?", default=True) is True
