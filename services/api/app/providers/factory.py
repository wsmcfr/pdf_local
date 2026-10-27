"""Provider factory: turn a ``ProviderConfig`` into a concrete adapter."""

from __future__ import annotations

import httpx

from .anthropic import AnthropicProvider
from .base import Provider, ProviderConfig
from .custom import CustomProvider
from .errors import ProviderConfigError
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatibleProvider
from .openai_responses import OpenAIResponsesProvider


def create_provider(
    config: ProviderConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = 60.0,
) -> Provider:
    kind = (config.kind or "").strip()
    if kind == "openai_chat":
        return OpenAICompatibleProvider(
            config.base_url,
            config.api_key,
            config.model,
            transport=transport,
            timeout=timeout,
        )
    if kind == "openai_responses":
        return OpenAIResponsesProvider(
            config.base_url,
            config.api_key,
            config.model,
            transport=transport,
            timeout=timeout,
        )
    if kind == "anthropic":
        return AnthropicProvider(
            config.base_url,
            config.api_key,
            config.model,
            transport=transport,
            timeout=timeout,
        )
    if kind == "gemini":
        return GeminiProvider(
            config.base_url,
            config.api_key,
            config.model,
            transport=transport,
            timeout=timeout,
        )
    if kind == "custom":
        return CustomProvider(
            config.base_url,
            config.api_key,
            config.model,
            extra=config.extra,
            transport=transport,
            timeout=timeout,
        )
    raise ProviderConfigError(f"unsupported provider kind: {kind or '<empty>'}")
