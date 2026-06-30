"""Provider-neutral LLM client boundary for Metis.

The rest of the app should ask for text completions and usage metadata, not
provider SDK response objects. Anthropic remains the only live provider in this
first slice; the interface is intentionally small so OpenAI/Gemini/Grok can be
added without changing scoring or extraction contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMProviderError(Exception):
    """Base class for provider setup/call failures."""


class LLMTransientError(LLMProviderError):
    """Retryable provider error such as rate limit, timeout, or 5xx."""


class LLMAuthError(LLMProviderError):
    """Authentication or permission failure."""


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: LLMUsage
    raw: Any = None


class LLMClient(Protocol):
    provider: str
    is_metis_llm: bool

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        user: str,
        max_tokens: int,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        ...


def _usage_from_provider(raw_usage: Any) -> LLMUsage:
    return LLMUsage(
        input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(raw_usage, "cache_creation_input_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(raw_usage, "cache_read_input_tokens", 0) or 0),
    )


def _text_from_anthropic_response(response: Any) -> str:
    content = getattr(response, "content", []) or []
    if not content:
        return ""
    return str(getattr(content[0], "text", "") or "")


class AnthropicLLM:
    provider = "anthropic"
    is_metis_llm = True

    def __init__(self, *, api_key: str):
        if not api_key:
            raise LLMAuthError("ANTHROPIC_API_KEY is not set")
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(
        self,
        *,
        model: str,
        system: str | list[dict[str, Any]],
        user: str,
        max_tokens: int,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None:
            kwargs["temperature"] = temperature

        try:
            response = self._client.messages.create(**kwargs)
        except (
            self._anthropic.InternalServerError,
            self._anthropic.RateLimitError,
            self._anthropic.APIConnectionError,
            self._anthropic.APITimeoutError,
        ) as exc:
            raise LLMTransientError(str(exc)) from exc
        except self._anthropic.AuthenticationError as exc:
            raise LLMAuthError(str(exc)) from exc

        return LLMResponse(
            text=_text_from_anthropic_response(response),
            usage=_usage_from_provider(getattr(response, "usage", None)),
            raw=response,
        )


def complete_text(
    client: Any,
    *,
    model: str,
    system: str | list[dict[str, Any]],
    user: str,
    max_tokens: int,
    temperature: float | None = None,
    json_mode: bool = False,
) -> LLMResponse:
    """Call a Metis LLM client, with a legacy Anthropic SDK fallback for tests.

    Existing unit tests pass MagicMock Anthropic clients directly. Checking for
    the explicit marker avoids treating arbitrary mocks as provider clients.
    """
    if getattr(client, "is_metis_llm", None) is True:
        return client.complete(
            model=model,
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            json_mode=json_mode,
        )

    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if temperature is not None:
        kwargs["temperature"] = temperature
    response = client.messages.create(**kwargs)
    return LLMResponse(
        text=_text_from_anthropic_response(response),
        usage=_usage_from_provider(getattr(response, "usage", None)),
        raw=response,
    )


def create_llm_client(*, provider: str, api_key: str) -> LLMClient:
    provider_id = (provider or "anthropic").strip().lower()
    if provider_id == "anthropic":
        return AnthropicLLM(api_key=api_key)
    raise LLMProviderError(
        f"Unsupported LLM provider '{provider_id}'. "
        "This branch currently wires the provider boundary with Anthropic only."
    )

