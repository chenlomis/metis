"""Provider-neutral LLM client boundary for Metis.

The rest of the app should ask for text completions and usage metadata, not
provider SDK response objects. Anthropic is the default provider, and OpenAI is
supported across public AI tasks. The interface is intentionally small so
Gemini/Grok can be added without changing scoring or extraction contracts.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Protocol


class LLMProviderError(Exception):
    """Base class for provider setup/call failures."""


class LLMTransientError(LLMProviderError):
    """Retryable provider error such as rate limit, timeout, or 5xx."""


class LLMAuthError(LLMProviderError):
    """Authentication or permission failure."""


_PROVIDER_ALIASES = {
    "anthropic": "anthropic",
    "claude": "anthropic",
    "openai": "openai",
    "oai": "openai",
    "chatgpt": "openai",
}

DEFAULT_MODELS = {
    "anthropic": {
        "model": "claude-sonnet-4-6",
        "prescreen_model": "claude-haiku-4-5",
        "extract_model": "claude-haiku-4-5",
    },
    "openai": {
        "model": "gpt-4.1",
        "prescreen_model": "gpt-4.1-mini",
        "extract_model": "gpt-4.1-mini",
    },
}


def _llm_timeout_seconds() -> float:
    raw = os.getenv("METIS_LLM_TIMEOUT_SECONDS", "120").strip()
    try:
        return max(10.0, float(raw))
    except ValueError:
        return 120.0

_STAGE_ENV = {
    "model": "MODEL",
    "prescreen_model": "PRESCREEN_MODEL",
    "extract_model": "EXTRACT_MODEL",
}


def normalize_provider(provider: str | None) -> str:
    """Normalize user-facing provider names and aliases.

    Accepts common casing/separator variants such as "OpenAI", "open_ai",
    "open-ai", "Anthropic", and "Claude".
    """
    raw = (provider or "anthropic").strip()
    key = "".join(ch for ch in raw.lower() if ch.isalnum())
    provider_id = _PROVIDER_ALIASES.get(key)
    if provider_id:
        return provider_id
    supported = ", ".join(sorted(DEFAULT_MODELS))
    raise LLMProviderError(
        f"Unsupported LLM provider '{raw}'. Supported values: {supported}. "
        "Use METIS_LLM_PROVIDER=anthropic or METIS_LLM_PROVIDER=openai. "
        "Model families such as Haiku/Sonnet belong in PRESCREEN_MODEL/MODEL, not the provider field."
    )


def provider_api_key_env(provider: str) -> str:
    provider_id = normalize_provider(provider)
    return "OPENAI_API_KEY" if provider_id == "openai" else "ANTHROPIC_API_KEY"


def resolve_stage_models(provider: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve per-stage model names with provider-aware defaults.

    Backward-compatible generic vars (`MODEL`, `PRESCREEN_MODEL`, `EXTRACT_MODEL`)
    still work. Provider-specific vars (`ANTHROPIC_MODEL`, `OPENAI_MODEL`, etc.)
    win when present. If a generic var is just another provider's built-in default,
    it is ignored so switching providers does not accidentally pass Claude model
    names to OpenAI or vice versa.
    """
    env_map = env if env is not None else os.environ
    provider_id = normalize_provider(provider)
    defaults = DEFAULT_MODELS[provider_id]
    all_builtin_defaults = {
        value
        for provider_defaults in DEFAULT_MODELS.values()
        for value in provider_defaults.values()
    }

    resolved: dict[str, str] = {}
    prefix = provider_id.upper()
    for stage, generic_env in _STAGE_ENV.items():
        provider_env = f"{prefix}_{generic_env}"
        provider_value = (env_map.get(provider_env) or "").strip()
        generic_value = (env_map.get(generic_env) or "").strip()
        if provider_value:
            resolved[stage] = provider_value
        elif generic_value and (
            generic_value == defaults[stage] or generic_value not in all_builtin_defaults
        ):
            resolved[stage] = generic_value
        else:
            resolved[stage] = defaults[stage]
    return resolved


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


def _system_to_text(system: str | list[dict[str, Any]]) -> str:
    if isinstance(system, str):
        return system
    parts: list[str] = []
    for block in system:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n\n".join(p for p in parts if p)


def _openai_usage(raw_usage: Any) -> LLMUsage:
    return LLMUsage(
        input_tokens=int(getattr(raw_usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(raw_usage, "output_tokens", 0) or 0),
    )


def _text_from_openai_response(response: Any) -> str:
    output_text = getattr(response, "output_text", None)
    if output_text is not None:
        return str(output_text)

    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", None)
            if text is not None:
                chunks.append(str(text))
    return "".join(chunks)


class AnthropicLLM:
    provider = "anthropic"
    is_metis_llm = True

    def __init__(self, *, api_key: str):
        if not api_key:
            raise LLMAuthError("ANTHROPIC_API_KEY is not set")
        import anthropic

        self._anthropic = anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=_llm_timeout_seconds())

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


class OpenAILLM:
    provider = "openai"
    is_metis_llm = True

    def __init__(self, *, api_key: str):
        if not api_key:
            raise LLMAuthError("OPENAI_API_KEY is not set")
        try:
            import openai
            from openai import OpenAI
        except ImportError as exc:
            raise LLMProviderError(
                "openai package is not installed. Install project dependencies after adding OpenAI support."
            ) from exc

        self._openai = openai
        try:
            self._client = OpenAI(api_key=api_key, timeout=_llm_timeout_seconds())
        except TypeError:
            self._client = OpenAI(api_key=api_key)

    def _retryable_errors(self) -> tuple[type[BaseException], ...]:
        return tuple(
            err
            for name in ("InternalServerError", "RateLimitError", "APIConnectionError", "APITimeoutError")
            if isinstance((err := getattr(self._openai, name, None)), type)
        )

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
            "instructions": _system_to_text(system),
            "input": user,
            "max_output_tokens": max_tokens,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if json_mode:
            kwargs["text"] = {"format": {"type": "json_object"}}

        try:
            response = self._client.responses.create(**kwargs)
        except self._retryable_errors() as exc:
            raise LLMTransientError(str(exc)) from exc
        except getattr(self._openai, "AuthenticationError", LLMAuthError) as exc:
            raise LLMAuthError(str(exc)) from exc

        return LLMResponse(
            text=_text_from_openai_response(response),
            usage=_openai_usage(getattr(response, "usage", None)),
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
    provider_id = normalize_provider(provider)
    if provider_id == "anthropic":
        return AnthropicLLM(api_key=api_key)
    if provider_id == "openai":
        return OpenAILLM(api_key=api_key)
    raise LLMProviderError(f"Unsupported LLM provider '{provider_id}'.")
