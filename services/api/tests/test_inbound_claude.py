import json

import pytest
from fastapi.testclient import TestClient

from app.api import inbound_claude
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
        inbound_claude.factory, "create_provider", lambda config, **kwargs: provider
    )


def add_provider(
    client,
    provider_id="p1",
    model="fake-model",
    enabled=True,
    kind="anthropic",
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
            kind,
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


def sse_events(body: str) -> list[tuple[str | None, dict]]:
    events: list[tuple[str | None, dict]] = []
    event_name = None
    for line in body.splitlines():
        if line.startswith("event: "):
            event_name = line[len("event: ") :]
        elif line.startswith("data: "):
            events.append((event_name, json.loads(line[len("data: ") :])))
            event_name = None
    return events


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------
def test_claude_non_stream_returns_message_object(client, monkeypatch):
    add_provider(client)
    retriever = FakeRetriever(CONTEXTS)
    client.app.state.retriever = retriever
    provider = FakeProvider(
        [
            ChatChunk(type="text", text="营收增长 20% [报告.pdf p.12]。"),
            ChatChunk(type="usage", usage={"input_tokens": 5, "output_tokens": 9}),
            ChatChunk(type="done"),
        ]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "model": "fake-model",
            "system": "你是一个资料助手。",
            "messages": [{"role": "user", "content": "营收多少？"}],
            "max_tokens": 64,
            "temperature": 0.2,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("msg_")
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["model"] == "fake-model"
    assert body["content"] == [
        {"type": "text", "text": "营收增长 20% [报告.pdf p.12]。"}
    ]
    assert body["stop_reason"] == "end_turn"
    assert body["stop_sequence"] is None
    assert body["usage"] == {"input_tokens": 5, "output_tokens": 9}
    assert body["metadata"]["kb_citations"][0] == {
        "file_name": "报告.pdf",
        "page_no": 12,
        "kind": "pdf",
        "chunk_id": "c1",
        "document_id": "d1",
    }
    assert body["metadata"]["kb_citations"][1]["kind"] == "markdown"

    sent = provider.requests[0]
    assert sent.stream is False
    assert sent.model == "fake-model"
    assert sent.temperature == 0.2
    assert sent.max_tokens == 64
    assert [message.role for message in sent.messages] == ["system", "system", "user"]
    assert "报告.pdf p.12" in sent.messages[0].content
    assert sent.messages[1].content == "你是一个资料助手。"
    assert sent.messages[-1].content == "营收多少？"
    assert retriever.calls == [("营收多少？", 8)]
    assert GATEWAY_TOKEN not in response.text


def test_claude_defaults_max_tokens_to_1024(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    provider = FakeProvider(
        [ChatChunk(type="text", text="ok"), ChatChunk(type="done")]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200, response.text
    assert provider.requests[0].max_tokens == 1024
    assert response.json()["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_claude_parses_system_blocks_and_message_content_blocks(
    client, monkeypatch
):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    provider = FakeProvider(
        [ChatChunk(type="text", text="ok"), ChatChunk(type="done")]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "system": [
                {"type": "text", "text": "第一段"},
                {"type": "text", "text": "第二段"},
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "问题一"},
                        {"type": "text", "text": "问题二"},
                    ],
                }
            ],
            "max_tokens": 32,
        },
    )

    assert response.status_code == 200, response.text
    sent = provider.requests[0]
    assert [message.role for message in sent.messages] == ["system", "user"]
    assert sent.messages[0].content == "第一段\n第二段"
    assert sent.messages[1].content == "问题一\n问题二"
    assert response.json()["content"][0]["text"] == "ok"


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def test_claude_stream_event_order_and_text(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        [
            ChatChunk(type="text", text="营收"),
            ChatChunk(type="text", text="增长 20%"),
            ChatChunk(type="usage", usage={"input_tokens": 4, "output_tokens": 7}),
            ChatChunk(type="done"),
        ]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "model": "fake-model",
            "messages": [{"role": "user", "content": "营收多少？"}],
            "max_tokens": 64,
            "stream": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = sse_events(response.text)
    names = [name for name, _ in events]
    assert names == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    assert all(data["type"] == name for name, data in events)

    start_message = events[0][1]["message"]
    assert start_message["id"].startswith("msg_")
    assert start_message["type"] == "message"
    assert start_message["role"] == "assistant"
    assert start_message["model"] == "fake-model"
    assert start_message["content"] == []
    assert start_message["usage"] == {"input_tokens": 0, "output_tokens": 0}
    assert start_message["metadata"]["kb_citations"][0]["file_name"] == "报告.pdf"

    block_start = events[1][1]
    assert block_start["index"] == 0
    assert block_start["content_block"] == {"type": "text", "text": ""}

    deltas = [
        data["delta"]["text"]
        for name, data in events
        if name == "content_block_delta"
    ]
    assert "".join(deltas) == "营收增长 20%"
    assert all(
        data["delta"]["type"] == "text_delta"
        for name, data in events
        if name == "content_block_delta"
    )

    message_delta = events[5][1]
    assert message_delta["delta"] == {
        "stop_reason": "end_turn",
        "stop_sequence": None,
    }
    assert message_delta["usage"] == {"output_tokens": 7}
    assert events[6][1] == {"type": "message_stop"}
    assert provider.requests[0].stream is True


# ---------------------------------------------------------------------------
# RAG modes
# ---------------------------------------------------------------------------
def test_claude_rag_off_proxies_without_injection(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    retriever = FakeRetriever(CONTEXTS)
    client.app.state.retriever = retriever
    provider = FakeProvider(
        [ChatChunk(type="text", text="ok"), ChatChunk(type="done")]
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "system": "系统提示",
            "messages": [{"role": "user", "content": "营收多少？"}],
            "max_tokens": 32,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["kb_citations"] == []
    sent = provider.requests[0]
    assert [message.role for message in sent.messages] == ["system", "user"]
    assert sent.messages[1].content == "营收多少？"
    assert retriever.calls == []


def test_claude_auto_without_context_refuses_without_calling_provider(
    client, monkeypatch
):
    add_provider(client)
    retriever = FakeRetriever([])
    client.app.state.retriever = retriever
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "messages": [{"role": "user", "content": "知识库里有什么？"}],
            "max_tokens": 32,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["type"] == "message"
    assert body["content"] == [{"type": "text", "text": "资料中没有找到。"}]
    assert body["stop_reason"] == "end_turn"
    assert body["metadata"]["kb_citations"] == []
    assert provider.requests == []
    assert retriever.calls == [("知识库里有什么？", 8)]


def test_claude_stream_auto_without_context_refuses(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever([])
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "messages": [{"role": "user", "content": "知识库里有什么？"}],
            "max_tokens": 32,
            "stream": True,
        },
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert [name for name, _ in events] == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]
    delta = events[2][1]["delta"]
    assert delta == {"type": "text_delta", "text": "资料中没有找到。"}
    assert events[0][1]["message"]["metadata"]["kb_citations"] == []
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Gateway auth
# ---------------------------------------------------------------------------
def test_claude_requires_gateway_token(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    provider = FakeProvider(
        [ChatChunk(type="text", text="ok"), ChatChunk(type="done")]
    )
    patch_provider(monkeypatch, provider)
    payload = {"messages": [{"role": "user", "content": "x"}], "max_tokens": 8}

    missing = client.post("/v1/messages", json=payload)
    assert missing.status_code == 401
    assert GATEWAY_TOKEN not in missing.text

    wrong = client.post(
        "/v1/messages",
        headers={"Authorization": "Bearer wrong-token"},
        json=payload,
    )
    assert wrong.status_code == 401
    assert "wrong-token" not in wrong.text

    accepted = client.post(
        "/v1/messages",
        headers={"x-api-key": GATEWAY_TOKEN},
        json=payload,
    )
    assert accepted.status_code == 200, accepted.text


def test_claude_gateway_disabled_returns_403(client):
    client.app.state.settings["gateway_enabled"] = False

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "x"}], "max_tokens": 8},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "GATEWAY_DISABLED"
    assert GATEWAY_TOKEN not in response.text


