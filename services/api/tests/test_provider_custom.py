import asyncio
import json

import httpx
import pytest

from app.providers import factory
from app.providers.base import ChatChunk, ChatMessage, ChatRequest, ModelInfo
from app.providers.custom import CustomProvider
from app.providers.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.providers.presets import PRESETS
from app.providers.registry import config_from_row

BASE_URL = "https://api.example.com/v1"
CHAT_URL = "https://api.example.com/custom/chat"
API_KEY = "sk-custom-secret"
MODEL = "my-model"


def make_provider(
    handler,
    *,
    extra: dict | None = None,
    api_key: str | None = API_KEY,
    model: str = MODEL,
):
    return CustomProvider(
        BASE_URL,
        api_key,
        model,
        extra=extra,
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )


def collect(provider: CustomProvider, request: ChatRequest) -> list[ChatChunk]:
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
# Non-streaming custom JSON
# ---------------------------------------------------------------------------


def test_non_stream_uses_custom_url_and_response_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == CHAT_URL
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        body = json.loads(request.content)
        assert body == {
            "model": MODEL,
            "messages": [{"role": "user", "content": "ping"}],
            "stream": False,
        }
        return httpx.Response(
            200,
            json={
                "data": {"answer": "pong"},
                "usage": {"total_tokens": 4},
            },
        )

    provider = make_provider(
        handler,
        extra={
            "chat_url": CHAT_URL,
            "response_text_path": "data.answer",
        },
    )

    chunks = collect(provider, user_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "usage", "done"]
    assert chunks[0].text == "pong"
    assert chunks[1].usage == {"total_tokens": 4}


def test_non_stream_default_response_path_handles_openai_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "default"}}]}
        )

    chunks = collect(make_provider(handler), user_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "default"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def test_stream_parses_sse_with_custom_done_marker():
    sse = "\n".join(
        [
            'data: {"delta": {"content": "Hel"}}',
            "",
            'data: {"delta": {"content": "lo"}}',
            "",
            "data: [[END]]",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["stream"] is True
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse,
        )

    provider = make_provider(
        handler,
        extra={
            "chat_url": CHAT_URL,
            "stream_text_path": "delta.content",
            "stream_done": "[[END]]",
        },
    )

    chunks = collect(provider, user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "text", "done"]
    assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == "Hello"


def test_stream_without_done_marker_still_emits_done():
    sse = 'data: {"delta": {"content": "Hi"}}\n'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse,
        )

    provider = make_provider(
        handler,
        extra={"chat_url": CHAT_URL, "stream_text_path": "delta.content"},
    )

    chunks = collect(provider, user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "Hi"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def test_auth_bearer_is_the_default():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={"response_text_path": "answer"},
    )

    collect(provider, user_request(stream=False))


def test_auth_x_api_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == API_KEY
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={"auth": "x-api-key", "response_text_path": "answer"},
    )

    collect(provider, user_request(stream=False))


def test_auth_header_override():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-custom-key"] == API_KEY
        assert "x-api-key" not in request.headers
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={
            "auth": "x-api-key",
            "auth_header": "X-Custom-Key",
            "response_text_path": "answer",
        },
    )

    collect(provider, user_request(stream=False))


def test_auth_query_only_adds_key_to_request_url():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["key"] == API_KEY
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={"auth": "query", "response_text_path": "answer"},
    )

    assert API_KEY not in provider.base_url

    collect(provider, user_request(stream=False))


def test_auth_none_sends_no_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        assert "x-api-key" not in request.headers
        assert "key" not in request.url.params
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={"auth": "none", "response_text_path": "answer"},
    )

    collect(provider, user_request(stream=False))


def test_headers_support_placeholders():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-custom"] == API_KEY
        assert request.headers["x-model"] == f"model-{MODEL}"
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={
            "headers": {
                "X-Custom": "{{api_key}}",
                "X-Model": "model-{{model}}",
            },
            "response_text_path": "answer",
        },
    )

    collect(provider, user_request(stream=False))


