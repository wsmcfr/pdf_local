import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers import factory, secret_store
from app.providers.base import ChatChunk, ChatMessage, ChatRequest, ModelInfo
from app.providers.errors import (
    ProviderAuthError,
    ProviderConfigError,
    ProviderNotFoundError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from app.providers.openai_compat import OpenAICompatibleProvider
from app.providers.registry import config_from_row

BASE_URL = "https://api.example.com/v1"
API_KEY = "sk-test"
HEADERS = {"X-App-Token": "secret"}

PROVIDER_PAYLOAD = {
    "name": "DeepSeek",
    "kind": "openai_chat",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
}


# ---------------------------------------------------------------------------
# Adapter tests (httpx.MockTransport, no network)
# ---------------------------------------------------------------------------


def make_provider(handler, *, api_key: str | None = API_KEY, model: str = "deepseek-chat"):
    return OpenAICompatibleProvider(
        BASE_URL,
        api_key,
        model,
        transport=httpx.MockTransport(handler),
        timeout=5.0,
    )


def collect(provider: OpenAICompatibleProvider, request: ChatRequest) -> list[ChatChunk]:
    async def run() -> list[ChatChunk]:
        return [chunk async for chunk in provider.stream_chat(request)]

    return asyncio.run(run())


def user_request(stream: bool) -> ChatRequest:
    return ChatRequest(
        model="deepseek-chat",
        messages=[ChatMessage(role="user", content="ping")],
        stream=stream,
    )


def test_list_models_uses_bearer_auth():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/models"
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return httpx.Response(
            200,
            json={"data": [{"id": "deepseek-chat", "name": "DeepSeek Chat"}]},
        )

    models = asyncio.run(make_provider(handler).list_models())

    assert models == [ModelInfo(id="deepseek-chat", name="DeepSeek Chat")]


def test_list_models_omits_auth_header_without_key():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    models = asyncio.run(make_provider(handler, api_key=None).list_models())

    assert models == [ModelInfo(id="local-model", name=None)]


def test_non_stream_chat_returns_text_usage_and_done():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-chat"
        assert body["messages"] == [{"role": "user", "content": "ping"}]
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "pong"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            },
        )

    chunks = collect(make_provider(handler), user_request(stream=False))

    assert [chunk.type for chunk in chunks] == ["text", "usage", "done"]
    assert chunks[0].text == "pong"
    assert chunks[1].usage["total_tokens"] == 4


