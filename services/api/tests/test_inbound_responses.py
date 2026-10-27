import json

import pytest
from fastapi.testclient import TestClient

from app.api import inbound_responses
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
        inbound_responses.factory, "create_provider", lambda config, **kwargs: provider
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
            "openai_responses",
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
def test_responses_non_stream_returns_response_object(client, monkeypatch):
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
        "/v1/responses",
        headers=HEADERS,
        json={
            "model": "fake-model",
            "instructions": "你是一个资料助手。",
            "input": "营收多少？",
            "temperature": 0.2,
            "max_output_tokens": 64,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("resp_")
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["model"] == "fake-model"
    assert isinstance(body["created_at"], int)

    assert len(body["output"]) == 1
    item = body["output"][0]
    assert item["id"].startswith("msg_")
    assert item["type"] == "message"
    assert item["role"] == "assistant"
    assert item["content"] == [
        {
            "type": "output_text",
            "text": "营收增长 20% [报告.pdf p.12]。",
            "annotations": [],
        }
    ]

    assert body["output_text"] == "营收增长 20% [报告.pdf p.12]。"
    assert body["usage"] == {"input_tokens": 5, "output_tokens": 9}
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
    assert sent.temperature == 0.2
    assert sent.max_tokens == 64
    assert sent.messages[0].role == "system"
    assert "报告.pdf p.12" in sent.messages[0].content
    assert sent.messages[1].role == "system"
    assert sent.messages[1].content == "你是一个资料助手。"
    assert sent.messages[-1].role == "user"
    assert sent.messages[-1].content == "营收多少？"
    assert retriever.calls == [("营收多少？", 8)]
    assert GATEWAY_TOKEN not in response.text


def test_responses_parses_input_array_and_content_parts(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider([ChatChunk(type="text", text="ok"), ChatChunk(type="done")])
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses",
        headers=HEADERS,
        json={
            "instructions": "系统提示",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "第一段"},
                        {"type": "input_text", "text": "第二段"},
                    ],
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    sent = provider.requests[0]
    assert [message.role for message in sent.messages] == ["system", "user"]
    assert sent.messages[0].content == "系统提示"
    assert sent.messages[1].content == "第一段\n第二段"
    assert response.json()["output_text"] == "ok"


def test_responses_parses_string_input_array_and_bare_parts(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    provider = FakeProvider([ChatChunk(type="text", text="ok"), ChatChunk(type="done")])
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses",
        headers=HEADERS,
        json={
            "input": [
                "直接字符串",
                {"type": "input_text", "text": "裸文本块"},
            ]
        },
    )

    assert response.status_code == 200, response.text
    sent = provider.requests[0]
    assert [message.role for message in sent.messages] == ["user", "user"]
    assert [message.content for message in sent.messages] == [
        "直接字符串",
        "裸文本块",
    ]


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def test_responses_stream_event_order_and_text(client, monkeypatch):
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
        "/v1/responses",
        headers=HEADERS,
        json={
            "model": "fake-model",
            "input": "营收多少？",
            "stream": True,
        },
    )

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")

    events = sse_events(response.text)
    names = [name for name, _ in events]
    assert names == [
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.delta",
        "response.output_text.done",
        "response.content_part.done",
        "response.output_item.done",
        "response.completed",
    ]
    assert all(data["type"] == name for name, data in events)

    deltas = [
        data["delta"]
        for name, data in events
        if name == "response.output_text.delta"
    ]
    assert "".join(deltas) == "营收增长 20%"

    created = events[0][1]["response"]
    assert created["id"].startswith("resp_")
    assert created["status"] == "in_progress"
    assert created["model"] == "fake-model"

    text_done = events[5][1]
    assert text_done["text"] == "营收增长 20%"

    completed = events[-1][1]["response"]
    assert completed["object"] == "response"
    assert completed["status"] == "completed"
    assert completed["output_text"] == "营收增长 20%"
    assert completed["usage"] == {"total_tokens": 7}
    assert completed["kb_citations"][0]["file_name"] == "报告.pdf"
    assert provider.requests[0].stream is True


