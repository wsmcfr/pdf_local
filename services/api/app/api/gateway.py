"""Local gateway configuration for the desktop settings UI.

These endpoints are part of the token-protected ``/api/v1`` surface. They never
log or echo anything except the locally visible gateway token, which the UI
needs in order to build client configuration snippets.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request

from . import gateway_auth

router = APIRouter()

RAG_MODES = {"auto", "off", "tools"}


def _settings(request: Request) -> dict:
    return getattr(request.app.state, "settings", {}) or {}


def _config(request: Request) -> dict:
    settings = _settings(request)
    return {
        "enabled": bool(settings.get("gateway_enabled", False)),
        "rag_mode": settings.get("gateway_rag_mode") or "auto",
        "provider_id": settings.get("llm_provider_id") or "",
        "mcp_enabled": bool(settings.get("mcp_enabled", True)),
        "token": gateway_auth.get_gateway_token(),
    }


def _store_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


@router.get("/gateway/config")
def get_gateway_config(request: Request) -> dict:
    return _config(request)


@router.patch("/gateway/config")
def patch_gateway_config(payload: dict, request: Request) -> dict:
    settings = _settings(request)
    conn = request.app.state.db
    updates: dict[str, object] = {}

    if "enabled" in payload:
        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise HTTPException(status_code=400, detail="INVALID_ENABLED")
        updates["gateway_enabled"] = enabled

    if "mcp_enabled" in payload:
        mcp_enabled = payload.get("mcp_enabled")
        if not isinstance(mcp_enabled, bool):
            raise HTTPException(status_code=400, detail="INVALID_MCP_ENABLED")
        updates["mcp_enabled"] = mcp_enabled

    if "rag_mode" in payload:
        rag_mode = payload.get("rag_mode")
        if not isinstance(rag_mode, str) or rag_mode.strip().lower() not in RAG_MODES:
            raise HTTPException(status_code=400, detail="INVALID_RAG_MODE")
        updates["gateway_rag_mode"] = rag_mode.strip().lower()

    if "provider_id" in payload:
        provider_id = payload.get("provider_id")
        if provider_id is None:
            provider_id = ""
        if not isinstance(provider_id, str):
            raise HTTPException(status_code=400, detail="INVALID_PROVIDER_ID")
        provider_id = provider_id.strip()
        if provider_id:
            row = conn.execute(
                "SELECT id FROM providers WHERE id = ?", (provider_id,)
            ).fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="PROVIDER_NOT_FOUND")
        updates["llm_provider_id"] = provider_id

    for key, value in updates.items():
        _store_setting(conn, key, value)
    if updates:
        conn.commit()
    # The middleware and inbound gateway read this dict by reference.
    if isinstance(settings, dict):
        settings.update(updates)

    return _config(request)


@router.post("/gateway/token/rotate")
def rotate_gateway_token(request: Request) -> dict:
    if (os.environ.get("KB_GATEWAY_TOKEN") or "").strip():
        raise HTTPException(status_code=400, detail="TOKEN_ENV_MANAGED")

    try:
        token = gateway_auth.rotate_gateway_token()
    except Exception as exc:  # noqa: BLE001 - never expose credential-store details
        raise HTTPException(status_code=500, detail="KEYRING_ERROR") from exc
    return {"token": token}