def test_stream_chat_parses_sse_events():
    sse = "\n".join(
        [
            'data: {"choices": [{"delta": {"content": "Hel"}}]}',
            "",
            'data: {"choices": [{"delta": {"content": "lo"}}]}',
            "",
            'data: {"choices": [], "usage": {"total_tokens": 2}}',
            "",
            "data: [DONE]",
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

    chunks = collect(make_provider(handler), user_request(stream=True))

    assert [chunk.type for chunk in chunks] == ["text", "text", "usage", "done"]
    assert "".join(chunk.text for chunk in chunks if chunk.type == "text") == "Hello"
    assert chunks[2].usage == {"total_tokens": 2}


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
        asyncio.run(make_provider(handler).list_models())


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


def test_factory_creates_openai_chat_and_rejects_other_kinds():
    row = {
        "id": "p1",
        "name": "P",
        "kind": "openai_chat",
        "base_url": "https://api.example.com/v1",
        "model": "m",
        "extra_json": '{"note": "x"}',
    }
    config = config_from_row(row, API_KEY)

    provider = factory.create_provider(config)
    assert isinstance(provider, OpenAICompatibleProvider)
    assert provider.model == "m"

    config.kind = "unknown_kind"
    with pytest.raises(ProviderConfigError):
        factory.create_provider(config)


def test_config_from_row_handles_bad_extra():
    config = config_from_row(
        {
            "id": "p1",
            "name": "P",
            "kind": "openai_chat",
            "base_url": BASE_URL,
            "model": "m",
            "extra_json": "not-json",
        },
        API_KEY,
    )

    assert config.api_key == API_KEY
    assert config.extra == {}


# ---------------------------------------------------------------------------
# API tests (fake provider, in-memory keyring)
# ---------------------------------------------------------------------------


@pytest.fixture
def keyring(monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(
        secret_store, "set_secret", lambda name, value: store.__setitem__(name, value)
    )
    monkeypatch.setattr(secret_store, "get_secret", lambda name: store.get(name))
    monkeypatch.setattr(
        secret_store, "delete_secret", lambda name: store.pop(name, None)
    )
    return store


@pytest.fixture
def client(tmp_path, monkeypatch, keyring):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def create_provider(client, **overrides) -> dict:
    response = client.post(
        "/api/v1/providers", headers=HEADERS, json={**PROVIDER_PAYLOAD, **overrides}
    )
    assert response.status_code == 200, response.text
    return response.json()


class FakeProvider:
    def __init__(self, *, models=None, chunks=None, error=None):
        self.models = models or [ModelInfo(id="m1", name="Model One")]
        self.chunks = chunks or [ChatChunk(type="text", text="pong"), ChatChunk(type="done")]
        self.error = error

    async def list_models(self):
        if self.error:
            raise self.error
        return self.models

    async def stream_chat(self, request: ChatRequest):
        if self.error:
            raise self.error
        for chunk in self.chunks:
            yield chunk

    async def embed(self, texts):
        return None


@pytest.fixture
def install_fake(monkeypatch):
    box: dict = {}

    def install(provider: FakeProvider) -> dict:
        def create(config, **kwargs):
            box["config"] = config
            box["kwargs"] = kwargs
            return provider

        monkeypatch.setattr(factory, "create_provider", create)
        return box

    return install


def test_models_endpoint_lists_and_caches(client, install_fake):
    created = create_provider(client, api_key=API_KEY)
    box = install_fake(FakeProvider())

    response = client.post(f"/api/v1/providers/{created['id']}/models", headers=HEADERS)

    assert response.status_code == 200, response.text
    assert response.json() == {"models": [{"id": "m1", "name": "Model One"}]}
    assert box["config"].api_key == API_KEY

    row = client.app.state.db.execute(
        "SELECT models_json FROM model_cache WHERE provider_id = ?",
        (created["id"],),
    ).fetchone()
    assert json.loads(row["models_json"]) == [{"id": "m1", "name": "Model One"}]


def test_models_endpoint_maps_upstream_auth_error(client, install_fake):
    created = create_provider(client, api_key="sk-super-secret")
    install_fake(FakeProvider(error=ProviderAuthError("bad key")))

    response = client.post(f"/api/v1/providers/{created['id']}/models", headers=HEADERS)

    assert response.status_code == 502
    assert response.json()["detail"] == "PROVIDER_AUTH_ERROR"
    assert "sk-super-secret" not in response.text


def test_test_endpoint_success(client, install_fake):
    created = create_provider(client, api_key=API_KEY)
    install_fake(FakeProvider())

    response = client.post(f"/api/v1/providers/{created['id']}/test", headers=HEADERS)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["model"] == "deepseek-chat"
    assert isinstance(body["latency_ms"], (int, float))
    assert "error" not in body


def test_test_endpoint_reports_provider_failure(client, install_fake):
    created = create_provider(client, api_key="sk-super-secret")
    install_fake(FakeProvider(error=ProviderRateLimitError("429")))

    response = client.post(f"/api/v1/providers/{created['id']}/test", headers=HEADERS)

    assert response.status_code == 429
    body = response.json()
    assert body["ok"] is False
    assert body["model"] == "deepseek-chat"
    assert body["error"] == "PROVIDER_RATE_LIMIT"
    assert isinstance(body["latency_ms"], (int, float))
    assert "sk-super-secret" not in response.text


def test_test_endpoint_timeout_maps_to_504(client, install_fake):
    created = create_provider(client, api_key=API_KEY)
    install_fake(FakeProvider(error=ProviderTimeoutError("slow")))

    response = client.post(f"/api/v1/providers/{created['id']}/test", headers=HEADERS)

    assert response.status_code == 504
    assert response.json()["error"] == "PROVIDER_TIMEOUT"


def test_endpoints_require_api_key(client):
    created = create_provider(client)

    for path in ("models", "test"):
        response = client.post(f"/api/v1/providers/{created['id']}/{path}", headers=HEADERS)
        assert response.status_code == 400
        assert response.json()["detail"] == "PROVIDER_KEY_REQUIRED"


def test_test_endpoint_requires_model(client, install_fake):
    created = create_provider(client, api_key=API_KEY, model="")
    install_fake(FakeProvider())

    response = client.post(f"/api/v1/providers/{created['id']}/test", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "MODEL_REQUIRED"


def test_unsupported_kind_is_reported_as_invalid_config(client):
    created = create_provider(client, kind="gemini", api_key=API_KEY)

    response = client.post(f"/api/v1/providers/{created['id']}/models", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "INVALID_PROVIDER_CONFIG"


def test_unknown_provider_returns_404(client):
    for path in ("models", "test"):
        response = client.post(f"/api/v1/providers/nope/{path}", headers=HEADERS)
        assert response.status_code == 404


def test_keyring_failure_does_not_leak(client, monkeypatch):
    created = create_provider(client, api_key="sk-super-secret")

    def boom(name):
        raise RuntimeError("credential store unavailable")

    monkeypatch.setattr(secret_store, "get_secret", boom)

    response = client.post(f"/api/v1/providers/{created['id']}/models", headers=HEADERS)

    assert response.status_code == 500
    assert response.json()["detail"] == "KEYRING_ERROR"
    assert "sk-super-secret" not in response.text
