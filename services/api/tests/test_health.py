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
    assert response.json()["ok"] is True
