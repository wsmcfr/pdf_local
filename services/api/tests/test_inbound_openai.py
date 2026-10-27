import json

import pytest
from fastapi.testclient import TestClient

from app.api import gateway_auth, inbound_openai
from app.main import app
from app.providers import secret_store
from app.providers.base import ChatChunk
from app.providers.errors import ProviderAuthError

GATEWAY_TOKEN = "gateway-secret"
HEADERS = {"Authorization": f"Bearer {GATEWAY_TOKEN}"}

CONTEXTS = [
    {
        "chunk_id": "c1",
        "document_id": "d1",
        "page_no": 12,
        "text": "营收增长 20%。",
        "file_name": "报告.pdf",
        "mime": "application/pdf",
        "score": 0.03,
    },
    {
        "chunk_id": "c2",
        "document_id": "d2",
        "page_no": 3,
        "text": "第二段内容。",
        "file_name": "笔记.md",
        "mime": "text/markdown",
    },
]


class FakeRetriever:
    def __init__(self, contexts):
        self.contexts = contexts
        self.calls = []

    def search(self, question, limit=8):
        self.calls.append((question, limit))
        return self.contexts


class FakeProvider:
    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.requests = []

    async def stream_chat(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk


def patch_provider(monkeypatch, provider):
    monkeypatch.setattr(
        inbound_openai.factory, "create_provider", lambda config, **kwargs: provider
    )


def add_provider(
    client,
    provider_id="p1",
    model="fake-model",
    enabled=True,
    created_at="2026-01-01T00:00:00+00:00",
):
    client.app.state.db.execute(
        "INSERT INTO providers"
        "(id, name, kind, base_url, model, enabled, key_ref, extra_json,"
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            provider_id,
            f"Provider {provider_id}",
            "openai_chat",
            "https://fake.example/v1",
            model,
            int(enabled),
            None,
            "{}",
            created_at,
            created_at,
        ),
    )
    client.app.state.db.commit()


@pytest.fixture
def keyring(monkeypatch):
    """In-memory stand-in so tests never touch the OS credential store."""
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
    monkeypatch.setenv("KB_GATEWAY_TOKEN", GATEWAY_TOKEN)
    with TestClient(app) as test_client:
        app.state.settings.update(
            {
                "gateway_enabled": True,
                "gateway_rag_mode": "auto",
                "llm_provider_id": "",
                "context_chunks": 8,
            }
        )
        app.state.retriever = FakeRetriever([])
        yield test_client


def sse_events(body: str) -> list[str]:
    return [line[6:] for line in body.splitlines() if line.startswith("data: ")]


# ---------------------------------------------------------------------------
# Gateway token resolution
# ---------------------------------------------------------------------------
def test_gateway_token_prefers_env_then_keyring(monkeypatch, keyring):
    monkeypatch.delenv("KB_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(gateway_auth, "_generated_token", None)

    keyring["gateway-token"] = "from-keyring"
    assert gateway_auth.get_gateway_token() == "from-keyring"

    monkeypatch.setenv("KB_GATEWAY_TOKEN", "from-env")
    assert gateway_auth.get_gateway_token() == "from-env"


def test_gateway_token_generated_and_stored(monkeypatch, keyring):
    monkeypatch.delenv("KB_GATEWAY_TOKEN", raising=False)
    monkeypatch.setattr(gateway_auth, "_generated_token", None)

    token = gateway_auth.get_gateway_token()

    assert token
    assert keyring["gateway-token"] == token


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------
def test_chat_completions_non_stream_injects_rag_context(client, monkeypatch):
    add_provider(client)
    retriever = FakeRetriever(CONTEXTS)
    client.app.state.retriever = retriever
    provider = FakeProvider(
        [
            ChatChunk(type="text", text="营收增长 20% [报告.pdf p.12]。"),
            ChatChunk(type="usage", usage={"total_tokens": 12}),
            ChatChunk(type="done"),
        ]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "营收多少？"}],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "chat.completion"
    assert body["model"] == "fake-model"
    assert body["choices"][0]["message"] == {
        "role": "assistant",
        "content": "营收增长 20% [报告.pdf p.12]。",
    }
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"] == {"total_tokens": 12}
    assert body["kb_citations"][0] == {
        "file_name": "报告.pdf",
        "page_no": 12,
        "kind": "pdf",
        "chunk_id": "c1",
        "document_id": "d1",
    }
    assert body["kb_citations"][1]["kind"] == "markdown"

    sent = provider.requests[0]
    assert sent.stream is False
    assert sent.model == "fake-model"
    assert sent.messages[0].role == "system"
    assert "报告.pdf p.12" in sent.messages[0].content
    assert "笔记.md §3" in sent.messages[0].content
    assert sent.messages[-1].role == "user"
    assert sent.messages[-1].content == "营收多少？"
    assert retriever.calls == [("营收多少？", 8)]
    assert GATEWAY_TOKEN not in response.text


