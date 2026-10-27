"""Google Gemini provider adapter (``generateContent`` / ``streamGenerateContent``).

Gemini differs from the OpenAI shape:

* credentials go in the ``x-goog-api-key`` header (not ``Authorization``);
* the model is part of the URL, not the JSON body;
* conversations are encoded as ``contents[]`` with ``user`` / ``model`` roles,
  while system prompts live in a top-level ``systemInstruction``;
* sampling options live under ``generationConfig`` (``maxOutputTokens``);
* streaming uses ``GET``-style SSE via ``?alt=sse`` and emits cumulative
  ``usageMetadata`` objects.

Embeddings use ``batchEmbedContents`` and return ``embeddings[].values``.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator

import httpx

from .base import ChatChunk, ChatRequest, ModelInfo
from .errors import ProviderError, ProviderTimeoutError
from .openai_compat import OpenAICompatibleProvider

MODELS_PREFIX = "models/"


class GeminiProvider(OpenAICompatibleProvider):
    kind = "gemini"

    # ------------------------------------------------------------------
    # Request mapping
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["x-goog-api-key"] = self.api_key
        return headers

    def _stream_headers(self) -> dict[str, str]:
        headers = self._headers()
        headers["accept"] = "text/event-stream"
        return headers

    def _chat_body(self, request: ChatRequest) -> dict:
        system_parts: list[str] = []
        contents: list[dict] = []
        for message in request.messages:
            role = (message.role or "").strip().lower()
            if role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            contents.append(
                {
                    "role": "model" if role in ("assistant", "model") else "user",
                    "parts": [{"text": message.content}],
                }
            )

        body: dict = {"contents": contents}
        if system_parts:
            body["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_parts)}]
            }

        generation_config: dict = {}
        if request.temperature is not None:
            generation_config["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if generation_config:
            body["generationConfig"] = generation_config

        body.update(request.extra or {})
        return body

    def _model_name(self, request: ChatRequest) -> str:
        model = (request.model or self.model or "").strip()
        if model.startswith(MODELS_PREFIX):
            model = model[len(MODELS_PREFIX) :]
        return model

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

        # Not every account/key can enumerate models; let the caller fall
        # back to the manually configured model instead of failing.
        if response.status_code == 404:
            return []

        self._raise_for_status(response.status_code)
        payload = self._parse_json(response)

        models: list[ModelInfo] = []
        for item in payload.get("models") or []:
            if not isinstance(item, dict):
                continue
            raw_name = item.get("name")
            if not raw_name:
                continue
            model_id = str(raw_name)
            if model_id.startswith(MODELS_PREFIX):
                model_id = model_id[len(MODELS_PREFIX) :]
            display_name = item.get("displayName")
            models.append(
                ModelInfo(
                    id=model_id,
                    name=str(display_name) if display_name else None,
                )
            )
        return models

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        model = self._model_name(request)
        body = self._chat_body(request)

        if not request.stream:
            url = f"{self.base_url}/models/{model}:generateContent"
            payload = await self._post_json(url, body)
            for chunk in self._non_stream_chunks(payload):
                yield chunk
            return

        url = f"{self.base_url}/models/{model}:streamGenerateContent?alt=sse"
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

                        for text in self._candidate_texts(event):
                            yield ChatChunk(type="text", text=text)

                        usage = event.get("usageMetadata")
                        if isinstance(usage, dict) and usage:
                            yield ChatChunk(type="usage", usage=usage)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        yield ChatChunk(type="done")

    def _non_stream_chunks(self, payload: dict) -> Iterator[ChatChunk]:
        texts = self._candidate_texts(payload)
        if texts:
            yield ChatChunk(type="text", text="".join(texts))

        usage = payload.get("usageMetadata")
        if isinstance(usage, dict) and usage:
            yield ChatChunk(type="usage", usage=usage)
        yield ChatChunk(type="done")

    @staticmethod
    def _candidate_texts(payload: dict) -> list[str]:
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            return []
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            return []
        content = candidate.get("content")
        if not isinstance(content, dict):
            return []
        parts = content.get("parts")
        if not isinstance(parts, list):
            return []

        texts: list[str] = []
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str) and text:
                texts.append(text)
        return texts

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not texts:
            return []
        model = self.model.strip()
        if model.startswith(MODELS_PREFIX):
            model = model[len(MODELS_PREFIX) :]
        url = f"{self.base_url}/models/{model}:batchEmbedContents"
        body = {
            "requests": [
                {
                    "model": f"{MODELS_PREFIX}{model}",
                    "content": {"parts": [{"text": text}]},
                }
                for text in texts
            ]
        }
        try:
            payload = await self._post_json(url, body)
        except ProviderError:
            return None

        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list):
            return None

        vectors: list[list[float]] = []
        for item in embeddings:
            values = item.get("values") if isinstance(item, dict) else None
            if not isinstance(values, list):
                return None
            vectors.append([float(value) for value in values])
        return vectors
