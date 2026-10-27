#!/usr/bin/env python3
"""Rebuild chunks and vectors for documents that were imported as FTS-only.

Documents imported before BGE-M3 finished downloading have chunks but no
``embedding_id``. This script reuses the already-extracted page text (no OCR
or PDF parsing), regenerates chunks and embeddings, and rewrites the FAISS
index. Close the desktop app first so the database and vector file are not in
use while it runs.

Usage::

    .\\.venv\\Scripts\\python.exe scripts\\reindex_embeddings.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.db import connect, init_db  # noqa: E402
from app.index.embeddings import encode, local_model_available  # noqa: E402
from app.index.indexer import index_document  # noqa: E402
from app.index.vector_store import VectorStore  # noqa: E402
from app.paths import configure_huggingface_cache, default_data_dir  # noqa: E402


def main() -> int:
    data_dir = default_data_dir()
    configure_huggingface_cache(data_dir)
    model_dir = data_dir / "models" / "bge-m3"
    os.environ.setdefault("KB_EMBEDDING_MODEL_PATH", str(model_dir))

    if not local_model_available():
        print(
            "local BGE-M3 is not ready. Run scripts/download_embedding.py first.",
            file=sys.stderr,
        )
        return 1

    print(f"loading {model_dir} and verifying a test embedding ...", flush=True)
    probe = encode(["ping"])
    if not probe or not probe[0]:
        print("the local model could not produce an embedding.", file=sys.stderr)
        return 1
    print(f"model ready ({len(probe[0])} dimensions)", flush=True)

    conn = connect(data_dir / "app.db")
    init_db(conn)
    store = VectorStore(data_dir / "vectors.faiss")

    rows = conn.execute(
        "SELECT id, file_name FROM documents "
        "WHERE status IN ('ready', 'pending', 'processing') "
        "ORDER BY file_name"
    ).fetchall()
    if not rows:
        print("no documents to re-index.")
        conn.close()
        return 0

    print(f"re-indexing {len(rows)} documents (this can take a while) ...", flush=True)
    done = 0
    for index, row in enumerate(rows, start=1):
        print(f"[{index}/{len(rows)}] {row['file_name']}", flush=True)
        try:
            completed = index_document(conn, store, row["id"])
        except Exception as exc:  # noqa: BLE001 - keep going with other documents
            print(f"  failed: {exc}", file=sys.stderr)
            continue
        if not completed:
            print("  stopped early", file=sys.stderr)
            continue
        done += 1

    embedded, total = conn.execute(
        "SELECT "
        "(SELECT COUNT(*) FROM chunks WHERE embedding_id IS NOT NULL), "
        "(SELECT COUNT(*) FROM chunks)"
    ).fetchone()
    print(
        f"done: {done}/{len(rows)} documents re-indexed; "
        f"{embedded}/{total} chunks have vectors; "
        f"FAISS entries: {store.index.ntotal}",
        flush=True,
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
