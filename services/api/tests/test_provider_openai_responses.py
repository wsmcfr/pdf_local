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
)
from app.providers.openai_responses import OpenAIResponsesProvider
from app.providers.registry import config_from_row

BASE_URL = "https://api.example.com/v1"
API_KEY = "sk-test"


def make_provider(handler, *, api_key: str | None = API_KEY, model: str = "gpt-4o"):
    return OpenAIResponsesProvider(
        BASE_URL,
        api_key,
        model,
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )


def collect(provider: OpenAIResponsesProvider, request: ChatRequest) -> list[ChatChunk]:
    async def run() -> list[ChatChunk]:
        return [chunk async for chunk in provider.stream_chat(request)]

    return asyncio.run(run())


def chat_request(stream: bool, **overrides) -> ChatRequest:
    payload = {
        "model": "gpt-4o",
        "messages": [ChatMessage(role="user", content="ping")],
        "stream": stream,
    }
    payload.update(overrides)
    return ChatRequest(**payload)


def test_list_models_uses_bearer_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(
            200,
            json={"data": [{"id": "gpt-4o", "name": "GPT-4o"}]},
        )

    models = asyncio.run(make_provider(handler).list_models())

    assert models == [ModelInfo(id="gpt-4o", name="GPT-4o")]


def test_non_stream_parses_output_content_and_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["model"] == "gpt-4o"
        assert body["stream"] is False
        assert body["input"] == [{"role": "user", "content": "ping"}]
        return httpx.Response(
            200,
            json={
                "id": "resp_1",
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "output_text", "text": "po"},
                            {"type": "output_text", "text": "ng"},
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 3,
                    "output_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )

    chunks = collect(make_provider(handler), chat_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "text", "usage", "done"]
    assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == "pong"
    assert chunks[2].usage["total_tokens"] == 4


def test_non_stream_falls_back_to_output_text():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "completed", "output_text": "pong"})

    chunks = collect(make_provider(handler), chat_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "pong"


def test_stream_parses_delta_and_completed_events():
    sse = "\n".join(
        [
            "event: response.created",
            'data: {"type": "response.created", "response": {"id": "resp_1"}}',
            "",
            "event: response.output_text.delta",
            'data: {"type": "response.output_text.delta", "delta": "Hel"}',
            "",
            "event: response.output_text.delta",
            'data: {"type": "response.output_text.delta", "delta": "lo"}',
            "",
            "event: response.completed",
            'data: {"type": "response.completed", "response": '
            '{"status": "completed", "usage": {"total_tokens": 2}}}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse,
        )

    chunks = collect(make_provider(handler), chat_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "text", "usage", "done"]
    assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == "Hello"
    assert chunks[2].usage == {"total_tokens": 2}


def test_stream_emits_done_when_upstream_ends_without_completed():
    sse = "\n".join(
        [
            "event: response.output_text.delta",
            'data: {"type": "response.output_text.delta", "delta": "Hi"}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse
        )

    chunks = collect(make_provider(handler), chat_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "done"]


def test_stream_failed_event_becomes_error_chunk():
    sse = "\n".join(
        [
            "event: response.failed",
            'data: {"type": "response.failed", "response": {"status": "failed", '
            '"error": {"code": "server_error", "message": "upstream exploded"}}}',
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, text=sse
        )

    chunks = collect(make_provider(handler), chat_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["error", "done"]
    assert chunks[0].error == "upstream exploded"


def test_request_body_maps_system_to_instructions_and_limits_tokens():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["instructions"] == "You are helpful\n\nBe terse"
        assert body["input"] == [{"role": "user", "content": "ping"}]
        assert all(message["role"] != "system" for message in body["input"])
        assert body["max_output_tokens"] == 12
        assert body["temperature"] == 0.2
        return httpx.Response(200, json={"output_text": "ok"})

    request = chat_request(
        stream=False,
        messages=[
            ChatMessage(role="system", content="You are helpful"),
            ChatMessage(role="system", content="Be terse"),
            ChatMessage(role="user", content="ping"),
        ],
        max_tokens=12,
        temperature=0.2,
    )

    chunks = collect(make_provider(handler), request)

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
        collect(make_provider(handler), chat_request(stream=False))


def test_embed_returns_vectors():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.content)
        assert body["input"] == ["a", "b"]
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]},
        )

    vectors = asyncio.run(make_provider(handler).embed(["a", "b"]))

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_returns_none_on_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    assert asyncio.run(make_provider(handler).embed(["a"])) is None


def test_factory_creates_openai_responses_and_rejects_other_kinds():
    row = {
        "id": "p1",
        "name": "P",
        "kind": "openai_responses",
        "base_url": "https://api.example.com/v1",
        "model": "gpt-4o",
    }
    config = config_from_row(row, API_KEY)

    provider = factory.create_provider(config)
    assert isinstance(provider, OpenAIResponsesProvider)
    assert provider.kind == "openai_responses"
    assert provider.model == "gpt-4o"

    config.kind = "anthropic"
    with pytest.raises(ProviderConfigError):
        factory.create_provider(config)
