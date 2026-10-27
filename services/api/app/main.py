from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import require_token
from .api.health import router as health_router

app = FastAPI(title="Local KB", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    health_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
