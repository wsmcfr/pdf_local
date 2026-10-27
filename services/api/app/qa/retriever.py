from __future__ import annotations

import logging
import sqlite3
from typing import Callable

from ..index.embeddings import encode
from ..index.fts import search as fts_search
from ..index.vector_store import VectorStore
from .retrieve import rrf_fuse

logger = logging.getLogger(__name__)

EncodeFn = Callable[[list[str]], list[list[float]] | None]

KEYWORD_LIMIT = 20
VECTOR_LIMIT = 20


class HybridRetriever:
    """Fuse SQLite FTS5 keyword hits with FAISS vector hits via RRF.

    ``encode_fn`` is injected so tests can avoid loading BGE-M3. The default
    lazily loads the real model only when :meth:`search` is called. Passing
    ``None`` disables the vector path; keyword search stays available.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        vector_store: VectorStore,
        encode_fn: EncodeFn | None = encode,
    ) -> None:
        self.conn = conn
        self.vector_store = vector_store
        self.encode_fn = encode_fn

    def search(self, question: str, limit: int = 8) -> list[dict]:
        if limit <= 0:
            return []

        keyword = fts_search(self.conn, question, limit=KEYWORD_LIMIT)
        vector_results = self._vector_results(question, limit=VECTOR_LIMIT)

        fused = rrf_fuse(keyword, vector_results, k=60)[:limit]
        return [self._hydrate(item) for item in fused]

    def _vector_results(self, question: str, limit: int) -> list[dict]:
        # Avoid loading the embedding model at all when nothing is indexed or
        # vector embedding is disabled (e.g. ``fts`` mode).
        if self.encode_fn is None or self.vector_store.index.ntotal == 0:
            return []

        try:
            vectors = self.encode_fn([question])
        except Exception:  # noqa: BLE001 - degrade to keyword search only
            logger.warning("query embedding failed; using FTS only", exc_info=True)
            return []

        if not vectors or not vectors[0]:
            return []

        results: list[dict] = []
        for embedding_id, score in self.vector_store.search(vectors[0], limit=limit):
            row = self.conn.execute(
                "SELECT id AS chunk_id FROM chunks WHERE embedding_id = ?",
                (embedding_id,),
            ).fetchone()
            if row:
                results.append({"chunk_id": row["chunk_id"], "score": score})
        return results

    def _hydrate(self, item: dict) -> dict:
        row = self.conn.execute(
            """
            SELECT c.id AS chunk_id, c.document_id, c.page_no, c.text,
                   d.file_name, d.mime
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id = ?
            """,
            (item["chunk_id"],),
        ).fetchone()
        if not row:
            return item

        hydrated = dict(row)
        if "rrf_score" in item:
            hydrated["score"] = item["rrf_score"]
        return hydrated
