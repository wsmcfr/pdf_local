from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..providers import errors, factory, registry, secret_store
from ..providers.base import ChatMessage, ChatRequest

router = APIRouter()

ALLOWED_KINDS = {
    "openai_chat",
    "openai_responses",
    "anthropic",
    "gemini",
    "custom",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_dict(row) -> dict:
    try:
        extra = json.loads(row["extra_json"] or "{}")
    except (TypeError, ValueError):
        extra = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "base_url": row["base_url"],
        "model": row["model"],
        "enabled": bool(row["enabled"]),
        "has_key": bool(row["key_ref"]),
        "extra": extra,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_row(provider_id: str, request: Request):
    row = request.app.state.db.execute(
        "SELECT * FROM providers WHERE id = ?", (provider_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="PROVIDER_NOT_FOUND")
    return row


def _require_text(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=400, detail=f"{field.upper()}_REQUIRED")
    return value.strip()


def _validate_kind(kind: str) -> str:
    if kind not in ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail="INVALID_KIND")
    return kind


def _parse_extra(payload: dict) -> str:
    if "extra" in payload:
        extra = payload.get("extra")
        if extra is None:
            extra = {}
        if not isinstance(extra, dict):
            raise HTTPException(status_code=400, detail="INVALID_EXTRA")
        return json.dumps(extra, ensure_ascii=False)

    raw = payload.get("extra_json")
    if raw is None:
        return "{}"
    if isinstance(raw, dict):
        return json.dumps(raw, ensure_ascii=False)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="INVALID_EXTRA") from exc
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=400, detail="INVALID_EXTRA")
        return json.dumps(parsed, ensure_ascii=False)
    raise HTTPException(status_code=400, detail="INVALID_EXTRA")


def _write_key(provider_id: str, api_key: str) -> None:
    try:
        secret_store.set_secret(
            secret_store.provider_key_name(provider_id), api_key
        )
    except Exception as exc:  # noqa: BLE001 - never echo the secret back
        raise HTTPException(status_code=500, detail="KEYRING_ERROR") from exc


def _remove_key(provider_id: str) -> None:
    try:
        secret_store.delete_secret(secret_store.provider_key_name(provider_id))
    except Exception as exc:  # noqa: BLE001 - never echo the secret back
        raise HTTPException(status_code=500, detail="KEYRING_ERROR") from exc


def _error_code(exc: Exception) -> str:
    if isinstance(exc, errors.ProviderAuthError):
        return "PROVIDER_AUTH_ERROR"
    if isinstance(exc, errors.ProviderRateLimitError):
        return "PROVIDER_RATE_LIMIT"
    if isinstance(exc, errors.ProviderTimeoutError):
        return "PROVIDER_TIMEOUT"
    if isinstance(exc, errors.ProviderNotFoundError):
        return "PROVIDER_ENDPOINT_NOT_FOUND"
    if isinstance(exc, errors.ProviderConfigError):
        return "INVALID_PROVIDER_CONFIG"
    return "PROVIDER_ERROR"


def _error_status(exc: Exception) -> int:
    if isinstance(exc, errors.ProviderRateLimitError):
        return 429
    if isinstance(exc, errors.ProviderTimeoutError):
        return 504
    if isinstance(exc, errors.ProviderConfigError):
        return 400
    return 502


def _provider_error(exc: errors.ProviderError) -> HTTPException:
    return HTTPException(status_code=_error_status(exc), detail=_error_code(exc))


def _load_config(row):
    try:
        api_key = registry.load_key(row["id"])
    except Exception as exc:  # noqa: BLE001 - never echo the secret back
        raise HTTPException(status_code=500, detail="KEYRING_ERROR") from exc

    config = registry.config_from_row(row, api_key)
    if not config.base_url.strip():
        raise HTTPException(status_code=400, detail="PROVIDER_NOT_CONFIGURED")
    if not config.api_key:
        raise HTTPException(status_code=400, detail="PROVIDER_KEY_REQUIRED")
    return config


def _build_provider(config):
    try:
        return factory.create_provider(config)
    except errors.ProviderConfigError as exc:
        raise HTTPException(
            status_code=400, detail="INVALID_PROVIDER_CONFIG"
        ) from exc


@router.get("/providers")
def list_providers(request: Request) -> list[dict]:
    rows = request.app.state.db.execute(
        "SELECT * FROM providers ORDER BY created_at ASC"
    ).fetchall()
    return [_row_dict(row) for row in rows]


@router.get("/providers/{provider_id}")
def get_provider(provider_id: str, request: Request) -> dict:
    return _row_dict(_get_row(provider_id, request))


@router.post("/providers")
def create_provider(payload: dict, request: Request) -> dict:
    name = _require_text(payload, "name")
    kind = _validate_kind(_require_text(payload, "kind"))
    base_url = _require_text(payload, "base_url")

    model = payload.get("model", "")
    if not isinstance(model, str):
        raise HTTPException(status_code=400, detail="INVALID_MODEL")
    enabled = bool(payload.get("enabled", True))
    extra_json = _parse_extra(payload)

    api_key = payload.get("api_key")
    if api_key is not None and not isinstance(api_key, str):
        raise HTTPException(status_code=400, detail="INVALID_API_KEY")

    provider_id = uuid.uuid4().hex
    key_ref = None
    if api_key:
        _write_key(provider_id, api_key)
        key_ref = secret_store.provider_key_name(provider_id)

    now = _now()
    conn = request.app.state.db
    try:
        conn.execute(
            "INSERT INTO providers"
            "(id, name, kind, base_url, model, enabled, key_ref, extra_json,"
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                provider_id,
                name,
                kind,
                base_url,
                model,
                int(enabled),
                key_ref,
                extra_json,
                now,
                now,
            ),
        )
        conn.commit()
    except Exception:
        if key_ref:
            _remove_key(provider_id)
        raise

    return _row_dict(_get_row(provider_id, request))


