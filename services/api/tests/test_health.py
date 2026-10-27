from fastapi.testclient import TestClient

from app.main import app


def test_health_requires_token(monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    client = TestClient(app)

    assert client.get("/api/v1/health").status_code == 401
    assert (
        client.get(
            "/api/v1/health",
            headers={"X-App-Token": "wrong"},
        ).status_code
        == 401
    )

    response = client.get(
        "/api/v1/health",
        headers={"X-App-Token": "secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"] == "0.1.0"
    assert "ollama" not in body
    assert "providers" not in body