# ---------------------------------------------------------------------------
# Request validation and provider errors
# ---------------------------------------------------------------------------
def test_claude_without_provider_returns_claude_error(client):
    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={"messages": [{"role": "user", "content": "x"}], "max_tokens": 8},
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["message"] == "no provider is configured"


@pytest.mark.parametrize("bad_value", [0, -1, "64", 1.5, True])
def test_claude_invalid_max_tokens_returns_400(client, bad_value):
    add_provider(client)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "messages": [{"role": "user", "content": "x"}],
            "max_tokens": bad_value,
        },
    )

    assert response.status_code == 400
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["message"] == "max_tokens must be a positive integer"


def test_claude_upstream_auth_error_maps_to_claude_error(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        error=ProviderAuthError("authentication failed (HTTP 401)")
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "messages": [{"role": "user", "content": "营收多少？"}],
            "max_tokens": 32,
        },
    )

    assert response.status_code == 401
    body = response.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["message"] == "upstream provider rejected the credentials"
    # Fixed message only: no raw upstream text or credentials.
    assert "authentication failed" not in response.text
    assert GATEWAY_TOKEN not in response.text


def test_claude_stream_upstream_error_emits_error_event(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        error=ProviderAuthError("authentication failed (HTTP 401)")
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/messages",
        headers=HEADERS,
        json={
            "messages": [{"role": "user", "content": "营收多少？"}],
            "max_tokens": 32,
            "stream": True,
        },
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert [name for name, _ in events] == [
        "message_start",
        "content_block_start",
        "error",
    ]
    error = events[-1][1]
    assert error["type"] == "error"
    assert error["error"]["type"] == "authentication_error"
    assert "authentication failed" not in response.text
    assert GATEWAY_TOKEN not in response.text
