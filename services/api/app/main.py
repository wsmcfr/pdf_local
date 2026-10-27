import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import require_token
from .api.documents import router as documents_router
from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .api.settings import router as settings_router
from .db import connect, init_db
from .jobs.runner import JobRunner


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("KB_DATA_DIR") or (Path.home() / ".local-kb"))
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir / "app.db")
    init_db(conn)
    app.state.db = conn
    app.state.runner = JobRunner(conn)
    app.state.runner.start()
    try:
        yield
    finally:
        app.state.runner.stop()
        conn.close()


app = FastAPI(title="Local KB", version="0.1.0", lifespan=lifespan)

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
app.include_router(
    jobs_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
app.include_router(
    settings_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
app.include_router(
    documents_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
