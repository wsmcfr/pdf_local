import app.api.models as models_module
from app.main import app
from fastapi.testclient import TestClient

HEADERS = {"X-App-Token": "secret"}


async def no_ollama():
    return None


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_models_status_requires_token(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/models/status").status_code == 401
        assert (
            client.get(
                "/api/v1/models/status",
                headers={"X-App-Token": "wrong"},
            ).status_code
            == 401
        )

        response = client.get("/api/v1/models/status", headers=HEADERS)

    assert response.status_code == 200
    assert response.json()["ocr"]["ready"] is True


def test_models_status_reports_missing_models(tmp_path, monkeypatch):
    monkeypatch.setattr(models_module, "fetch_ollama_tags", no_ollama)

    with make_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/models/status", headers=HEADERS)

    body = response.json()
    assert body["embedding"]["ready"] is False
    assert body["embedding"]["path"] == str(tmp_path / "models" / "bge-m3")
    assert body["embedding"]["source"] == "BAAI/bge-m3"
    assert body["llm"] == {
        "ready": False,
        "installed": False,
        "models": [],
        "target": "qwen2.5:7b",
        "error": "无法连接 Ollama（127.0.0.1:11434）",
    }


def test_models_status_detects_local_embedding_and_llm(tmp_path, monkeypatch):
    embedding_dir = tmp_path / "models" / "bge-m3"
    embedding_dir.mkdir(parents=True)
    (embedding_dir / "config.json").write_text("{}", encoding="utf-8")

    async def with_ollama():
        return ["qwen2.5:7b", "nomic-embed-text:latest"]

    monkeypatch.setattr(models_module, "fetch_ollama_tags", with_ollama)

    with make_client(tmp_path, monkeypatch) as client:
        body = client.get("/api/v1/models/status", headers=HEADERS).json()

    assert body["embedding"]["ready"] is True
    assert body["embedding"]["source"] == str(embedding_dir)
    assert body["llm"]["ready"] is True
    assert body["llm"]["installed"] is True
    assert body["llm"]["models"] == ["qwen2.5:7b", "nomic-embed-text:latest"]


def test_models_status_llm_installed_but_model_missing(tmp_path, monkeypatch):
    async def other_models():
        return ["llama3.2:3b"]

    monkeypatch.setattr(models_module, "fetch_ollama_tags", other_models)

    with make_client(tmp_path, monkeypatch) as client:
        body = client.get("/api/v1/models/status", headers=HEADERS).json()

    assert body["llm"]["installed"] is True
    assert body["llm"]["ready"] is False
    assert body["llm"]["error"] is None


def test_models_download_returns_steps_without_downloading(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/models/download", json={}, headers=HEADERS)

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["downloads_started"] is False
    assert "ollama pull qwen2.5:7b" in body["steps"]
    assert any("download_embedding.py" in step for step in body["steps"])
    assert "download-models.ps1" in body["notes"]


def test_models_status_only_calls_local_ollama(tmp_path, monkeypatch):
    requested: list[str] = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"models": []}

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            requested.append(url)
            return FakeResponse()

    monkeypatch.setattr(models_module.httpx, "AsyncClient", FakeClient)

    with make_client(tmp_path, monkeypatch) as client:
        client.get("/api/v1/models/status", headers=HEADERS)

    assert requested == ["http://127.0.0.1:11434/api/tags"]
