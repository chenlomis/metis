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


def test_create_llm_client_rejects_unknown_provider():
    try:
        create_llm_client(provider="openai", api_key="sk-test")
    except Exception as exc:
        assert "Unsupported LLM provider" in str(exc)
    else:
        raise AssertionError("expected unsupported provider error")
