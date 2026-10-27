from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Request

router = APIRouter()

OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_LLM_MODEL = "qwen2.5:7b"
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


async def fetch_ollama_tags() -> list[str] | None:
    """Return installed Ollama model names, or ``None`` when Ollama is not reachable."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            response = await client.get(OLLAMA_TAGS_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception:  # noqa: BLE001 - not running Ollama is an expected state
        return None

    names: list[str] = []
    for item in payload.get("models") or []:
        name = item.get("name") or item.get("model")
        if name:
            names.append(str(name))
    return names


def _model_installed(target: str, installed: list[str]) -> bool:
    if target in installed:
        return True
    base = target.split(":", 1)[0]
    return any(name == f"{target}:latest" or name.split(":", 1)[0] == base for name in installed)


@router.get("/models/status")
async def models_status(request: Request) -> dict:
    settings = getattr(request.app.state, "settings", {})
    target = str(settings.get("llm_model") or DEFAULT_LLM_MODEL)
    installed_models = await fetch_ollama_tags()

    installed = installed_models is not None
    llm_ready = bool(installed_models) and _model_installed(target, installed_models or [])

    return {
        "ocr": ocr_status(),
        "embedding": embedding_status(request),
        "llm": {
            "ready": llm_ready,
            "installed": installed,
            "models": installed_models or [],
            "target": target,
            "error": None
            if installed
            else "无法连接 Ollama（127.0.0.1:11434）",
        },
    }


@router.post("/models/download")
async def download_models(request: Request, payload: dict | None = None) -> dict:
    """Return step-by-step instructions instead of starting a multi-GB download."""
    settings = getattr(request.app.state, "settings", {})
    target = str(settings.get("llm_model") or DEFAULT_LLM_MODEL)
    steps = [
        f"ollama pull {target}",
        f".\\.venv\\Scripts\\python.exe {EMBEDDING_SCRIPT}",
    ]
    return {
        "ok": True,
        "downloads_started": False,
        "steps": steps,
        "notes": (
            "本接口只返回安装指引，不会自动下载模型。"
            f"也可以直接运行 {MODELS_SCRIPT}（先安装并启动 Ollama）。"
        ),
        "requested": payload or {},
    }
