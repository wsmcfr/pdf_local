"""Fully configurable HTTP/SSE provider adapter.

``CustomProvider`` lets a provider be described entirely by data stored in
``ProviderConfig.extra``: request URL/method, authentication style, headers,
body template, response/stream extraction paths, and optional model/embedding
endpoints. It deliberately makes no assumptions about a vendor protocol beyond
the defaults, so unusual domestic APIs can be wired up without code changes.

Templates use ``{{name}}`` placeholders. A value that is exactly one placeholder
is replaced by the typed context value (for example ``{{stream}}`` becomes a
bool and ``{{messages_json}}`` becomes the message list). A placeholder embedded
inside a larger string is replaced by its JSON/string representation. Unknown
placeholders are left untouched.
"""

from __future__ import annotations

import json
import re
from typing import Any, AsyncIterator, Iterator

import httpx

from .base import ChatChunk, ChatRequest, ModelInfo
from .errors import ProviderError, ProviderTimeoutError
from .openai_compat import OpenAICompatibleProvider

DEFAULT_METHOD = "POST"
DEFAULT_AUTH = "bearer"
DEFAULT_AUTH_QUERY_PARAM = "key"
DEFAULT_RESPONSE_TEXT_PATH = "choices.0.message.content"
DEFAULT_STREAM_TEXT_PATH = "delta.content"
DEFAULT_STREAM_DONE = "[DONE]"
DEFAULT_MODELS_PATH = "data"
DEFAULT_EMBEDDING_RESULT_PATH = "data"

_EXACT_PLACEHOLDER = re.compile(r"^\{\{\s*([A-Za-z0-9_]+)\s*\}\}$")
_ANY_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