# ---------------------------------------------------------------------------
# Body templates and paths
# ---------------------------------------------------------------------------


def test_body_template_nested_placeholders_keep_types():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == MODEL
        assert body["messages"] == [
            {"role": "system", "content": "You are helpful"},
            {"role": "system", "content": "Be terse"},
            {"role": "user", "content": "ping"},
        ]
        assert body["system"] == "You are helpful\n\nBe terse"
        assert body["stream"] is False
        assert body["options"] == {"temperature": 0.2, "max_tokens": 32}
        assert body["meta"] == {"key": f"k={API_KEY}"}
        assert body["keep"] == "{{unknown}}"
        return httpx.Response(200, json={"result": {"text": "ok"}})

    provider = make_provider(
        handler,
        extra={
            "body_template": {
                "model": "{{model}}",
                "messages": "{{messages_json}}",
                "system": "{{system}}",
                "stream": "{{stream}}",
                "options": {
                    "temperature": "{{temperature}}",
                    "max_tokens": "{{max_tokens}}",
                },
                "meta": {"key": "k={{api_key}}"},
                "keep": "{{unknown}}",
            },
            "response_text_path": "result.text",
        },
    )

    request = user_request(
        stream=False,
        messages=[
            ChatMessage(role="system", content="You are helpful"),
            ChatMessage(role="system", content="Be terse"),
            ChatMessage(role="user", content="ping"),
        ],
        temperature=0.2,
        max_tokens=32,
    )

    chunks = collect(provider, request)

    assert [chunk.type for chunk in chunks] == ["text", "done"]
    assert chunks[0].text == "ok"


def test_embedded_messages_json_renders_as_json_string():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prefix = "Messages: "
        assert body["prompt"].startswith(prefix)
        assert json.loads(body["prompt"][len(prefix) :]) == [
            {"role": "user", "content": "ping"}
        ]
        assert body["stream"] == "s=false"
        return httpx.Response(200, json={"answer": "ok"})

    provider = make_provider(
        handler,
        extra={
            "body_template": {
                "prompt": "Messages: {{messages_json}}",
                "stream": "s={{stream}}",
            },
            "response_text_path": "answer",
        },
    )

    collect(provider, user_request(stream=False))


def test_get_path_supports_indexes_and_missing_paths():
    data = {"a": {"b": [{"c": 1}, {"c": 2}]}}

    assert CustomProvider._get_path(data, "a.b.0.c") == 1
    assert CustomProvider._get_path(data, "a.b.1.c") == 2
    assert CustomProvider._get_path(data, "a.b.2.c") is None
    assert CustomProvider._get_path(data, "a.missing.c") is None
    assert CustomProvider._get_path(data, "") is None


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------


def test_list_models_uses_models_url_and_custom_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.example.com/custom/models"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(
            200,
            json={
                "result": {
                    "items": [
                        {"id": "m1", "name": "Model One"},
                        "m2",
                    ]
                }
            },
        )

    provider = make_provider(
        handler,
        extra={
            "models_url": "https://api.example.com/custom/models",
            "models_path": "result.items",
        },
    )

    models = asyncio.run(provider.list_models())

    assert models == [
        ModelInfo(id="m1", name="Model One"),
        ModelInfo(id="m2", name=None),
    ]


def test_list_models_defaults_to_data_path():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "m1"}]})

    provider = make_provider(
        handler,
        extra={"models_url": "https://api.example.com/models"},
    )

    assert asyncio.run(provider.list_models()) == [ModelInfo(id="m1", name=None)]


def test_list_models_returns_empty_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    provider = make_provider(
        handler,
        extra={"models_url": "https://api.example.com/models"},
    )

    assert asyncio.run(provider.list_models()) == []


def test_list_models_without_models_url_skips_network():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("models_url is not configured")

    assert asyncio.run(make_provider(handler).list_models()) == []


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


