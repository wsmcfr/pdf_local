from app.main import app
from fastapi.testclient import TestClient

HEADERS = {"X-App-Token": "secret"}


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    return TestClient(app)


def add_provider(client, provider_id="p1", name="DeepSeek", key_ref=None, enabled=1):
    conn = client.app.state.db
    conn.execute(
        "INSERT INTO providers"
        "(id, name, kind, base_url, model, enabled, key_ref, extra_json,"
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            provider_id,
            name,
            "openai_chat",
            "https://api.example.invalid/v1",
            "deepseek-chat",
            enabled,
            key_ref,
            "{}",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()


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


def test_models_status_reports_missing_embedding_and_providers(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.get("/api/v1/models/status", headers=HEADERS)

    body = response.json()
    assert body["embedding"] == {
        "ready": False,
        "model": "BAAI/bge-m3",
        "path": str(tmp_path / "models" / "bge-m3"),
        "source": "BAAI/bge-m3",
    }
    assert body["providers"] == {
        "configured": False,
        "enabled": False,
        "items": [],
    }
    assert "llm" not in body


def test_models_status_detects_local_embedding(tmp_path, monkeypatch):
    embedding_dir = tmp_path / "models" / "bge-m3"
    embedding_dir.mkdir(parents=True)
    (embedding_dir / "config.json").write_text("{}", encoding="utf-8")

    with make_client(tmp_path, monkeypatch) as client:
        body = client.get("/api/v1/models/status", headers=HEADERS).json()

    assert body["embedding"]["ready"] is True
    assert body["embedding"]["source"] == str(embedding_dir)


def test_models_status_lists_providers_without_secrets(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client, provider_id="p1", name="DeepSeek", key_ref="provider:p1")
        add_provider(client, provider_id="p2", name="Kimi", enabled=0)
        body = client.get("/api/v1/models/status", headers=HEADERS).json()
        raw = client.get("/api/v1/models/status", headers=HEADERS).text

    providers = body["providers"]
    assert providers["items"] == [
        {
            "id": "p1",
            "name": "DeepSeek",
            "kind": "openai_chat",
            "model": "deepseek-chat",
            "has_key": True,
        },
        {
            "id": "p2",
            "name": "Kimi",
            "kind": "openai_chat",
            "model": "deepseek-chat",
            "has_key": False,
        },
    ]
    assert providers["configured"] is True
    assert providers["enabled"] is True
    assert "provider:p1" not in raw
    assert "api_key" not in raw


def test_models_status_reports_selected_disabled_provider(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        add_provider(client, provider_id="p1", name="Disabled", enabled=0)
        client.app.state.settings["llm_provider_id"] = "p1"
        body = client.get("/api/v1/models/status", headers=HEADERS).json()

    assert body["providers"]["configured"] is True
    assert body["providers"]["enabled"] is False
    assert body["providers"]["items"][0]["id"] == "p1"


def test_models_download_returns_bge_m3_steps_without_downloading(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post("/api/v1/models/download", json={}, headers=HEADERS)

    body = response.json()
    assert response.status_code == 200
    assert body["ok"] is True
    assert body["downloads_started"] is False
    assert any("download_embedding.py" in step for step in body["steps"])
    assert len(body["steps"]) == 1
    assert "download-models.ps1" in body["notes"]
    assert "ollama" not in response.text.lower()
    assert "OLLAMA" not in response.text
