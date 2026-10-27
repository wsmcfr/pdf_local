from __future__ import annotations

import json
import os
from typing import AsyncIterator

import httpx

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


class OllamaError(Exception):
    """Base error for local Ollama failures."""


class OllamaUnavailable(OllamaError):
    """Ollama is not running or cannot be reached."""


def _base_url() -> str:
    return os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


async def list_models() -> list[str]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{_base_url()}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [model["name"] for model in data.get("models", [])]
    except (httpx.HTTPError, OSError) as exc:
        raise OllamaUnavailable(str(exc)) from exc


async def stream_chat(model: str, messages: list[dict]) -> AsyncIterator[str]:
    """Yield content tokens from Ollama's streaming chat endpoint."""
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST",
                f"{_base_url()}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "options": {"temperature": 0.1, "num_ctx": 8192},
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    content = data.get("message", {}).get("content")
                    if content:
                        yield content
    except (httpx.HTTPError, OSError) as exc:
        raise OllamaUnavailable(str(exc)) from exc