@router.patch("/providers/{provider_id}")
def update_provider(provider_id: str, payload: dict, request: Request) -> dict:
    row = _get_row(provider_id, request)
    conn = request.app.state.db

    updates: dict[str, object] = {}
    if "name" in payload:
        updates["name"] = _require_text(payload, "name")
    if "kind" in payload:
        updates["kind"] = _validate_kind(_require_text(payload, "kind"))
    if "base_url" in payload:
        updates["base_url"] = _require_text(payload, "base_url")
    if "model" in payload:
        model = payload.get("model")
        if not isinstance(model, str):
            raise HTTPException(status_code=400, detail="INVALID_MODEL")
        updates["model"] = model
    if "enabled" in payload:
        updates["enabled"] = int(bool(payload.get("enabled")))
    if "extra" in payload or "extra_json" in payload:
        updates["extra_json"] = _parse_extra(payload)

    key_ref = row["key_ref"]
    if "api_key" in payload:
        api_key = payload.get("api_key")
        if not isinstance(api_key, str):
            raise HTTPException(status_code=400, detail="INVALID_API_KEY")
        if api_key == "":
            _remove_key(provider_id)
            key_ref = None
        else:
            _write_key(provider_id, api_key)
            key_ref = secret_store.provider_key_name(provider_id)
        updates["key_ref"] = key_ref

    if updates:
        updates["updated_at"] = _now()
        columns = ", ".join(f"{column} = ?" for column in updates)
        conn.execute(
            f"UPDATE providers SET {columns} WHERE id = ?",
            (*updates.values(), provider_id),
        )
        conn.commit()

    return _row_dict(_get_row(provider_id, request))


@router.delete("/providers/{provider_id}")
def delete_provider(provider_id: str, request: Request) -> dict:
    _get_row(provider_id, request)
    _remove_key(provider_id)
    conn = request.app.state.db
    conn.execute("DELETE FROM model_cache WHERE provider_id = ?", (provider_id,))
    conn.execute("DELETE FROM providers WHERE id = ?", (provider_id,))
    conn.commit()
    return {"ok": True, "id": provider_id}


@router.put("/providers/{provider_id}/key")
def put_provider_key(provider_id: str, payload: dict, request: Request) -> dict:
    _get_row(provider_id, request)
    api_key = payload.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(status_code=400, detail="API_KEY_REQUIRED")

    _write_key(provider_id, api_key)
    conn = request.app.state.db
    conn.execute(
        "UPDATE providers SET key_ref = ?, updated_at = ? WHERE id = ?",
        (secret_store.provider_key_name(provider_id), _now(), provider_id),
    )
    conn.commit()
    return {"ok": True, "has_key": True}


def _cache_models(request: Request, provider_id: str, models: list) -> None:
    payload = [
        {"id": model.id, "name": model.name or model.id} for model in models
    ]
    conn = request.app.state.db
    conn.execute(
        "INSERT INTO model_cache(provider_id, models_json, fetched_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(provider_id) DO UPDATE SET "
        "models_json = excluded.models_json, fetched_at = excluded.fetched_at",
        (provider_id, json.dumps(payload, ensure_ascii=False), _now()),
    )
    conn.commit()


@router.post("/providers/{provider_id}/models")
async def probe_models(provider_id: str, request: Request) -> dict:
    row = _get_row(provider_id, request)
    config = _load_config(row)
    provider = _build_provider(config)

    try:
        models = await provider.list_models()
    except errors.ProviderError as exc:
        raise _provider_error(exc) from exc

    _cache_models(request, provider_id, models)
    return {
        "models": [
            {"id": model.id, "name": model.name or model.id} for model in models
        ]
    }


@router.post("/providers/{provider_id}/test", response_model=None)
async def test_provider(provider_id: str, request: Request) -> dict | JSONResponse:
    row = _get_row(provider_id, request)
    config = _load_config(row)
    if not config.model.strip():
        raise HTTPException(status_code=400, detail="MODEL_REQUIRED")
    provider = _build_provider(config)

    chat_request = ChatRequest(
        model=config.model,
        messages=[ChatMessage(role="user", content="ping")],
        stream=True,
    )
    started = time.perf_counter()
    try:
        async for chunk in provider.stream_chat(chat_request):
            if chunk.type == "error":
                raise errors.ProviderError(chunk.error or "provider error")
    except errors.ProviderError as exc:
        latency_ms = round((time.perf_counter() - started) * 1000, 1)
        return JSONResponse(
            status_code=_error_status(exc),
            content={
                "ok": False,
                "model": config.model,
                "latency_ms": latency_ms,
                "error": _error_code(exc),
            },
        )

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    return {"ok": True, "model": config.model, "latency_ms": latency_ms}
