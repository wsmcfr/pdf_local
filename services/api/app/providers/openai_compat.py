"""OpenAI Chat Completions compatible provider.

Works with any server exposing the OpenAI v1 API shape (OpenAI, DeepSeek,
Moonshot, vLLM, LM Studio, ...). The HTTP transport is injectable so tests can
use ``httpx.MockTransport`` and never touch the network.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator

import httpx

from .base import ChatChunk, ChatRequest, ModelInfo
from .errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)

DEFAULT_TIMEOUT = 60.0


class OpenAICompatibleProvider:
    kind = "openai_chat"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str = "",
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        base_url = (base_url or "").strip()
        if not base_url:
            raise ProviderConfigError("base_url is required")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or ""
        self.model = model
        self._transport = transport
        self._timeout = timeout

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _stream_headers(self) -> dict[str, str]:
        """Ask for SSE explicitly so gateways do not buffer JSON responses."""
        headers = self._headers()
        headers["Accept"] = "text/event-stream"
        return headers

    @staticmethod
    def _raise_for_status(status: int) -> None:
        if status < 400:
            return
        if status in (401, 403):
            raise ProviderAuthError(f"authentication failed (HTTP {status})")
        if status == 404:
            raise ProviderNotFoundError("provider endpoint not found (HTTP 404)")
        if status == 429:
            raise ProviderRateLimitError("provider rate limit exceeded (HTTP 429)")
        raise ProviderError(f"provider returned HTTP {status}")

    @staticmethod
    def _parse_json(response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("provider returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("provider returned an unexpected payload")
        return payload

    async def _post_json(self, url: str, body: dict) -> dict:
        try:
            async with self._client() as client:
                response = await client.post(url, json=body, headers=self._headers())
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        self._raise_for_status(response.status_code)
        return self._parse_json(response)

    def _chat_body(self, request: ChatRequest) -> dict:
        body: dict = {
            "model": request.model or self.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in request.messages
            ],
            "stream": bool(request.stream),
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_tokens"] = request.max_tokens
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

        self._raise_for_status(response.status_code)
        payload = self._parse_json(response)

        models: list[ModelInfo] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if not model_id:
                continue
            name = item.get("name")
            models.append(
                ModelInfo(id=str(model_id), name=str(name) if name else None)
            )
        return models

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        url = f"{self.base_url}/chat/completions"
        body = self._chat_body(request)

        if not request.stream:
            payload = await self._post_json(url, body)
            for chunk in self._non_stream_chunks(payload):
                yield chunk
            return

        try:
            async with self._client() as client:
                async with client.stream(
                    "POST", url, json=body, headers=self._stream_headers()
                ) as response:
                    self._raise_for_status(response.status_code)
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if data == "[DONE]":
                            break
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        if not isinstance(event, dict):
                            continue

                        for choice in event.get("choices") or []:
                            if not isinstance(choice, dict):
                                continue
                            delta = choice.get("delta") or {}
                            text = delta.get("content")
                            if text:
                                yield ChatChunk(type="text", text=text)

                        usage = event.get("usage")
                        if usage:
                            yield ChatChunk(type="usage", usage=usage)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        yield ChatChunk(type="done")

    def _non_stream_chunks(self, payload: dict) -> Iterator[ChatChunk]:
        """Yield text/usage/done for a buffered completion payload."""
        choices = payload.get("choices") or []
        message = {}
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
        content = message.get("content") if isinstance(message, dict) else None
        if content:
            yield ChatChunk(type="text", text=str(content))

        usage = payload.get("usage")
        if usage:
            yield ChatChunk(type="usage", usage=usage)
        yield ChatChunk(type="done")

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        body = {"model": self.model, "input": texts}
        try:
            payload = await self._post_json(url, body)
        except ProviderError:
            return None

        data = payload.get("data")
        if not isinstance(data, list):
            return None

        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list):
                return None
            vectors.append([float(value) for value in embedding])
        return vectors
