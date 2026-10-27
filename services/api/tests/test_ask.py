import app.api.ask as ask_module
from app.main import app
from app.providers import errors
from app.providers.base import ChatChunk, ProviderConfig
from fastapi.testclient import TestClient

CONTEXTS = [
    {
        "chunk_id": "c1",
        "document_id": "d1",
        "page_no": 12,
        "text": "营收增长 20%。",
        "file_name": "报告.pdf",
        "mime": "application/pdf",
        "score": 0.03,
    }
]

HEADERS = {"X-App-Token": "secret"}
MODEL = "test-model"


class FakeRetriever:
    def __init__(self, contexts):
        self.contexts = contexts
        self.calls = []

    def search(self, question, limit=8):
        self.calls.append((question, limit))
        return self.contexts


class FakeProvider:
    def __init__(self, chunks=None, error=None):
        self.chunks = chunks or []
        self.error = error
        self.requests = []

    async def list_models(self):
        return []

    async def stream_chat(self, request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk

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


def add_provider(client, model=MODEL):
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
            model,
            1,
            None,
            "{}",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()


def use_provider(monkeypatch, provider, model=MODEL):
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


def test_ask_streams_retrieval_tokens_and_citations(tmp_path, monkeypatch):
    provider = FakeProvider(
        chunks=[
            ChatChunk(type="text", text="营收增长 20%"),
            ChatChunk(type="text", text=" [报告.pdf p.12]。"),
        ]
    )
    use_provider(monkeypatch, provider)
    retriever = FakeRetriever(CONTEXTS)

    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client)
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "营收增长多少？"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert event_names(body) == ["retrieval", "token", "token", "citations", "done"]
    assert '"page_no": 12' in body
    assert '"kind": "pdf"' in body
    assert 'data: {"text": "营收增长 20%"}' in body
    assert '"model": "test-model"' in body
    assert retriever.calls == [("营收增长多少？", 8)]

    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.model == MODEL
    assert request.stream is True
    assert [message.role for message in request.messages] == ["system", "user"]
    assert "报告.pdf p.12" in request.messages[1].content
    assert "营收增长多少？" in request.messages[1].content


def test_ask_without_evidence_returns_no_answer_without_calling_provider(
    tmp_path, monkeypatch
):
    provider = FakeProvider()
    use_provider(monkeypatch, provider)
    retriever = FakeRetriever([])

    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client)
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "知识库里有什么？"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    body = response.text
    assert event_names(body) == ["retrieval", "token", "citations", "done"]
    assert "资料中没有找到" in body
    assert 'data: {"text": "资料中没有找到。"}' in body
    assert "event: error" not in body
    assert provider.requests == []
    assert retriever.calls == [("知识库里有什么？", 8)]


def test_ask_without_provider_returns_sse_error(tmp_path, monkeypatch):
    retriever = FakeRetriever(CONTEXTS)

    with make_client(tmp_path, monkeypatch) as client:
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "营收增长多少？"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert event_names(response.text) == ["error"]
    assert "PROVIDER_NOT_CONFIGURED" in response.text
    assert retriever.calls == []


def test_ask_maps_provider_auth_error_to_sse_code(tmp_path, monkeypatch):
    provider = FakeProvider(error=errors.ProviderAuthError("invalid key sk-secret"))
    use_provider(monkeypatch, provider)
    retriever = FakeRetriever(CONTEXTS)

    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client)
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "营收增长多少？"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert event_names(response.text) == ["retrieval", "error"]
    assert "UPSTREAM_AUTH_ERROR" in response.text
    assert "Provider 密钥无效或没有访问权限。" in response.text
    assert "sk-secret" not in response.text


def test_ask_requires_question(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/ask", json={}, headers=HEADERS)

    assert response.status_code == 200
    assert event_names(response.text) == ["error"]
    assert "QUESTION_REQUIRED" in response.text
