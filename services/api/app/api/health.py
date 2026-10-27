import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    ollama = False
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            ollama = response.status_code == 200
    except Exception:
        ollama = False

    return {
        "ok": True,
        "ollama": ollama,
        "version": "0.1.0",
    }
