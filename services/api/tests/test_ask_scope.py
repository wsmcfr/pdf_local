from datetime import datetime, timezone

import app.api.ask as ask_module
from app.main import app
from app.providers.base import ChatChunk, ProviderConfig
from fastapi.testclient import TestClient

HEADERS = {"X-App-Token": "secret"}
MODEL = "test-model"
DOCX_MIME = (
    "application/vnd.openxmlformats-officedocument."
    "wordprocessingml.document"
)


def now():
    return datetime.now(timezone.utc).isoformat()


class FakeRetriever:
    def __init__(self, contexts):
        self.contexts = contexts
        self.calls = []

    def search(self, question, limit=8, document_id=None):
        self.calls.append((question, limit, document_id))
        return self.contexts


class FakeProvider:
    def __init__(self, text="资料中没有找到。"):
        self.text = text
        self.requests = []

    async def list_models(self):
        return []

    async def stream_chat(self, request):
        self.requests.append(request)
        yield ChatChunk(type="text", text=self.text)

    async def embed(self, texts):
        return None


def event_names(body):
    return [
        line.removeprefix("event: ")
        for line in body.splitlines()
        if line.startswith("event: ")
    ]


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    return TestClient(app)


def add_provider(client):
    conn = client.app.state.db
    conn.execute(
        "INSERT INTO providers"
        "(id, name, kind, base_url, model, enabled, key_ref, extra_json,"
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "p1",
            "Test Provider",
            "openai_chat",
            "https://example.invalid/v1",
            MODEL,
            1,
            None,
            "{}",
            now(),
            now(),
        ),
    )
    conn.commit()


def use_provider(monkeypatch, provider):
    monkeypatch.setattr(
        ask_module.registry,
        "load_config",
        lambda row: ProviderConfig(
            id=row["id"],
            name=row["name"],
            kind=row["kind"],
            base_url=row["base_url"],
            model=row["model"],
        ),
    )
    monkeypatch.setattr(
        ask_module.factory, "create_provider", lambda config: provider
    )


def add_document(client, pages, document_id="doc1", file_name="a.docx"):
    conn = client.app.state.db
    conn.execute(
        """
        INSERT INTO documents (
          id, root_path, path, file_name, mime, size_bytes, mtime,
          content_hash, status, created_at, updated_at
        ) VALUES (?, '/root', ?, ?, ?, 10, 1.0, ?, 'ready', ?, ?)
        """,
        (document_id, f"/root/{file_name}", file_name, DOCX_MIME, document_id, now(), now()),
    )
    for page_no, text in enumerate(pages, start=1):
        conn.execute(
            "INSERT INTO pages (document_id, page_no, text, is_scanned, created_at) "
            "VALUES (?, ?, ?, 0, ?)",
            (document_id, page_no, text, now()),
        )
    conn.commit()


def test_scoped_ask_uses_full_text_for_short_document(tmp_path, monkeypatch):
    provider = FakeProvider("第一页内容 [a.docx §1]")
    use_provider(monkeypatch, provider)
    retriever = FakeRetriever([])

    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client)
        add_document(client, ["第一页内容", "第二页内容"])
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "这份文件讲了什么？", "document_id": "doc1"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    body = response.text
    assert '"context_mode": "full_text"' in body
    assert retriever.calls == []
    request = provider.requests[0]
    prompt = request.messages[1].content
    assert "第一页内容" in prompt
    assert "第二页内容" in prompt
    assert "a.docx §1" in prompt


def test_scoped_ask_uses_retrieval_for_long_document(tmp_path, monkeypatch):
    provider = FakeProvider("片段 [a.docx §1]")
    use_provider(monkeypatch, provider)
    context = {
        "chunk_id": "c1",
        "document_id": "doc1",
        "page_no": 1,
        "text": "片段",
        "file_name": "a.docx",
        "mime": DOCX_MIME,
    }
    retriever = FakeRetriever([context])

    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client)
        add_document(client, ["长" * 40000])
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "总结这份文件", "document_id": "doc1"},
            headers=HEADERS,
        )

    body = response.text
    assert '"context_mode": "retrieval"' in body
    assert retriever.calls == [("总结这份文件", 8, "doc1")]
    assert provider.requests


def test_scoped_ask_reports_missing_document(tmp_path, monkeypatch):
    provider = FakeProvider()
    use_provider(monkeypatch, provider)

    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client)
        response = client.post(
            "/api/v1/ask",
            json={"question": "总结", "document_id": "missing"},
            headers=HEADERS,
        )

    assert event_names(response.text) == ["error"]
    assert "DOCUMENT_NOT_FOUND" in response.text
    assert provider.requests == []
