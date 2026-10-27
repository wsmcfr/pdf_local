"""OpenAI Responses API provider adapter (``POST /responses``).

The Responses API differs from Chat Completions in how a conversation is
encoded: system messages become a single ``instructions`` string while the
remaining messages are sent as ``input``. Streaming is also event-based
(``response.output_text.delta`` / ``response.completed`` / ``response.failed``)
instead of ``choices[].delta``.

Model probing and embeddings reuse the plain OpenAI endpoints, so those are
inherited from :class:`OpenAICompatibleProvider`.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Iterator

import httpx

from .base import ChatChunk, ChatRequest
from .errors import ProviderError, ProviderTimeoutError
from .openai_compat import OpenAICompatibleProvider

TEXT_DELTA_EVENT = "response.output_text.delta"
COMPLETED_EVENT = "response.completed"
FAILED_EVENT = "response.failed"


class OpenAIResponsesProvider(OpenAICompatibleProvider):
    kind = "openai_responses"

    # ------------------------------------------------------------------
    # Request mapping
    # ------------------------------------------------------------------
    def _chat_body(self, request: ChatRequest) -> dict:
        instructions: list[str] = []
        input_messages: list[dict] = []
        for message in request.messages:
            role = (message.role or "").strip().lower()
            if role == "system":
                if message.content:
                    instructions.append(message.content)
                continue
            input_messages.append(
                {"role": message.role, "content": message.content}
            )

        body: dict = {
            "model": request.model or self.model,
            "input": input_messages or "",
            "stream": bool(request.stream),
        }
        if instructions:
            body["instructions"] = "\n\n".join(instructions)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_tokens is not None:
            body["max_output_tokens"] = request.max_tokens
        body.update(request.extra or {})
        return body

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        url = f"{self.base_url}/responses"
        body = self._chat_body(request)

        if not request.stream:
            payload = await self._post_json(url, body)
            for chunk in self._non_stream_chunks(payload):
                yield chunk
            return

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
                        if data == "[DONE]":
                            break
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

                        if event_type == TEXT_DELTA_EVENT:
                            delta = payload.get("delta")
                            if isinstance(delta, str) and delta:
                                yield ChatChunk(type="text", text=delta)
                            continue

                        if event_type == COMPLETED_EVENT:
                            usage = self._completed_usage(payload)
                            if usage:
                                yield ChatChunk(type="usage", usage=usage)
                            yield ChatChunk(type="done")
                            finished = True
                            break

                        if event_type == FAILED_EVENT:
                            yield ChatChunk(
                                type="error", error=self._failure_message(payload)
                            )
                            yield ChatChunk(type="done")
                            finished = True
                            break
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"provider request failed: {exc}") from exc

        if not finished:
            yield ChatChunk(type="done")

    # ------------------------------------------------------------------
    # Response parsing helpers
    # ------------------------------------------------------------------
    def _non_stream_chunks(self, payload: dict) -> Iterator[ChatChunk]:
        for text in self._output_texts(payload):
            yield ChatChunk(type="text", text=text)

        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            yield ChatChunk(type="usage", usage=usage)
        yield ChatChunk(type="done")

    @staticmethod
    def _output_texts(payload: dict) -> list[str]:
        texts: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if not isinstance(content, list):
                    continue
                for part in content:
                    text = part.get("text") if isinstance(part, dict) else None
                    if isinstance(text, str) and text:
                        texts.append(text)

        if not texts:
            output_text = payload.get("output_text")
            if isinstance(output_text, str) and output_text:
                texts.append(output_text)
        return texts

    @staticmethod
    def _completed_usage(payload: dict) -> dict | None:
        response = payload.get("response")
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict) and usage:
                return usage
        usage = payload.get("usage")
        if isinstance(usage, dict) and usage:
            return usage
        return None

    @staticmethod
    def _failure_message(payload: dict) -> str:
        error: object = None
        response = payload.get("response")
        if isinstance(response, dict):
            error = response.get("error")
        if error is None:
            error = payload.get("error")

        if isinstance(error, dict):
            message = error.get("message") or error.get("code")
            if message:
                return str(message)
        elif isinstance(error, str) and error:
            return error
        return "provider response failed"