def test_chat_completions_accepts_content_parts(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider([ChatChunk(type="text", text="ok"), ChatChunk(type="done")])
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "第一段"},
                        {"type": "text", "text": "第二段"},
                    ],
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    assert provider.requests[0].messages[-1].content == "第一段\n第二段"
    assert response.json()["choices"][0]["message"]["content"] == "ok"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def test_chat_completions_streams_chunks_and_done(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        [
            ChatChunk(type="text", text="营收"),
            ChatChunk(type="text", text="增长 20%"),
            ChatChunk(type="usage", usage={"total_tokens": 7}),
            ChatChunk(type="done"),
        ]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "营收多少？"}],
            "stream": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = sse_events(response.text)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunks)
    assert chunks[0]["id"] == chunks[-1]["id"]
    assert "".join(
        chunk["choices"][0]["delta"].get("content", "") for chunk in chunks
    ) == "营收增长 20%"

    final = chunks[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["choices"][0]["delta"] == {}
    assert final["usage"] == {"total_tokens": 7}
    assert final["kb_citations"][0]["file_name"] == "报告.pdf"
    assert provider.requests[0].stream is True


def test_stream_without_context_refuses_without_calling_provider(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever([])
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={
            "messages": [{"role": "user", "content": "知识库里有什么？"}],
            "stream": True,
        },
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert events[-1] == "[DONE]"
    chunks = [json.loads(event) for event in events[:-1]]
    assert chunks[0]["choices"][0]["delta"]["content"] == "资料中没有找到。"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["kb_citations"] == []
    assert provider.requests == []


# ---------------------------------------------------------------------------
# RAG modes
# ---------------------------------------------------------------------------
def test_rag_off_proxies_without_injection(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    retriever = FakeRetriever(CONTEXTS)
    client.app.state.retriever = retriever
    provider = FakeProvider([ChatChunk(type="text", text="ok"), ChatChunk(type="done")])
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "营收多少？"}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kb_citations"] == []
    sent = provider.requests[0]
    assert [message.role for message in sent.messages] == ["user"]
    assert sent.messages[0].content == "营收多少？"
    assert retriever.calls == []


def test_rag_auto_without_context_refuses_without_calling_provider(
    client, monkeypatch
):
    add_provider(client)
    retriever = FakeRetriever([])
    client.app.state.retriever = retriever
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "营收多少？"}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "资料中没有找到。"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["kb_citations"] == []
    assert provider.requests == []
    assert retriever.calls == [("营收多少？", 8)]


# ---------------------------------------------------------------------------
# Gateway auth
# ---------------------------------------------------------------------------
def test_chat_requires_gateway_token(client, monkeypatch):
    add_provider(client)
    payload = {"messages": [{"role": "user", "content": "x"}]}

    missing = client.post("/v1/chat/completions", json=payload)
    assert missing.status_code == 401
    assert GATEWAY_TOKEN not in missing.text

    wrong = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer wrong-token"},
        json=payload,
    )
    assert wrong.status_code == 401
    assert "wrong-token" not in wrong.text

    accepted = client.post(
        "/v1/chat/completions",
        headers={"x-api-key": GATEWAY_TOKEN},
        json=payload,
    )
    assert accepted.status_code == 200, accepted.text


def test_gateway_disabled_returns_403(client):
    client.app.state.settings["gateway_enabled"] = False

    response = client.get("/v1/models", headers=HEADERS)

    assert response.status_code == 403
    assert response.json()["detail"] == "GATEWAY_DISABLED"
    assert GATEWAY_TOKEN not in response.text


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------
def test_chat_without_provider_returns_openai_error(client):
    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "x"}]},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert body["error"]["type"] == "invalid_request_error"


def test_upstream_auth_error_maps_to_openai_error(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        error=ProviderAuthError("authentication failed (HTTP 401)")
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/chat/completions",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "营收多少？"}]},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "UPSTREAM_AUTH_ERROR"
    # Fixed message only: no raw upstream text or credentials.
    assert "authentication failed" not in response.text
    assert GATEWAY_TOKEN not in response.text


# ---------------------------------------------------------------------------
# Model listing
# ---------------------------------------------------------------------------
def test_models_returns_provider_default_model(client):
    add_provider(client, model="fake-model")

    response = client.get("/v1/models", headers=HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert [item["id"] for item in body["data"]] == ["fake-model"]
    assert body["data"][0]["object"] == "model"


def test_models_prefers_cached_models(client):
    add_provider(client)
    client.app.state.db.execute(
        "INSERT INTO model_cache(provider_id, models_json, fetched_at) "
        "VALUES (?, ?, ?)",
        (
            "p1",
            json.dumps([{"id": "m1", "name": "M1"}, {"id": "m2"}]),
            "2026-01-01T00:00:00+00:00",
        ),
    )
    client.app.state.db.commit()

    response = client.get("/v1/models", headers=HEADERS)

    assert [item["id"] for item in response.json()["data"]] == ["m1", "m2"]


def test_models_uses_configured_provider(client):
    add_provider(client, provider_id="p1", model="first")
    add_provider(client, provider_id="p2", model="second")
    client.app.state.settings["llm_provider_id"] = "p2"

    response = client.get("/v1/models", headers=HEADERS)

    assert [item["id"] for item in response.json()["data"]] == ["second"]
