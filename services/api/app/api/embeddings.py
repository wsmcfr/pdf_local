from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request

from ..index.embedding_service import MODES
from ..index.embeddings import MODEL_NAME

logger = logging.getLogger(__name__)

router = APIRouter()


def _settings(request: Request) -> dict:
    return getattr(request.app.state, "settings", {}) or {}


def _providers(request: Request) -> list[dict]:
    rows = request.app.state.db.execute(
        "SELECT id, name FROM providers ORDER BY created_at ASC"
    ).fetchall()
    return [{"id": row["id"], "name": row["name"]} for row in rows]


def _config(request: Request) -> dict:
    settings = _settings(request)
    service = getattr(request.app.state, "embedding_service", None)
    if service is not None and hasattr(service, "info"):
        body = dict(service.info())
    else:
        body = {
            "mode": settings.get("embedding_mode") or "local",
            "provider_id": settings.get("embedding_provider_id") or "",
            "model": settings.get("embedding_model") or MODEL_NAME,
            "local_model": MODEL_NAME,
            "ok": True,
            "status": "ready",
        }

    body.setdefault("mode", "local")
    body.setdefault("provider_id", settings.get("embedding_provider_id") or "")
    body.setdefault("model", settings.get("embedding_model") or MODEL_NAME)
    body.setdefault("local_model", MODEL_NAME)
    body.setdefault("ok", True)
    body.setdefault("status", "ready")
    body["providers"] = _providers(request)
    return body


def _store_setting(conn, key: str, value) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value)),
    )


@router.get("/embedding/config")
def get_embedding_config(request: Request) -> dict:
    return _config(request)


@router.patch("/embedding/config")
def patch_embedding_config(payload: dict, request: Request) -> dict:
    mode = payload.get("mode")
    if not isinstance(mode, str) or not mode.strip():
        raise HTTPException(status_code=400, detail="MODE_REQUIRED")
    mode = mode.strip().lower()
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="INVALID_MODE")

    settings = _settings(request)
    provider_id = payload.get("provider_id", settings.get("embedding_provider_id", ""))
    model = payload.get("model", settings.get("embedding_model", ""))
    if provider_id is None:
        provider_id = ""
    if model is None:
        model = ""
    if not isinstance(provider_id, str) or not isinstance(model, str):
        raise HTTPException(status_code=400, detail="INVALID_CONFIG")

    conn = request.app.state.db
    if mode == "external":
        if not provider_id.strip():
            raise HTTPException(status_code=400, detail="PROVIDER_REQUIRED")
        row = conn.execute(
            "SELECT id FROM providers WHERE id = ?", (provider_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="PROVIDER_NOT_FOUND")
        if not model.strip():
            raise HTTPException(status_code=400, detail="MODEL_REQUIRED")

    updates = {"embedding_mode": mode}
    if "provider_id" in payload:
        updates["embedding_provider_id"] = provider_id
    if "model" in payload:
        updates["embedding_model"] = model

    for key, value in updates.items():
        _store_setting(conn, key, value)
    conn.commit()

    # The service holds a reference to this dict, so update it in place.
    if isinstance(settings, dict):
        settings.update(updates)

    return _config(request)


@router.post("/embedding/test")
def test_embedding(request: Request, payload: dict | None = None) -> dict:
    text = "ping"
    if isinstance(payload, dict):
        value = payload.get("text")
        if isinstance(value, str) and value.strip():
            text = value

    service = getattr(request.app.state, "embedding_service", None)
    mode = getattr(service, "mode", "local") if service is not None else "local"
    if service is None:
        return {"ok": False, "mode": mode, "fallback": "fts"}

    try:
        if hasattr(service, "test"):
            return service.test(text)
        vectors = service.embed([text])
    except Exception:  # noqa: BLE001 - the endpoint must never leak errors/keys
        logger.warning("embedding test failed", exc_info=True)
        return {"ok": False, "mode": mode, "fallback": "fts"}

    if not vectors or not vectors[0]:
        return {"ok": False, "mode": mode, "fallback": "fts"}
    return {"ok": True, "mode": mode, "dimension": len(vectors[0])}
