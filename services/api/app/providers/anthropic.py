"""Anthropic Claude Messages API provider adapter (``POST /messages``).

Differences from the OpenAI shape:

* credentials go in ``x-api-key`` plus a required ``anthropic-version`` header;
* system prompts are a single top-level ``system`` string, not a message;
* ``max_tokens`` is mandatory (Claude rejects requests without it);
* streaming is event-based (``content_block_delta`` / ``message_stop``) and
  token usage is split across ``message_start`` (input) and ``message_delta``
  (output).

Anthropic has no embeddings endpoint, so :meth:`embed` always returns ``None``.
Model listing uses ``GET /models``; when the account cannot list models (404)
an empty list is returned so the UI can fall back to manual model entry.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator

import httpx

from .base import ChatChunk, ChatRequest, ModelInfo
from .errors import ProviderError, ProviderTimeoutError
from .openai_compat import OpenAICompatibleProvider

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 1024


class AnthropicProvider(OpenAICompatibleProvider):
    kind = "anthropic"

    # ------------------------------------------------------------------
    # Request mapping
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {
            "content-type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _stream_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["accept"] = "text/event-stream"
        return headers

    def _chat_body(self, request: ChatRequest) -> dict:
        system_parts: list[str] = []
        messages: list[dict] = []
        for message in request.messages:
            role = (message.role or "").strip().lower()
            if role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            messages.append({"role": message.role, "content": message.content})

        body: dict = {
            "model": request.model or self.model,
            "messages": messages,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "stream": bool(request.stream),
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        body.update(request.extra or {})
        return body

    # ------------------------------------------------------------------
    # Provider protocol
    # ------------------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        url = f"{self.base_url}/models"
        try:
            async with self._client() as client:
                response = await client.get(url, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        # Not every Anthropic account/version can enumerate models; let the
        # caller fall back to the manually configured model instead of failing.
        if response.status_code == 404:
            return []

        self._raise_for_status(response.status_code)
        payload = self._parse_json(response)

        models: list[ModelInfo] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not model_id:
                continue
            name = item.get("display_name") or item.get("name")
            models.append(
                ModelInfo(id=str(model_id), name=str(name) if name else None)
            )
        return models

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        url = f"{self.base_url}/messages"
        body = self._chat_body(request)

        if not request.stream:
            payload = await self._post_json(url, body)
            for chunk in self._non_stream_chunks(payload):
                yield chunk
            return

        input_tokens: int | None = None
        output_tokens: int | None = None
        finished = False

        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", url, json=body, headers=self._stream_headers()
                ) as response:
                    self._raise_for_status(response.status_code)
                    event_name: str | None = None
                    async for raw in response.aiter_lines():
                        line = raw.strip()
                        if not line:
                            event_name = None
                            continue
                        if line.startswith("event:"):
                            event_name = line[len("event:") :].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if not data:
                            continue
                        try:
                            payload = json.loads(data)
                        except ValueError:
                            event_name = None
                            continue
                        if not isinstance(payload, dict):
                            event_name = None
                            continue

                        event_type = event_name or str(payload.get("type") or "")
                        event_name = None

                        if event_type == "content_block_delta":
                            delta = payload.get("delta")
                            text = (
                                delta.get("text") if isinstance(delta, dict) else None
                            )
                            if isinstance(text, str) and text:
                                yield ChatChunk(type="text", text=text)
                            continue

                        if event_type == "message_start":
                            message = payload.get("message")
                            usage = (
                                message.get("usage")
                                if isinstance(message, dict)
                                else None
                            )
                            if isinstance(usage, dict):
                                value = usage.get("input_tokens")
                                if isinstance(value, int):
                                    input_tokens = value
                            continue

                        if event_type == "message_delta":
                            usage = payload.get("usage")
                            if isinstance(usage, dict):
                                value = usage.get("output_tokens")
                                if isinstance(value, int):
                                    output_tokens = value
                            continue

                        if event_type == "message_stop":
                            usage = self._usage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            )
                            if usage:
                                yield ChatChunk(type="usage", usage=usage)
                            yield ChatChunk(type="done")
                            finished = True
                            break

                        if event_type == "error":
                            yield ChatChunk(
                                type="error", error=self._error_message(payload)
                            )
                            yield ChatChunk(type="done")
                            finished = True
                            break

                        # content_block_start/stop, ping, ... are ignored.
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        if not finished:
            yield ChatChunk(type="done")

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        # Anthropic exposes no embeddings API.
        return None

    # ------------------------------------------------------------------
    # Response parsing helpers
    # ------------------------------------------------------------------
    def _non_stream_chunks(self, payload: dict) -> Iterator[ChatChunk]:
        texts: list[str] = []
        content = payload.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "text":
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    texts.append(text)
        if texts:
            yield ChatChunk(type="text", text="".join(texts))

        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            yield ChatChunk(type="usage", usage=usage)
        yield ChatChunk(type="done")

    @staticmethod
    def _usage(input_tokens: int | None, output_tokens: int | None) -> dict | None:
        usage: dict = {}
        if input_tokens is not None:
            usage["input_tokens"] = input_tokens
        if output_tokens is not None:
            usage["output_tokens"] = output_tokens
        return usage or None

    @staticmethod
    def _error_message(payload: dict) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return str(message)
        elif isinstance(error, str) and error:
            return error
        return "provider stream error"
