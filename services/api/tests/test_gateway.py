"""Tests for the local gateway configuration API and MCP toggle."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.providers import secret_store

HEADERS = {"X-App-Token": "secret"}
GATEWAY_TOKEN = "gateway-secret"

PROVIDER_PAYLOAD = {
    "name": "DeepSeek",
    "kind": "openai_chat",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
}


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
def client(tmp_path, monkeypatch, keyring):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KB_GATEWAY_TOKEN", GATEWAY_TOKEN)
    with TestClient(app) as test_client:
        yield test_client


def create_provider(client) -> dict:
    response = client.post("/api/v1/providers", headers=HEADERS, json=PROVIDER_PAYLOAD)
    assert response.status_code == 200, response.text
    return response.json()


def test_get_gateway_config_defaults(client):
    response = client.get("/api/v1/gateway/config", headers=HEADERS)

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "rag_mode": "auto",
        "provider_id": "",
        "mcp_enabled": True,
        "token": GATEWAY_TOKEN,
    }


def test_patch_gateway_config_updates_settings(client):
    provider = create_provider(client)

    response = client.patch(
        "/api/v1/gateway/config",
        headers=HEADERS,
        json={
            "enabled": True,
            "mcp_enabled": False,
            "rag_mode": "tools",
            "provider_id": provider["id"],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["mcp_enabled"] is False
    assert body["rag_mode"] == "tools"
    assert body["provider_id"] == provider["id"]
    assert body["token"] == GATEWAY_TOKEN

    # The running app keeps the same settings dict for the gateway/middleware.
    assert client.app.state.settings["gateway_enabled"] is True
    assert client.app.state.settings["mcp_enabled"] is False
    assert client.app.state.settings["gateway_rag_mode"] == "tools"
    assert client.app.state.settings["llm_provider_id"] == provider["id"]

    persisted = client.get("/api/v1/settings", headers=HEADERS).json()
    assert persisted["gateway_enabled"] is True
    assert persisted["mcp_enabled"] is False
    assert persisted["gateway_rag_mode"] == "tools"
    assert persisted["llm_provider_id"] == provider["id"]


def test_patch_gateway_config_validates_types(client):
    cases = [
        ({"enabled": "yes"}, "INVALID_ENABLED"),
        ({"mcp_enabled": 1}, "INVALID_MCP_ENABLED"),
        ({"rag_mode": "sometimes"}, "INVALID_RAG_MODE"),
        ({"rag_mode": 1}, "INVALID_RAG_MODE"),
        ({"provider_id": 123}, "INVALID_PROVIDER_ID"),
    ]
    for payload, detail in cases:
        response = client.patch("/api/v1/gateway/config", headers=HEADERS, json=payload)
        assert response.status_code == 400, payload
        assert response.json()["detail"] == detail


def test_patch_gateway_config_requires_existing_provider(client):
    response = client.patch(
        "/api/v1/gateway/config",
        headers=HEADERS,
        json={"provider_id": "missing-provider"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "PROVIDER_NOT_FOUND"


def test_patch_gateway_config_allows_clearing_provider(client):
    provider = create_provider(client)
    client.patch(
        "/api/v1/gateway/config",
        headers=HEADERS,
        json={"provider_id": provider["id"]},
    )

    response = client.patch(
        "/api/v1/gateway/config", headers=HEADERS, json={"provider_id": ""}
    )

    assert response.status_code == 200
    assert response.json()["provider_id"] == ""


def test_gateway_config_requires_app_token(client):
    response = client.get("/api/v1/gateway/config", headers={"X-App-Token": "wrong"})
    assert response.status_code == 401


def test_rotate_token_writes_keyring_and_updates_config(client, keyring, monkeypatch):
    monkeypatch.delenv("KB_GATEWAY_TOKEN")
    keyring["gateway-token"] = "old-token"

    response = client.post("/api/v1/gateway/token/rotate", headers=HEADERS)

    assert response.status_code == 200
    token = response.json()["token"]
    assert token and token != "old-token"
    assert token != GATEWAY_TOKEN
    assert keyring["gateway-token"] == token
    assert client.get("/api/v1/gateway/config", headers=HEADERS).json()["token"] == token


def test_rotate_token_rejected_when_env_managed(client, keyring):
    response = client.post("/api/v1/gateway/token/rotate", headers=HEADERS)

    assert response.status_code == 400
    assert response.json()["detail"] == "TOKEN_ENV_MANAGED"
    assert "gateway-token" not in keyring


def test_mcp_disabled_returns_403(client):
    client.patch(
        "/api/v1/gateway/config",
        headers=HEADERS,
        json={"enabled": True, "mcp_enabled": False},
    )

    response = client.get(
        "/mcp/sse", headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "MCP_DISABLED"


def test_mcp_enabled_allows_authenticated_requests(client):
    client.patch(
        "/api/v1/gateway/config",
        headers=HEADERS,
        json={"enabled": True, "mcp_enabled": True},
    )

    response = client.post(
        "/mcp/messages/",
        headers={
            "Host": "127.0.0.1:8000",
            "Authorization": f"Bearer {GATEWAY_TOKEN}",
            "Content-Type": "application/json",
        },
        json={},
    )

    # Auth passed; the SSE transport rejects the session-less message.
    assert response.status_code == 400
    assert "session_id" in response.text


def test_provider_presets_are_available(client):
    response = client.get("/api/v1/providers/presets", headers=HEADERS)

    assert response.status_code == 200
    presets = response.json()
    assert presets
    ids = {preset["id"] for preset in presets}
    assert "openai" in ids
    assert all(
        {"id", "name", "kind", "base_url", "default_model"} <= set(preset)
        for preset in presets
    )
