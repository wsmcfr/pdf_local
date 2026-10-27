import asyncio
import json

import httpx
import pytest

from app.providers import factory
from app.providers.anthropic import AnthropicProvider
from app.providers.base import ChatChunk, ChatMessage, ChatRequest, ModelInfo
from app.providers.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.providers.registry import config_from_row

BASE_URL = "https://api.anthropic.com/v1"
API_KEY = "sk-ant-test"


def make_provider(handler, *, api_key: str | None = API_KEY, model: str = "claude-3-5-sonnet"):
    return AnthropicProvider(
        BASE_URL,
        api_key,
        model,
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )


def collect(provider: AnthropicProvider, request: ChatRequest) -> list[ChatChunk]:
    async def run() -> list[ChatChunk]:
        return [chunk async for chunk in provider.stream_chat(request)]

    return asyncio.run(run())


def user_request(stream: bool, **overrides) -> ChatRequest:
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [ChatMessage(role="user", content="ping")],
        "stream": stream,
    }
    payload.update(overrides)
    return ChatRequest(**payload)


def test_headers_use_api_key_and_anthropic_version():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == API_KEY
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert request.headers["content-type"] == "application/json"
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [{"id": "claude-3-5-sonnet"}]})

    models = asyncio.run(make_provider(handler).list_models())

    assert models == [ModelInfo(id="claude-3-5-sonnet", name=None)]


def test_list_models_parses_display_name():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": "claude-3-5-sonnet", "display_name": "Claude 3.5 Sonnet"}]},
        )

    models = asyncio.run(make_provider(handler).list_models())

    assert models == [ModelInfo(id="claude-3-5-sonnet", name="Claude 3.5 Sonnet")]


def test_list_models_returns_empty_list_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "no model listing"})

    assert asyncio.run(make_provider(handler).list_models()) == []


def test_non_stream_concatenates_text_content_blocks():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/messages"
        body = json.loads(request.content)
        assert body["model"] == "claude-3-5-sonnet"
        assert body["stream"] is False
        assert body["max_tokens"] == 1024
        assert body["messages"] == [{"role": "user", "content": "ping"}]
        assert "system" not in body
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "po"},
                    {"type": "thinking", "thinking": "ignored"},
                    {"type": "text", "text": "ng"},
                ],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    chunks = collect(make_provider(handler), user_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "usage", "done"]
    assert chunks[0].text == "pong"
    assert chunks[1].usage == {"input_tokens": 3, "output_tokens": 2}


def test_system_messages_become_top_level_system_and_max_tokens_passthrough():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["system"] == "You are helpful\n\nBe terse"
        assert [m["role"] for m in body["messages"]] == ["user"]
        assert body["max_tokens"] == 64
        assert body["temperature"] == 0.2
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    request = user_request(
        stream=False,
        messages=[
            ChatMessage(role="system", content="You are helpful"),
            ChatMessage(role="system", content="Be terse"),
            ChatMessage(role="user", content="ping"),
        ],
        max_tokens=64,
        temperature=0.2,
    )

    chunks = collect(make_provider(handler), request)

    assert [chunk.type for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "ok"


def test_stream_parses_content_block_delta_and_message_stop():
    sse = "\n".join(
        [
            "event: message_start",
            'data: {"type": "message_start", "message": {"id": "msg_1", '
            '"usage": {"input_tokens": 7, "output_tokens": 1}}}',
            "",
            "event: content_block_start",
            'data: {"type": "content_block_start", "index": 0, '
            '"content_block": {"type": "text", "text": ""}}',
            "",
            "event: ping",
            'data: {"type": "ping"}',
            "",
            "event: content_block_delta",
            'data: {"type": "content_block_delta", "index": 0, '
            '"delta": {"type": "text_delta", "text": "Hel"}}',
            "",
            "event: content_block_delta",
            'data: {"type": "content_block_delta", "index": 0, '
            '"delta": {"type": "text_delta", "text": "lo"}}',
            "",
            "event: content_block_stop",
            'data: {"type": "content_block_stop", "index": 0}',
            "",
            "event: message_delta",
            'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, '
            '"usage": {"output_tokens": 5}}',
            "",
            "event: message_stop",
            'data: {"type": "message_stop"}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        assert body["max_tokens"] == 1024
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse,
        )

    chunks = collect(make_provider(handler), user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "text", "usage", "done"]
    assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == "Hello"
    assert chunks[2].usage == {"input_tokens": 7, "output_tokens": 5}


def test_stream_error_event_becomes_error_chunk():
    sse = "\n".join(
        [
            "event: error",
            'data: {"type": "error", "error": {"type": "overloaded_error", '
            '"message": "Overloaded"}}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse
        )

    chunks = collect(make_provider(handler), user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["error", "done"]
    assert chunks[0].error == "Overloaded"


def test_stream_without_message_stop_still_emits_done():
    sse = "\n".join(
        [
            "event: content_block_delta",
            'data: {"type": "content_block_delta", "index": 0, '
            '"delta": {"type": "text_delta", "text": "Hi"}}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse
        )

    chunks = collect(make_provider(handler), user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "done"]


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
        return httpx.Response(status, json={"error": "nope"})

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


def test_embed_returns_none():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("embedding must not hit the network")

    assert asyncio.run(make_provider(handler).embed(["a", "b"])) is None


def test_factory_creates_anthropic_and_rejects_other_kinds():
    row = {
        "id": "p1",
        "name": "Claude",
        "kind": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet",
    }
    config = config_from_row(row, API_KEY)

    provider = factory.create_provider(config)
    assert isinstance(provider, AnthropicProvider)
    assert provider.kind == "anthropic"
    assert provider.model == "claude-3-5-sonnet"

    config.kind = "unknown_kind"
    with pytest.raises(ProviderConfigError):
        factory.create_provider(config)
