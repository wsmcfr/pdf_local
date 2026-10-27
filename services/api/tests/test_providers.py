import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers import secret_store

HEADERS = {"X-App-Token": "secret"}

BASE_PAYLOAD = {
    "name": "DeepSeek",
    "kind": "openai_chat",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
}


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
    with TestClient(app) as test_client:
        yield test_client


def create_provider(client, **overrides) -> dict:
    payload = {**BASE_PAYLOAD, **overrides}
    response = client.post("/api/v1/providers", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_create_provider_hides_key(client, keyring):
    response = client.post(
        "/api/v1/providers",
        headers=HEADERS,
        json={**BASE_PAYLOAD, "api_key": "sk-test"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["has_key"] is True
    assert "api_key" not in body
    assert "key_ref" not in body
    assert "sk-test" not in response.text
    assert keyring == {f"provider:{body['id']}": "sk-test"}


def test_create_without_key_has_key_false(client, keyring):
    body = create_provider(client)

    assert body["has_key"] is False
    assert body["enabled"] is True
    assert body["extra"] == {}
    assert keyring == {}


def test_create_stores_extra_and_accepts_extra_json(client):
    body = create_provider(client, extra={"region": "cn"})
    assert body["extra"] == {"region": "cn"}
    assert "extra_json" not in body

    body = create_provider(client, extra_json='{"region": "us"}')
    assert body["extra"] == {"region": "us"}


def test_create_rejects_invalid_extra(client):
    response = client.post(
        "/api/v1/providers",
        headers=HEADERS,
        json={**BASE_PAYLOAD, "extra_json": "not-json"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "INVALID_EXTRA"


def test_list_and_detail_never_expose_secrets(client, keyring):
    with_key = create_provider(client, name="A", api_key="sk-a")
    without_key = create_provider(client, name="B")

    response = client.get("/api/v1/providers", headers=HEADERS)
    assert response.status_code == 200
    assert "sk-a" not in response.text
    assert "key_ref" not in response.text

    by_id = {item["id"]: item for item in response.json()}
    assert set(by_id) == {with_key["id"], without_key["id"]}
    assert by_id[with_key["id"]]["has_key"] is True
    assert by_id[without_key["id"]]["has_key"] is False
    assert all("api_key" not in item for item in by_id.values())

    detail = client.get(f"/api/v1/providers/{with_key['id']}", headers=HEADERS)
    assert detail.status_code == 200
    assert detail.json()["name"] == "A"
    assert detail.json()["has_key"] is True
    assert "sk-a" not in detail.text


def test_patch_updates_fields_and_key(client, keyring):
    created = create_provider(client, api_key="sk-old")

    response = client.patch(
        f"/api/v1/providers/{created['id']}",
        headers=HEADERS,
        json={
            "name": "Renamed",
            "model": "deepseek-reasoner",
            "enabled": False,
            "extra": {"note": "x"},
            "api_key": "sk-new",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed"
    assert body["model"] == "deepseek-reasoner"
    assert body["enabled"] is False
    assert body["extra"] == {"note": "x"}
    assert body["has_key"] is True
    assert "sk-new" not in response.text
    assert keyring[f"provider:{created['id']}"] == "sk-new"

    cleared = client.patch(
        f"/api/v1/providers/{created['id']}",
        headers=HEADERS,
        json={"api_key": ""},
    )
    assert cleared.status_code == 200
    assert cleared.json()["has_key"] is False
    assert keyring == {}


def test_patch_invalid_kind(client):
    created = create_provider(client)
    response = client.patch(
        f"/api/v1/providers/{created['id']}",
        headers=HEADERS,
        json={"kind": "ollama"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "INVALID_KIND"


def test_put_key_sets_and_validates(client, keyring):
    created = create_provider(client)

    response = client.put(
        f"/api/v1/providers/{created['id']}/key",
        headers=HEADERS,
        json={"api_key": "sk-put"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "has_key": True}
    assert "sk-put" not in response.text
    assert keyring[f"provider:{created['id']}"] == "sk-put"

    detail = client.get(f"/api/v1/providers/{created['id']}", headers=HEADERS)
    assert detail.json()["has_key"] is True

    empty = client.put(
        f"/api/v1/providers/{created['id']}/key",
        headers=HEADERS,
        json={"api_key": ""},
    )
    assert empty.status_code == 400
    assert empty.json()["detail"] == "API_KEY_REQUIRED"


def test_delete_removes_provider_and_key(client, keyring):
    created = create_provider(client, api_key="sk-del")

    response = client.delete(f"/api/v1/providers/{created['id']}", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["ok"] is True

    assert keyring == {}
    assert client.get("/api/v1/providers", headers=HEADERS).json() == []
    missing = client.get(f"/api/v1/providers/{created['id']}", headers=HEADERS)
    assert missing.status_code == 404


def test_invalid_kind_rejected_on_create(client):
    response = client.post(
        "/api/v1/providers",
        headers=HEADERS,
        json={**BASE_PAYLOAD, "kind": "ollama"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "INVALID_KIND"
    assert client.get("/api/v1/providers", headers=HEADERS).json() == []


def test_missing_required_fields_rejected(client):
    for field in ("name", "kind", "base_url"):
        payload = {**BASE_PAYLOAD}
        payload.pop(field)
        response = client.post("/api/v1/providers", headers=HEADERS, json=payload)
        assert response.status_code == 400, field
        assert response.json()["detail"] == f"{field.upper()}_REQUIRED"


def test_unknown_provider_returns_404(client):
    missing = "does-not-exist"
    assert (
        client.get(f"/api/v1/providers/{missing}", headers=HEADERS).status_code == 404
    )
    assert (
        client.patch(
            f"/api/v1/providers/{missing}", headers=HEADERS, json={"name": "x"}
        ).status_code
        == 404
    )
    assert (
        client.delete(f"/api/v1/providers/{missing}", headers=HEADERS).status_code
        == 404
    )
    assert (
        client.put(
            f"/api/v1/providers/{missing}/key",
            headers=HEADERS,
            json={"api_key": "sk-x"},
        ).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/providers/{missing}/models", headers=HEADERS).status_code
        == 404
    )
    assert (
        client.post(f"/api/v1/providers/{missing}/test", headers=HEADERS).status_code
        == 404
    )


def test_models_and_test_are_not_ready_until_adapters_land(client):
    created = create_provider(client)

    for path in ("models", "test"):
        response = client.post(
            f"/api/v1/providers/{created['id']}/{path}", headers=HEADERS
        )
        assert response.status_code == 501
        assert response.json()["detail"] == "ADAPTER_NOT_READY"


def test_keyring_failure_does_not_leak_or_persist(client, monkeypatch):
    def boom(name, value):
        raise RuntimeError("credential store unavailable")

    monkeypatch.setattr(secret_store, "set_secret", boom)

    response = client.post(
        "/api/v1/providers",
        headers=HEADERS,
        json={**BASE_PAYLOAD, "api_key": "sk-should-not-leak"},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "KEYRING_ERROR"
    assert "sk-should-not-leak" not in response.text
    assert client.get("/api/v1/providers", headers=HEADERS).json() == []


def test_providers_require_token(client):
    response = client.get("/api/v1/providers", headers={"X-App-Token": "wrong"})
    assert response.status_code == 401
