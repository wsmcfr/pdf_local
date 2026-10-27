import asyncio
import json

import httpx
import pytest

from app.providers import factory
from app.providers.base import ChatChunk, ChatMessage, ChatRequest, ModelInfo
from app.providers.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.providers.gemini import GeminiProvider
from app.providers.registry import config_from_row

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
API_KEY = "gemini-test-key"
MODEL = "gemini-2.0-flash"


def make_provider(handler, *, api_key: str | None = API_KEY, model: str = MODEL):
    return GeminiProvider(
        BASE_URL,
        api_key,
        model,
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )


def collect(provider: GeminiProvider, request: ChatRequest) -> list[ChatChunk]:
    async def run() -> list[ChatChunk]:
        return [chunk async for chunk in provider.stream_chat(request)]

    return asyncio.run(run())


def user_request(stream: bool, **overrides) -> ChatRequest:
    payload = {
        "model": MODEL,
        "messages": [ChatMessage(role="user", content="ping")],
        "stream": stream,
    }
    payload.update(overrides)
    return ChatRequest(**payload)


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


def test_list_models_uses_header_key_and_strips_prefix():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1beta/models"
        assert request.headers["x-goog-api-key"] == API_KEY
        assert "authorization" not in request.headers
        assert "key" not in request.url.params
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-2.0-flash",
                        "displayName": "Gemini 2.0 Flash",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {"name": "models/text-embedding-004"},
                ]
            },
        )

    models = asyncio.run(make_provider(handler).list_models())

    assert models == [
        ModelInfo(id="gemini-2.0-flash", name="Gemini 2.0 Flash"),
        ModelInfo(id="text-embedding-004", name=None),
    ]


def test_list_models_omits_header_without_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "x-goog-api-key" not in request.headers
        assert "key" not in request.url.params
        return httpx.Response(200, json={"models": [{"name": "models/gemini-pro"}]})

    models = asyncio.run(make_provider(handler, api_key=None).list_models())

    assert models == [ModelInfo(id="gemini-pro", name=None)]


def test_list_models_returns_empty_list_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": 404, "status": "NOT_FOUND"}})

    assert asyncio.run(make_provider(handler).list_models()) == []


# ---------------------------------------------------------------------------
# Chat mapping
# ---------------------------------------------------------------------------


def test_non_stream_concatenates_candidate_parts_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1beta/models/gemini-2.0-flash:generateContent"
        body = json.loads(request.content)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "ping"}]}]
        assert "systemInstruction" not in body
        assert "generationConfig" not in body
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "po"}, {"text": "ng"}],
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 3,
                    "candidatesTokenCount": 2,
                    "totalTokenCount": 5,
                },
            },
        )

    chunks = collect(make_provider(handler), user_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "usage", "done"]
    assert chunks[0].text == "pong"
    assert chunks[1].usage == {
        "promptTokenCount": 3,
        "candidatesTokenCount": 2,
        "totalTokenCount": 5,
    }


def test_system_instruction_and_role_mapping_and_generation_config():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["systemInstruction"] == {
            "parts": [{"text": "You are helpful\n\nBe terse"}]
        }
        assert [item["role"] for item in body["contents"]] == ["user", "model", "user"]
        assert body["contents"][1]["parts"] == [{"text": "earlier answer"}]
        assert body["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 64}
        return httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )

    request = user_request(
        stream=False,
        messages=[
            ChatMessage(role="system", content="You are helpful"),
            ChatMessage(role="system", content="Be terse"),
            ChatMessage(role="user", content="ping"),
            ChatMessage(role="assistant", content="earlier answer"),
            ChatMessage(role="USER", content="again"),
        ],
        max_tokens=64,
        temperature=0.2,
    )

    chunks = collect(make_provider(handler), request)

    assert [chunk.type for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "ok"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_stream_parses_sse_parts_and_usage_metadata():
    sse = "\n".join(
        [
            'data: {"candidates": [{"content": {"parts": [{"text": "Hel"}]}}]}',
            "",
            'data: {"candidates": [{"content": {"parts": [{"text": "lo"}]}}]}',
            "",
            'data: {"candidates": [{"content": {"parts": [{"text": "!"}]}}], '
            '"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, '
            '"totalTokenCount": 5}}',
            "",
            "data: [DONE]",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert (
            request.url.path
            == "/v1beta/models/gemini-2.0-flash:streamGenerateContent"
        )
        assert request.url.params["alt"] == "sse"
        body = json.loads(request.content)
        assert body["contents"] == [{"role": "user", "parts": [{"text": "ping"}]}]
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse,
        )

    chunks = collect(make_provider(handler), user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "text", "text", "usage", "done"]
    assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == "Hello!"
    assert chunks[3].usage == {
        "promptTokenCount": 3,
        "candidatesTokenCount": 2,
        "totalTokenCount": 5,
    }


def test_stream_without_done_still_emits_done():
    sse = 'data: {"candidates": [{"content": {"parts": [{"text": "Hi"}]}}]}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse
        )

    chunks = collect(make_provider(handler), user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "done"]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (404, ProviderNotFoundError),
        (429, ProviderRateLimitError),
    ],
)
def test_http_status_maps_to_typed_error(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": status}})

    with pytest.raises(expected):
        collect(make_provider(handler), user_request(stream=False))


def test_timeout_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderTimeoutError):
        asyncio.run(make_provider(handler).list_models())


def test_stream_timeout_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderTimeoutError):
        collect(make_provider(handler), user_request(stream=True))


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def test_embed_builds_batch_requests_and_parses_values():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert (
            request.url.path
            == "/v1beta/models/text-embedding-004:batchEmbedContents"
        )
        assert request.headers["x-goog-api-key"] == API_KEY
        body = json.loads(request.content)
        assert body["requests"] == [
            {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": "a"}]},
            },
            {
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": "b"}]},
            },
        ]
        return httpx.Response(
            200,
            json={
                "embeddings": [
                    {"values": [0.1, 0.2]},
                    {"values": [0.3, 0.4]},
                ]
            },
        )

    vectors = asyncio.run(
        make_provider(handler, model="text-embedding-004").embed(["a", "b"])
    )

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_returns_none_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 401}})

    assert asyncio.run(make_provider(handler, model="text-embedding-004").embed(["a"])) is None


def test_embed_empty_input_skips_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("embedding must not hit the network")

    assert asyncio.run(make_provider(handler).embed([])) == []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_creates_gemini_and_rejects_unknown_kinds():
    row = {
        "id": "p1",
        "name": "Gemini",
        "kind": "gemini",
        "base_url": BASE_URL,
        "model": MODEL,
    }
    config = config_from_row(row, API_KEY)

    provider = factory.create_provider(config)
    assert isinstance(provider, GeminiProvider)
    assert provider.kind == "gemini"
    assert provider.model == MODEL

    config.kind = "unknown_kind"
    with pytest.raises(ProviderConfigError):
        factory.create_provider(config)
