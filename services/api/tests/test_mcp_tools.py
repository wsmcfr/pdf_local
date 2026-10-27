"""Tests for the MCP knowledge-base tools and the /mcp auth middleware."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

import app.mcp_server as mcp_server
from app.api.settings import DEFAULTS
from app.db import connect, init_db
from app.main import app
from app.mcp_server import (
    kb_ask_impl,
    kb_get_page_impl,
    kb_list_documents_impl,
    kb_search_impl,
)
from app.providers import secret_store
from app.providers.base import ChatChunk
from app.providers.errors import ProviderAuthError

GATEWAY_TOKEN = "gateway-secret"
NOW = "2026-10-27T00:00:00+00:00"

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
        return self.contexts[:limit]


class BrokenRetriever:
    def search(self, question, limit=8):
        raise RuntimeError("index unavailable")


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
def state(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    settings = {
        **DEFAULTS,
        "gateway_enabled": True,
        "context_chunks": 8,
        "llm_provider_id": "",
    }
    state = SimpleNamespace(
        data_dir=tmp_path,
        db=conn,
        settings=settings,
        retriever=FakeRetriever([]),
    )
    yield state
    conn.close()


def add_document(
    state,
    document_id="doc1",
    file_name=None,
    path=None,
    mime="application/pdf",
    updated_at=NOW,
):
    file_name = file_name or f"{document_id}.pdf"
    path = path or (state.data_dir / file_name)
    state.db.execute(
        """
        INSERT INTO documents (
          id, root_path, path, file_name, mime, size_bytes, mtime,
          content_hash, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0, ?, 'ready', ?, ?)
        """,
        (
            document_id,
            str(state.data_dir),
            str(path),
            file_name,
            mime,
            f"hash-{document_id}",
            updated_at,
            updated_at,
        ),
    )
    state.db.commit()


def add_page(
    state,
    document_id,
    page_no,
    text,
    is_scanned=0,
    ocr_confidence=None,
    low_confidence=0,
):
    state.db.execute(
        """
        INSERT INTO pages (
          document_id, page_no, text, is_scanned, ocr_confidence,
          low_confidence, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            document_id,
            page_no,
            text,
            int(is_scanned),
            ocr_confidence,
            int(low_confidence),
            NOW,
        ),
    )
    state.db.commit()


def add_provider(state, provider_id="p1", model="fake-model"):
    state.db.execute(
        "INSERT INTO providers"
        "(id, name, kind, base_url, model, enabled, key_ref, extra_json,"
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, 1, NULL, '{}', ?, ?)",
        (
            provider_id,
            f"Provider {provider_id}",
            "openai_chat",
            "https://fake.example/v1",
            model,
            NOW,
            NOW,
        ),
    )
    state.db.commit()


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello local knowledge base")
    doc.save(path)
    doc.close()


def patch_provider(monkeypatch, provider):
    monkeypatch.setattr(
        mcp_server.factory, "create_provider", lambda config, **kwargs: provider
    )


# ---------------------------------------------------------------------------
# kb_search
# ---------------------------------------------------------------------------
def test_kb_search_returns_empty_for_no_results_and_blank_query(state):
    assert kb_search_impl(state, "营收") == {"results": []}
    assert kb_search_impl(state, "   ") == {"results": []}
    # Only the non-blank query reaches the retriever.
    assert state.retriever.calls == [("营收", 8)]


def test_kb_search_maps_hydrated_chunks(state):
    state.retriever = FakeRetriever(CONTEXTS)

    result = kb_search_impl(state, "营收", top_k=2)

    assert result["results"][0] == {
        "document_id": "d1",
        "file_name": "报告.pdf",
        "page_no": 12,
        "text": "营收增长 20%。",
        "score": 0.03,
        "chunk_id": "c1",
    }
    assert len(result["results"]) == 2
    assert state.retriever.calls == [("营收", 2)]


@pytest.mark.parametrize(
    ("requested", "expected"),
    [(0, 1), (-5, 1), (1, 1), (50, 50), (51, 50), (999, 50)],
)
def test_kb_search_clamps_top_k(state, requested, expected):
    kb_search_impl(state, "x", top_k=requested)

    assert state.retriever.calls == [("x", expected)]


