"""Stdio entry point for the knowledge base MCP server.

Trae (and other MCP clients) can launch this with::

    .\\.venv\\Scripts\\python.exe -m app.mcp_stdio

The process reads ``KB_DATA_DIR`` (falling back to ``~/.local-kb``), mirrors the
minimal part of the FastAPI lifespan needed by the tools, and serves JSON-RPC
over stdin/stdout.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from .api.settings import DEFAULTS
from .db import connect, init_db
from .index.embedding_service import EmbeddingService
from .index.vector_store import VectorStore
from .mcp_server import create_mcp_server
from .paths import configure_huggingface_cache, default_data_dir
from .qa.retriever import HybridRetriever


def build_state(data_dir: Path) -> SimpleNamespace:
    """Build the subset of ``app.state`` the MCP tools need."""
    data_dir = Path(data_dir)
    configure_huggingface_cache(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if not os.environ.get("KB_EMBEDDING_MODEL_PATH"):
        os.environ["KB_EMBEDDING_MODEL_PATH"] = str(
            data_dir / "models" / "bge-m3"
        )

    conn = connect(data_dir / "app.db")
    init_db(conn)
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = {
        **DEFAULTS,
        **{row["key"]: json.loads(row["value"]) for row in rows},
    }

    vector_store = VectorStore(data_dir / "vectors.faiss")
    embedding_service = EmbeddingService(conn, settings)
    retriever = HybridRetriever(
        conn, vector_store, encode_fn=embedding_service.embed
    )

    return SimpleNamespace(
        data_dir=data_dir,
        db=conn,
        settings=settings,
        vector_store=vector_store,
        embedding_service=embedding_service,
        retriever=retriever,
    )


def main() -> None:
    data_dir = default_data_dir()
    state = build_state(data_dir)
    server = create_mcp_server(lambda: state)
    try:
        server.run(transport="stdio")
    finally:
        state.db.close()


if __name__ == "__main__":
    main()
