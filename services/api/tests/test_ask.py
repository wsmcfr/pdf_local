import app.qa.answer as answer_module
from app.llm.ollama import OllamaUnavailable
from app.main import app
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


class FakeRetriever:
    def __init__(self, contexts):
        self.contexts = contexts
        self.calls = []

    def search(self, question, limit=8):
        self.calls.append((question, limit))
        return self.contexts


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


def test_ask_streams_retrieval_tokens_and_citations(tmp_path, monkeypatch):
    async def fake_stream(model, messages):
        assert messages[0]["role"] == "system"
        assert "报告.pdf p.12" in messages[1]["content"]
        yield "营收增长 20%"
        yield " [报告.pdf p.12]。"

    monkeypatch.setattr(answer_module, "stream_chat", fake_stream)
    retriever = FakeRetriever(CONTEXTS)

    with make_client(tmp_path, monkeypatch) as client:
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
    assert "营收增长 20%" in body
    assert retriever.calls == [("营收增长多少？", 8)]


def test_ask_without_evidence_returns_no_answer_without_calling_ollama(
    tmp_path, monkeypatch
):
    async def must_not_call(model, messages):
        raise AssertionError("Ollama must not be called without evidence")
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(answer_module, "stream_chat", must_not_call)
    retriever = FakeRetriever([])

    with make_client(tmp_path, monkeypatch) as client:
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
    assert retriever.calls == [("知识库里有什么？", 8)]


def test_ask_reports_ollama_unavailable_as_sse_error(tmp_path, monkeypatch):
    async def unavailable(model, messages):
        raise OllamaUnavailable("connection refused")
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(answer_module, "stream_chat", unavailable)
    retriever = FakeRetriever(CONTEXTS)

    with make_client(tmp_path, monkeypatch) as client:
        app.state.retriever = retriever
        response = client.post(
            "/api/v1/ask",
            json={"question": "营收增长多少？"},
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert event_names(response.text) == ["retrieval", "error"]
    assert "OLLAMA_UNAVAILABLE" in response.text


def test_ask_requires_question(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/ask", json={}, headers=HEADERS)

    assert response.status_code == 200
    assert event_names(response.text) == ["error"]
    assert "QUESTION_REQUIRED" in response.text
