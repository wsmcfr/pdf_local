import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.ask import router as ask_router
from .api.deps import require_token
from .api.documents import router as documents_router
from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .api.models import router as models_router
from .api.providers import router as providers_router
from .api.settings import DEFAULTS
from .api.settings import router as settings_router
from .db import connect, init_db
from .index.vector_store import VectorStore
from .jobs.runner import JobRunner
from .qa.retriever import HybridRetriever


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("KB_DATA_DIR") or (Path.home() / ".local-kb"))
    data_dir.mkdir(parents=True, exist_ok=True)
    app.state.data_dir = data_dir
    # Prefer a locally downloaded BGE-M3 copy; embeddings falls back to the hub
    # model name when this directory is absent.
    previous_embedding_path = os.environ.get("KB_EMBEDDING_MODEL_PATH")
    if not previous_embedding_path:
        os.environ["KB_EMBEDDING_MODEL_PATH"] = str(data_dir / "models" / "bge-m3")
    conn = connect(data_dir / "app.db")
    init_db(conn)
    app.state.db = conn
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    app.state.settings = {
        **DEFAULTS,
        **{row["key"]: json.loads(row["value"]) for row in rows},
    }
    vector_store = VectorStore(data_dir / "vectors.faiss")
    app.state.vector_store = vector_store
    app.state.retriever = HybridRetriever(conn, vector_store)
    app.state.runner = JobRunner(conn, vector_store)
    app.state.runner.start()
    try:
        yield
    finally:
        app.state.runner.stop()
        conn.close()
        if previous_embedding_path is None:
            os.environ.pop("KB_EMBEDDING_MODEL_PATH", None)


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
app.include_router(
    ask_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
app.include_router(
    models_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
app.include_router(
    providers_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