# ---------------------------------------------------------------------------
# RAG modes
# ---------------------------------------------------------------------------
def test_responses_rag_off_proxies_without_injection(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    retriever = FakeRetriever(CONTEXTS)
    client.app.state.retriever = retriever
    provider = FakeProvider([ChatChunk(type="text", text="ok"), ChatChunk(type="done")])
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses",
        headers=HEADERS,
        json={"instructions": "系统提示", "input": "营收多少？"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kb_citations"] == []
    sent = provider.requests[0]
    assert [message.role for message in sent.messages] == ["system", "user"]
    assert sent.messages[1].content == "营收多少？"
    assert retriever.calls == []


def test_responses_auto_without_context_refuses_without_calling_provider(
    client, monkeypatch
):
    add_provider(client)
    retriever = FakeRetriever([])
    client.app.state.retriever = retriever
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses",
        headers=HEADERS,
        json={"input": "知识库里有什么？"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert body["output_text"] == "资料中没有找到。"
    assert body["output"][0]["content"][0]["text"] == "资料中没有找到。"
    assert body["kb_citations"] == []
    assert provider.requests == []
    assert retriever.calls == [("知识库里有什么？", 8)]


def test_responses_stream_auto_without_context_refuses(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever([])
    provider = FakeProvider(error=AssertionError("provider must not be called"))
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses",
        headers=HEADERS,
        json={"input": "知识库里有什么？", "stream": True},
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    assert events[0][0] == "response.created"
    assert events[-1][0] == "response.completed"
    assert events[-1][1]["response"]["output_text"] == "资料中没有找到。"
    assert events[-1][1]["response"]["kb_citations"] == []
    assert provider.requests == []


# ---------------------------------------------------------------------------
# Gateway auth
# ---------------------------------------------------------------------------
def test_responses_requires_gateway_token(client, monkeypatch):
    add_provider(client)
    client.app.state.settings["gateway_rag_mode"] = "off"
    provider = FakeProvider([ChatChunk(type="text", text="ok"), ChatChunk(type="done")])
    patch_provider(monkeypatch, provider)
    payload = {"input": "x"}

    missing = client.post("/v1/responses", json=payload)
    assert missing.status_code == 401
    assert GATEWAY_TOKEN not in missing.text

    wrong = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer wrong-token"},
        json=payload,
    )
    assert wrong.status_code == 401
    assert "wrong-token" not in wrong.text

    accepted = client.post(
        "/v1/responses",
        headers={"x-api-key": GATEWAY_TOKEN},
        json=payload,
    )
    assert accepted.status_code == 200, accepted.text


def test_responses_gateway_disabled_returns_403(client):
    client.app.state.settings["gateway_enabled"] = False

    response = client.post(
        "/v1/responses", headers=HEADERS, json={"input": "x"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "GATEWAY_DISABLED"
    assert GATEWAY_TOKEN not in response.text


# ---------------------------------------------------------------------------
# Provider errors
# ---------------------------------------------------------------------------
def test_responses_without_provider_returns_openai_error(client):
    response = client.post(
        "/v1/responses", headers=HEADERS, json={"input": "x"}
    )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
    assert body["error"]["type"] == "invalid_request_error"


def test_responses_upstream_auth_error_maps_to_openai_error(client, monkeypatch):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        error=ProviderAuthError("authentication failed (HTTP 401)")
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses", headers=HEADERS, json={"input": "营收多少？"}
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error"]["type"] == "authentication_error"
    assert body["error"]["code"] == "UPSTREAM_AUTH_ERROR"
    # Fixed message only: no raw upstream text or credentials.
    assert "authentication failed" not in response.text
    assert GATEWAY_TOKEN not in response.text


def test_responses_stream_upstream_error_emits_response_failed(
    client, monkeypatch
):
    add_provider(client)
    client.app.state.retriever = FakeRetriever(CONTEXTS)
    provider = FakeProvider(
        error=ProviderAuthError("authentication failed (HTTP 401)")
    )
    patch_provider(monkeypatch, provider)

    response = client.post(
        "/v1/responses",
        headers=HEADERS,
        json={"input": "营收多少？", "stream": True},
    )

    assert response.status_code == 200
    events = sse_events(response.text)
    names = [name for name, _ in events]
    assert names == [
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
        "response.failed",
    ]

    failed = events[-1][1]
    assert failed["type"] == "response.failed"
    assert failed["response"]["status"] == "failed"
    assert failed["response"]["error"]["code"] == "UPSTREAM_AUTH_ERROR"
    assert failed["response"]["error"]["type"] == "authentication_error"
    assert "authentication failed" not in response.text
    assert GATEWAY_TOKEN not in response.text