def test_kb_search_degrades_on_retriever_error(state):
    state.retriever = BrokenRetriever()

    assert kb_search_impl(state, "x") == {"results": []}


# ---------------------------------------------------------------------------
# kb_get_page
# ---------------------------------------------------------------------------
def test_kb_get_page_returns_text_and_quality_flags(state):
    add_document(state, "doc1", "notes.md", mime="text/plain")
    add_page(
        state,
        "doc1",
        1,
        "第一页内容",
        is_scanned=1,
        ocr_confidence=0.5,
        low_confidence=1,
    )

    result = kb_get_page_impl(state, "doc1", 1)

    assert result == {
        "document_id": "doc1",
        "file_name": "notes.md",
        "page_no": 1,
        "text": "第一页内容",
        "is_scanned": True,
        "ocr_confidence": 0.5,
        "low_confidence": True,
    }
    assert "image_base64" not in result


def test_kb_get_page_includes_pdf_image_base64(state):
    pdf = state.data_dir / "report.pdf"
    make_pdf(pdf)
    add_document(state, "doc1", "report.pdf", path=pdf, mime="application/pdf")
    add_page(state, "doc1", 1, "page text")

    result = kb_get_page_impl(state, "doc1", 1)

    assert result["text"] == "page text"
    png = base64.b64decode(result["image_base64"])
    assert png.startswith(b"\x89PNG")


def test_kb_get_page_returns_safe_error_when_missing(state):
    result = kb_get_page_impl(state, "missing", 1)

    assert result["error"] == "PAGE_NOT_FOUND"
    assert result["text"] is None
    assert result["file_name"] is None
    assert "image_base64" not in result


# ---------------------------------------------------------------------------
# kb_list_documents
# ---------------------------------------------------------------------------
def test_kb_list_documents_filters_by_name_or_path(state):
    add_document(state, "d1", "report.pdf")
    add_document(state, "d2", "notes.md", mime="text/plain")
    add_document(state, "d3", "finance-notes.md", mime="text/plain")

    by_name = kb_list_documents_impl(state, "report")
    assert [item["id"] for item in by_name["documents"]] == ["d1"]

    by_path = kb_list_documents_impl(state, "notes")
    assert {item["id"] for item in by_path["documents"]} == {"d2", "d3"}

    all_documents = kb_list_documents_impl(state, "")
    assert len(all_documents["documents"]) == 3


def test_kb_list_documents_clamps_limit(state):
    for index in range(105):
        add_document(
            state,
            f"d{index:03d}",
            f"file-{index:03d}.md",
            mime="text/plain",
        )

    assert len(kb_list_documents_impl(state, "", limit=0)["documents"]) == 1
    assert len(kb_list_documents_impl(state, "", limit=999)["documents"]) == 100


# ---------------------------------------------------------------------------
# kb_ask
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kb_ask_refuses_without_context(state, monkeypatch):
    called = []
    monkeypatch.setattr(
        mcp_server.factory,
        "create_provider",
        lambda *args, **kwargs: called.append(True),
    )

    result = await kb_ask_impl(state, "知识库里有什么？")

    assert result == {"answer": "资料中没有找到。", "citations": [], "model": ""}
    assert called == []
    assert state.retriever.calls == [("知识库里有什么？", 8)]


