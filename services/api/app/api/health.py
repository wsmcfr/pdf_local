from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    # Liveness only: never probe external providers or local services here.
    return {
        "ok": True,
        "version": "0.1.0",
    }
