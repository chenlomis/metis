import sys
import types
from unittest.mock import MagicMock

from metis.llm import LLMResponse, LLMUsage, complete_text, create_llm_client


class FakeMetisLLM:
    provider = "fake"
    is_metis_llm = True

    def __init__(self):
        self.calls = []

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return LLMResponse(text='{"ok": true}', usage=LLMUsage(input_tokens=3, output_tokens=4))


def test_complete_text_uses_metis_client_interface():
    client = FakeMetisLLM()

    response = complete_text(
        client,
        model="fake-model",
        system="system",
        user="user",
        max_tokens=32,
        temperature=0,
        json_mode=True,
    )

    assert response.text == '{"ok": true}'
    assert response.usage.input_tokens == 3
    assert client.calls[0]["json_mode"] is True


def test_complete_text_keeps_legacy_anthropic_client_shape():
    client = MagicMock()
    raw = MagicMock()
    raw.content = [MagicMock(text="hello")]
    raw.usage = MagicMock(input_tokens=10, output_tokens=5)
    client.messages.create.return_value = raw

    response = complete_text(
        client,
        model="claude-haiku-4-5",
        system="system",
        user="user",
        max_tokens=32,
    )

    client.messages.create.assert_called_once()
    assert response.text == "hello"
    assert response.usage.output_tokens == 5


def test_create_llm_client_supports_openai_with_mock_sdk(monkeypatch):
    class FakeResponses:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            response = MagicMock()
            response.output_text = '{"ok": true}'
            response.usage = MagicMock(input_tokens=11, output_tokens=7)
            return response

    class FakeOpenAI:
        last_instance = None

        def __init__(self, *, api_key):
            self.api_key = api_key
            self.responses = FakeResponses()
            FakeOpenAI.last_instance = self

    fake_openai = types.ModuleType("openai")
    fake_openai.OpenAI = FakeOpenAI
    fake_openai.InternalServerError = RuntimeError
    fake_openai.RateLimitError = RuntimeError
    fake_openai.APIConnectionError = RuntimeError
    fake_openai.APITimeoutError = RuntimeError
    fake_openai.AuthenticationError = PermissionError
    monkeypatch.setitem(sys.modules, "openai", fake_openai)

    client = create_llm_client(provider="openai", api_key="sk-test")
    response = client.complete(
        model="gpt-4.1-mini",
        system=[{"type": "text", "text": "system"}],
        user="Return JSON.",
        max_tokens=64,
        temperature=0,
        json_mode=True,
    )

    assert response.text == '{"ok": true}'
    assert response.usage.input_tokens == 11
    call = FakeOpenAI.last_instance.responses.calls[0]
    assert call["instructions"] == "system"
    assert call["input"] == "Return JSON."
    assert call["text"] == {"format": {"type": "json_object"}}


def test_create_llm_client_rejects_unknown_provider():
    try:
        create_llm_client(provider="gemini", api_key="test")
    except Exception as exc:
        assert "Unsupported LLM provider" in str(exc)
    else:
        raise AssertionError("expected unsupported provider error")