def test_embed_uses_embedding_url_and_result_path():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.example.com/custom/embeddings"
        body = json.loads(request.content)
        assert body == {"model": MODEL, "input": ["a", "b"]}
        return httpx.Response(
            200,
            json={"result": {"vectors": [[0.1, 0.2], [0.3, 0.4]]}},
        )

    provider = make_provider(
        handler,
        extra={
            "embedding_url": "https://api.example.com/custom/embeddings",
            "embedding_result_path": "result.vectors",
        },
    )

    assert asyncio.run(provider.embed(["a", "b"])) == [[0.1, 0.2], [0.3, 0.4]]


def test_embed_returns_none_without_embedding_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("embedding_url is not configured")

    assert asyncio.run(make_provider(handler).embed(["a"])) is None


def test_embed_returns_none_on_upstream_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    provider = make_provider(
        handler,
        extra={"embedding_url": "https://api.example.com/custom/embeddings"},
    )

    assert asyncio.run(provider.embed(["a"])) is None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, ProviderAuthError),
        (429, ProviderRateLimitError),
    ],
)
def test_http_status_maps_to_typed_error(status, expected):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    provider = make_provider(
        handler,
        extra={"response_text_path": "answer"},
    )

    with pytest.raises(expected):
        collect(provider, user_request(stream=False))


def test_timeout_maps_to_typed_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = make_provider(handler, extra={"response_text_path": "answer"})

    with pytest.raises(ProviderTimeoutError):
        collect(provider, user_request(stream=False))


def test_query_auth_key_is_not_leaked_in_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    provider = make_provider(
        handler,
        extra={"auth": "query", "response_text_path": "answer"},
    )

    with pytest.raises(ProviderAuthError) as info:
        collect(provider, user_request(stream=False))

    assert API_KEY not in str(info.value)


# ---------------------------------------------------------------------------
# Factory and presets
# ---------------------------------------------------------------------------


def test_factory_creates_custom_provider_with_extra():
    row = {
        "id": "p1",
        "name": "Custom",
        "kind": "custom",
        "base_url": BASE_URL,
        "model": MODEL,
        "extra_json": json.dumps(
            {"chat_url": CHAT_URL, "response_text_path": "data.answer"}
        ),
    }
    config = config_from_row(row, API_KEY)

    provider = factory.create_provider(
        config, transport=httpx.MockTransport(lambda request: httpx.Response(200))
    )

    assert isinstance(provider, CustomProvider)
    assert provider.kind == "custom"
    assert provider.model == MODEL
    assert provider.chat_url == CHAT_URL
    assert provider.response_text_path == "data.answer"


def test_factory_still_rejects_unknown_kinds():
    row = {
        "id": "p1",
        "name": "Unknown",
        "kind": "not_a_provider",
        "base_url": BASE_URL,
        "model": MODEL,
    }
    config = config_from_row(row, API_KEY)

    with pytest.raises(ProviderConfigError):
        factory.create_provider(config)


def test_presets_cover_expected_providers_and_fields():
    required_fields = {"id", "name", "kind", "base_url", "default_model"}
    allowed_kinds = {
        "openai_chat",
        "openai_responses",
        "anthropic",
        "gemini",
        "custom",
    }

    assert len(PRESETS) >= 9
    ids = [preset["id"] for preset in PRESETS]
    assert len(ids) == len(set(ids))

    for preset in PRESETS:
        assert required_fields <= set(preset)
        assert preset["id"]
        assert preset["name"]
        assert preset["kind"] in allowed_kinds
        assert preset["base_url"].startswith("https://")
        assert isinstance(preset["default_model"], str)

    required_ids = {
        "openai",
        "openai-responses",
        "deepseek",
        "qwen",
        "kimi",
        "glm",
        "minimax",
        "siliconflow",
        "openrouter",
    }
    assert required_ids <= set(ids)
