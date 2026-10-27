from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, Request

from ..providers import registry

router = APIRouter()

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_SCRIPT = r".\scripts\download_embedding.py"
MODELS_SCRIPT = r".\scripts\download-models.ps1"


def ocr_status() -> dict:
    """RapidOCR ships its detection/recognition models inside the wheel."""
    try:
        import rapidocr_onnxruntime
    except Exception as exc:  # noqa: BLE001 - report import problems to the UI
        return {"ready": False, "engine": "rapidocr-onnxruntime", "error": str(exc)}

    package_file = getattr(rapidocr_onnxruntime, "__file__", None)
    bundled: list[str] = []
    if package_file:
        model_dir = Path(package_file).parent / "models"
        if model_dir.is_dir():
            bundled = sorted(path.name for path in model_dir.glob("*.onnx"))
    return {"ready": True, "engine": "rapidocr-onnxruntime", "models": bundled}


def embedding_path(request: Request) -> Path:
    override = os.environ.get("KB_EMBEDDING_MODEL_PATH")
    if override:
        return Path(override)
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir is None:
        data_dir = Path(os.environ.get("KB_DATA_DIR") or (Path.home() / ".local-kb"))
    return Path(data_dir) / "models" / "bge-m3"


def embedding_status(request: Request) -> dict:
    path = embedding_path(request)
    ready = path.is_dir() and any(path.iterdir())
    return {
        "ready": ready,
        # When the local copy is missing, embeddings.py falls back to this hub name,
        # which needs network access on first use (not offline ready).
        "model": EMBEDDING_MODEL,
        "path": str(path),
        "source": str(path) if ready else EMBEDDING_MODEL,
    }


def provider_items(request: Request) -> list[dict]:
    rows = request.app.state.db.execute(
        "SELECT id, name, kind, model, key_ref FROM providers "
        "ORDER BY created_at ASC, id ASC"
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "kind": row["kind"],
            "model": row["model"],
            "has_key": bool(row["key_ref"]),
        }
        for row in rows
    ]


def providers_status(request: Request) -> dict:
    settings = getattr(request.app.state, "settings", {}) or {}
    row = registry.select_provider(request.app.state.db, settings)
    return {
        "configured": row is not None,
        "enabled": bool(row["enabled"]) if row is not None else False,
        "items": provider_items(request),
    }


@router.get("/models/status")
async def models_status(request: Request) -> dict:
    return {
        "ocr": ocr_status(),
        "embedding": embedding_status(request),
        "providers": providers_status(request),
    }


@router.post("/models/download")
async def download_models(request: Request, payload: dict | None = None) -> dict:
    """Return step-by-step instructions instead of starting a multi-GB download."""
    steps = [f".\\.venv\\Scripts\\python.exe {EMBEDDING_SCRIPT}"]
    return {
        "ok": True,
        "downloads_started": False,
        "steps": steps,
        "notes": (
            "本接口只返回安装指引，不会自动下载模型。"
            f"也可以直接运行 {MODELS_SCRIPT}（仅下载 BGE-M3）。"
            "外部 Provider 问答需要网络，请在设置页配置 Provider。"
        ),
        "requested": payload or {},
    }