class CustomProvider(OpenAICompatibleProvider):
    kind = "custom"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str = "",
        *,
        extra: dict | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        super().__init__(
            base_url,
            api_key,
            model,
            transport=transport,
            timeout=timeout,
        )
        self.extra = dict(extra) if isinstance(extra, dict) else {}

        self.chat_url = str(self.extra.get("chat_url") or self.base_url).strip()
        self.method = str(self.extra.get("method") or DEFAULT_METHOD).strip().upper()
        if not self.method:
            self.method = DEFAULT_METHOD

        auth = str(self.extra.get("auth") or DEFAULT_AUTH).strip().lower()
        if auth not in {"bearer", "x-api-key", "query", "none"}:
            auth = DEFAULT_AUTH
        self.auth = auth
        raw_auth_header = self.extra.get("auth_header")
        self.auth_header = str(raw_auth_header) if raw_auth_header else None
        self.auth_query_param = str(
            self.extra.get("auth_query_param") or DEFAULT_AUTH_QUERY_PARAM
        )

        raw_headers = self.extra.get("headers")
        self.headers_template = raw_headers if isinstance(raw_headers, dict) else None

        raw_body = self.extra.get("body_template")
        self.body_template = raw_body if isinstance(raw_body, (dict, list)) else None

        self.response_text_path = str(
            self.extra.get("response_text_path") or DEFAULT_RESPONSE_TEXT_PATH
        )
        self.stream_text_path = str(
            self.extra.get("stream_text_path") or DEFAULT_STREAM_TEXT_PATH
        )
        raw_done = self.extra.get("stream_done")
        self.stream_done = (
            DEFAULT_STREAM_DONE if raw_done is None else str(raw_done)
        )

        raw_models_url = self.extra.get("models_url")
        self.models_url = str(raw_models_url).strip() if raw_models_url else ""
        self.models_path = str(self.extra.get("models_path") or DEFAULT_MODELS_PATH)

        raw_embedding_url = self.extra.get("embedding_url")
        self.embedding_url = (
            str(raw_embedding_url).strip() if raw_embedding_url else ""
        )
        self.embedding_result_path = str(
            self.extra.get("embedding_result_path") or DEFAULT_EMBEDDING_RESULT_PATH
        )

    # ------------------------------------------------------------------
    # Template / path helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _render(value: Any, context: dict) -> Any:
        """Recursively replace ``{{name}}`` placeholders in *value*."""
        if isinstance(value, dict):
            return {
                key: CustomProvider._render(item, context)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [CustomProvider._render(item, context) for item in value]
        if not isinstance(value, str):
            return value

        exact = _EXACT_PLACEHOLDER.match(value)
        if exact:
            name = exact.group(1)
            return context[name] if name in context else value

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in context:
                return match.group(0)
            item = context[name]
            if isinstance(item, str):
                return item
            return json.dumps(item, ensure_ascii=False)

        return _ANY_PLACEHOLDER.sub(replace, value)

    @staticmethod
    def _get_path(data: Any, path: str) -> Any:
        """Resolve a dot-separated path with list indexes (``a.b.0.c``)."""
        if not path:
            return None
        current = data
        for part in str(path).split("."):
            if isinstance(current, dict):
                if part not in current:
                    return None
                current = current[part]
                continue
            if isinstance(current, list):
                try:
                    index = int(part)
                except ValueError:
                    return None
                if index < 0 or index >= len(current):
                    return None
                current = current[index]
                continue
            return None
        return current

    # ------------------------------------------------------------------
    # Request mapping
    # ------------------------------------------------------------------
    def _context(self, request: ChatRequest) -> dict:
        messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
        system_parts = [
            message.content
            for message in request.messages
            if (message.role or "").strip().lower() == "system" and message.content
        ]
        return {
            "model": request.model or self.model,
            "messages_json": messages,
            "system": "\n\n".join(system_parts),
            "stream": bool(request.stream),
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "api_key": self.api_key,
        }

    def _chat_body(self, request: ChatRequest) -> Any:
        context = self._context(request)
        if self.body_template is not None:
            body = self._render(self.body_template, context)
        else:
            body = {
                "model": context["model"],
                "messages": context["messages_json"],
                "stream": context["stream"],
            }
            if request.temperature is not None:
                body["temperature"] = request.temperature
            if request.max_tokens is not None:
                body["max_tokens"] = request.max_tokens
        if isinstance(body, dict) and request.extra:
            body.update(request.extra)
        return body

    def _headers(self, context: dict | None = None) -> dict[str, str]:
        values = {"api_key": self.api_key, "model": self.model}
        if context:
            values.update(context)

        headers: dict[str, str] = {"Accept": "application/json"}
        if self.headers_template:
            for name, value in self.headers_template.items():
                rendered = self._render(value, values)
                if rendered is None:
                    continue
                headers[str(name)] = str(rendered)

        if not self.api_key:
            return headers

        if self.auth == "bearer":
            name = self.auth_header or "Authorization"
            headers[name] = f"Bearer {self.api_key}"
        elif self.auth == "x-api-key":
            name = self.auth_header or "x-api-key"
            headers[name] = self.api_key
        return headers

    def _auth_params(self) -> dict[str, str]:
        if self.auth == "query" and self.api_key:
            return {self.auth_query_param: self.api_key}
        return {}

    async def _request(
        self,
        method: str,
        url: str,
        body: Any = None,
        *,
        check_status: bool = True,
        context: dict | None = None,
    ) -> httpx.Response:
        try:
            async with self._client() as client:
                response = await client.request(
                    method,
                    url,
                    json=body,
                    headers=self._headers(context),
                    params=self._auth_params(),
                )
        except httpx.TimeoutException:
            # ``from None`` keeps the original exception (which can echo the
            # request URL and therefore a query-string API key) out of logs.
            raise ProviderTimeoutError("provider request timed out") from None
        except httpx.HTTPError:
            # Never interpolate the exception: it can echo the URL, and the
            # URL may carry a query-string API key.
            raise ProviderError("provider request failed") from None

        if check_status:
            self._raise_for_status(response.status_code)
        return response

    # ------------------------------------------------------------------
    # Provider protocol
    # ------------------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        if not self.models_url:
            return []

        response = await self._request("GET", self.models_url, check_status=False)
        # Not every endpoint/account can enumerate models; let the caller fall
        # back to the manually configured model instead of failing.
        if response.status_code == 404:
            return []
        self._raise_for_status(response.status_code)

        payload = self._parse_json(response)
        items = self._get_path(payload, self.models_path)
        if not isinstance(items, list):
            return []

        models: list[ModelInfo] = []
        for item in items:
            if isinstance(item, str):
                if item:
                    models.append(ModelInfo(id=item))
                continue
            if not isinstance(item, dict):
                continue
            model_id = item.get("id") or item.get("model") or item.get("name")
            if not model_id:
                continue
            name = item.get("name") or item.get("display_name")
            models.append(
                ModelInfo(
                    id=str(model_id),
                    name=str(name) if name else None,
                )
            )
        return models

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        context = self._context(request)
        body = self._chat_body(request)

        if not request.stream:
            response = await self._request(
                self.method, self.chat_url, body, context=context
            )
            payload = self._parse_json(response)
            for chunk in self._non_stream_chunks(payload):
                yield chunk
            return

        try:
            async with self._client() as client:
                async with client.stream(
                    self.method,
                    self.chat_url,
                    json=body,
                    headers=self._headers(context),
                    params=self._auth_params(),
                ) as response:
                    self._raise_for_status(response.status_code)
                    async for raw in response.aiter_lines():
                        line = raw.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:") :].strip()
                        if data == self.stream_done:
                            break
                        if not data:
                            continue
                        try:
                            event = json.loads(data)
                        except ValueError:
                            continue
                        if not isinstance(event, dict):
                            continue

                        text = self._get_path(event, self.stream_text_path)
                        if isinstance(text, str):
                            if text:
                                yield ChatChunk(type="text", text=text)
                        elif text is not None and not isinstance(text, (dict, list)):
                            yield ChatChunk(type="text", text=str(text))

                        usage = event.get("usage")
                        if isinstance(usage, dict) and usage:
                            yield ChatChunk(type="usage", usage=usage)
        except httpx.TimeoutException:
            raise ProviderTimeoutError("provider request timed out") from None
        except httpx.HTTPError:
            raise ProviderError("provider request failed") from None

        # Upstreams that never emit the configured marker still get a clean end.
        yield ChatChunk(type="done")

    def _non_stream_chunks(self, payload: dict) -> Iterator[ChatChunk]:
        text = self._get_path(payload, self.response_text_path)
        if isinstance(text, str):
            if text:
                yield ChatChunk(type="text", text=text)
        elif text is not None and not isinstance(text, (dict, list)):
            yield ChatChunk(type="text", text=str(text))

        usage = payload.get("usage") if isinstance(payload, dict) else None
        if isinstance(usage, dict) and usage:
            yield ChatChunk(type="usage", usage=usage)
        yield ChatChunk(type="done")

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        if not self.embedding_url:
            return None
        if not texts:
            return []

        body = {"model": self.model, "input": texts}
        try:
            response = await self._request("POST", self.embedding_url, body)
            payload = self._parse_json(response)
        except ProviderError:
            return None

        raw = self._get_path(payload, self.embedding_result_path)
        if not isinstance(raw, list):
            return None

        vectors: list[list[float]] = []
        for item in raw:
            if isinstance(item, list):
                vector = item
            elif isinstance(item, dict):
                vector = item.get("embedding")
                if vector is None:
                    vector = item.get("vector")
            else:
                return None
            if not isinstance(vector, list):
                return None
            try:
                vectors.append([float(value) for value in vector])
            except (TypeError, ValueError):
                return None
        return vectors
