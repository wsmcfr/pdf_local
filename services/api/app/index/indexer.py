from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Callable, ContextManager, Sequence

from ..ingest.chunker import chunk_text
from .embeddings import encode
from .fts import delete_document_chunks, insert_chunk
from .vector_store import VectorStore

logger = logging.getLogger(__name__)

Checkpoint = Callable[[], bool]
EncodeFn = Callable[[list[str]], list[list[float]] | None]

EMBED_BATCH_SIZE = 8


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute(
        "SELECT value FROM index_meta WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str | int) -> None:
    """Upsert an index meta row. The caller owns the transaction."""
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def _guard(lock: threading.RLock | None) -> ContextManager:
    return lock if lock is not None else nullcontext()


def _load_pages(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT page_no, text FROM pages WHERE document_id = ? ORDER BY page_no",
        (doc_id,),
    ).fetchall()


def index_document(
    conn: sqlite3.Connection,
    vector_store: VectorStore,
    doc_id: str,
    encode_fn: EncodeFn | None = encode,
    checkpoint: Checkpoint | None = None,
    pages: Sequence[sqlite3.Row | dict] | None = None,
    lock: threading.RLock | None = None,
) -> bool:
    """Chunk, tokenize and embed every page of ``doc_id``.

    Previous chunks/FTS rows and their FAISS ids are removed first so that a
    re-import never leaves stale vectors behind. ``encode_fn`` is injected for
    tests (the default lazily loads BGE-M3). When it is ``None`` or returns
    ``None`` the chunks are still indexed in FTS with a ``NULL``
    ``embedding_id`` so retrieval can fall back to keyword search. Database
    writes are serialized with ``lock`` when given, while encoding runs outside
    the lock so that pause requests stay responsive. Returns ``False`` when a
    checkpoint asked to stop (cancel/shutdown).
    """
    if pages is None:
        pages = _load_pages(conn, doc_id)

    with _guard(lock):
        old_ids = [
            int(row["embedding_id"])
            for row in conn.execute(
                "SELECT embedding_id FROM chunks "
                "WHERE document_id = ? AND embedding_id IS NOT NULL",
                (doc_id,),
            ).fetchall()
        ]
        delete_document_chunks(conn, doc_id)
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
        vector_store.remove(old_ids, save=False)
        # Make the cleanup durable before encoding: on failure the document is
        # marked failed and the next import starts from a clean slate.
        conn.commit()

    next_id = int(get_meta(conn, "next_embedding_id", "1") or "1")
    completed = True

    try:
        for page in pages:
            if checkpoint is not None and not checkpoint():
                completed = False
                break

            page_no = int(page["page_no"])
            chunks = chunk_text(page["text"] or "")
            for start in range(0, len(chunks), EMBED_BATCH_SIZE):
                if checkpoint is not None and not checkpoint():
                    completed = False
                    break

                batch = chunks[start : start + EMBED_BATCH_SIZE]
                # Heavy model work happens outside the database lock. A
                # ``None`` result (disabled/failed embedding) keeps chunks
                # searchable through FTS without a vector id.
                try:
                    vectors = (
                        encode_fn([chunk.text for chunk in batch])
                        if encode_fn is not None
                        else None
                    )
                except Exception:  # noqa: BLE001 - degrade to FTS, keep the doc
                    logger.warning(
                        "embedding failed for document %s; indexing with FTS only",
                        doc_id,
                        exc_info=True,
                    )
                    vectors = None

                with _guard(lock):
                    added_ids: list[int] = []
                    added_vectors: list[list[float]] = []
                    for offset, chunk in enumerate(batch):
                        vector = None
                        if vectors is not None and offset < len(vectors):
                            vector = vectors[offset]

                        chunk_id = uuid.uuid4().hex
                        embedding_id = next_id if vector is not None else None
                        conn.execute(
                            """
                            INSERT INTO chunks (
                              id, document_id, page_no, chunk_index, text,
                              token_count, start_offset, end_offset,
                              embedding_id, created_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                chunk_id,
                                doc_id,
                                page_no,
                                chunk.chunk_index,
                                chunk.text,
                                max(1, len(chunk.text) // 2),
                                chunk.start_offset,
                                chunk.end_offset,
                                embedding_id,
                                utc_now(),
                            ),
                        )
                        insert_chunk(conn, chunk_id, doc_id, page_no, chunk.text)
                        if vector is not None:
                            added_ids.append(next_id)
                            added_vectors.append(list(map(float, vector)))
                            next_id += 1

                    vector_store.add(added_ids, added_vectors, save=False)
                    conn.commit()

            if not completed:
                break
    except Exception:
        # Persist whatever was written so database rows and FAISS ids stay
        # consistent; the pipeline then marks the document as failed.
        with _guard(lock):
            set_meta(conn, "next_embedding_id", next_id)
            conn.commit()
            vector_store.save()
        raise

    with _guard(lock):
        set_meta(conn, "next_embedding_id", next_id)
        conn.commit()
        vector_store.save()
    return completed
