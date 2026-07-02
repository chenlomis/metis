import sys
import types
from unittest.mock import MagicMock

import pytest

from metis.llm import (
    LLMProviderError,
    LLMResponse,
    LLMUsage,
    complete_text,
    create_llm_client,
    normalize_provider,
    provider_api_key_env,
    resolve_stage_models,
)


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
    with pytest.raises(LLMProviderError, match="Unsupported LLM provider"):
        create_llm_client(provider="gemini", api_key="test")


@pytest.mark.parametrize("raw", ["openAI", "open_ai", "open-AI", "OPEN AI", "oai", "chatgpt"])
def test_normalize_provider_accepts_openai_aliases(raw):
    assert normalize_provider(raw) == "openai"


@pytest.mark.parametrize("raw", ["Anthropic", "anthROPIC", "Claude"])
def test_normalize_provider_accepts_anthropic_aliases(raw):
    assert normalize_provider(raw) == "anthropic"


def test_normalize_provider_rejects_model_names_with_hint():
    with pytest.raises(LLMProviderError) as excinfo:
        normalize_provider("anthropicHAIKU")

    message = str(excinfo.value)
    assert "Unsupported LLM provider" in message
    assert "PRESCREEN_MODEL/MODEL" in message


def test_provider_api_key_env_uses_normalized_provider():
    assert provider_api_key_env("open_ai") == "OPENAI_API_KEY"
    assert provider_api_key_env("Claude") == "ANTHROPIC_API_KEY"


def test_resolve_stage_models_ignores_other_provider_builtin_defaults():
    env = {
        "MODEL": "claude-sonnet-4-6",
        "PRESCREEN_MODEL": "claude-haiku-4-5",
        "EXTRACT_MODEL": "claude-haiku-4-5",
    }

    assert resolve_stage_models("openAI", env=env) == {
        "model": "gpt-4.1",
        "prescreen_model": "gpt-4.1-mini",
        "extract_model": "gpt-4.1-mini",
    }


def test_resolve_stage_models_prefers_provider_specific_values():
    env = {
        "MODEL": "claude-sonnet-4-6",
        "OPENAI_MODEL": "gpt-custom",
        "OPENAI_PRESCREEN_MODEL": "gpt-fast",
        "OPENAI_EXTRACT_MODEL": "gpt-extract",
    }

    assert resolve_stage_models("openai", env=env) == {
        "model": "gpt-custom",
        "prescreen_model": "gpt-fast",
        "extract_model": "gpt-extract",
    }