@pytest.mark.asyncio
async def test_kb_ask_uses_provider_with_context(state, monkeypatch, keyring):
    state.retriever = FakeRetriever(CONTEXTS)
    add_provider(state)
    provider = FakeProvider(
        [
            ChatChunk(type="text", text="营收增长 20% [报告.pdf p.12]。"),
            ChatChunk(type="done"),
        ]
    )
    patch_provider(monkeypatch, provider)

    result = await kb_ask_impl(state, "营收多少？", top_k=8)

    assert result["answer"] == "营收增长 20% [报告.pdf p.12]。"
    assert result["model"] == "fake-model"
    assert result["citations"] == [
        {
            "file_name": "报告.pdf",
            "page_no": 12,
            "kind": "pdf",
            "chunk_id": "c1",
            "document_id": "d1",
        },
        {
            "file_name": "笔记.md",
            "page_no": 3,
            "kind": "markdown",
            "chunk_id": "c2",
            "document_id": "d2",
        },
    ]
    assert "error" not in result

    sent = provider.requests[0]
    assert sent.stream is False
    assert sent.model == "fake-model"
    assert sent.messages[0].role == "system"
    assert "报告.pdf p.12" in sent.messages[0].content
    assert "笔记.md §3" in sent.messages[0].content
    assert sent.messages[-1].content == "营收多少？"
    assert "api_key" not in json.dumps(result)


@pytest.mark.asyncio
async def test_kb_ask_without_provider_returns_safe_code(state, keyring):
    state.retriever = FakeRetriever(CONTEXTS)

    result = await kb_ask_impl(state, "营收多少？")

    assert result["error"] == "PROVIDER_NOT_CONFIGURED"
    assert result["answer"] == ""
    assert result["citations"]


@pytest.mark.asyncio
async def test_kb_ask_provider_error_is_safe(state, monkeypatch, keyring):
    state.retriever = FakeRetriever(CONTEXTS)
    add_provider(state)
    provider = FakeProvider(
        error=ProviderAuthError("authentication failed: key sk-super-secret")
    )
    patch_provider(monkeypatch, provider)

    result = await kb_ask_impl(state, "营收多少？")

    assert result["error"] == "UPSTREAM_AUTH_ERROR"
    assert result["answer"] == ""
    dumped = json.dumps(result, ensure_ascii=False)
    assert "sk-super-secret" not in dumped
    assert "authentication failed" not in dumped
    assert GATEWAY_TOKEN not in dumped


@pytest.mark.asyncio
async def test_kb_ask_clamps_top_k(state):
    await kb_ask_impl(state, "x", top_k=0)
    await kb_ask_impl(state, "x", top_k=999)

    assert state.retriever.calls == [("x", 1), ("x", 50)]


# ---------------------------------------------------------------------------
# /mcp authentication
# ---------------------------------------------------------------------------
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
        yield test_client


def mcp_post(client, headers=None, query=""):
    """POST to the SSE message endpoint with a short, session-less request."""
    merged = {"Host": "127.0.0.1:8000", "Content-Type": "application/json"}
    merged.update(headers or {})
    return client.post(f"/mcp/messages/{query}", headers=merged, json={})


def test_mcp_gateway_disabled_returns_403(client):
    client.app.state.settings["gateway_enabled"] = False

    response = client.get("/mcp/sse")

    assert response.status_code == 403
    assert response.json()["detail"] == "GATEWAY_DISABLED"
    assert GATEWAY_TOKEN not in response.text


def test_mcp_requires_token(client):
    missing = client.get("/mcp/sse")
    assert missing.status_code == 401
    assert missing.json()["detail"] == "UNAUTHORIZED"
    assert GATEWAY_TOKEN not in missing.text

    wrong = client.get(
        "/mcp/sse", headers={"Authorization": "Bearer wrong-token"}
    )
    assert wrong.status_code == 401
    assert "wrong-token" not in wrong.text
    assert GATEWAY_TOKEN not in wrong.text


def test_mcp_accepts_bearer_header(client):
    response = mcp_post(
        client, headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"}
    )

    # Auth passed; the SSE transport then rejects the session-less message.
    assert response.status_code == 400
    assert "session_id" in response.text


def test_mcp_accepts_x_api_key_header(client):
    response = mcp_post(client, headers={"x-api-key": GATEWAY_TOKEN})

    assert response.status_code == 400
    assert "session_id" in response.text


def test_mcp_accepts_query_token_and_api_key(client):
    for param in ("token", "api_key"):
        response = mcp_post(client, query=f"?{param}={GATEWAY_TOKEN}")
        assert response.status_code == 400, (param, response.status_code, response.text)
        assert "session_id" in response.text
