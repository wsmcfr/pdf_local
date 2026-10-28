from __future__ import annotations

import sqlite3

from ..utils.text import tokenize_zh


def _quote(token: str) -> str:
    escaped = token.replace('"', '""')
    return f'"{escaped}"'


def insert_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    document_id: str,
    page_no: int,
    text: str,
) -> None:
    """Insert the pre-tokenized text of a chunk into the FTS index.

    The caller owns the transaction; :func:`app.index.indexer.index_document`
    commits once after writing every chunk.
    """
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, text_tokens) VALUES (?, ?)",
        (chunk_id, tokenize_zh(text)),
    )


def delete_chunk(conn: sqlite3.Connection, chunk_id: str) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))


def delete_document_chunks(conn: sqlite3.Connection, document_id: str) -> None:
    """Remove every FTS row that belongs to ``document_id``."""
    conn.execute(
        "DELETE FROM chunks_fts WHERE chunk_id IN "
        "(SELECT id FROM chunks WHERE document_id = ?)",
        (document_id,),
    )


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    document_id: str | None = None,
) -> list[dict]:
    tokens = [token for token in tokenize_zh(query).split() if token]
    if not tokens:
        return []
    match = " OR ".join(_quote(token) for token in tokens)

    if document_id:
        rows = conn.execute(
            """
            SELECT chunks_fts.chunk_id, bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks ON chunks.id = chunks_fts.chunk_id
            WHERE chunks_fts MATCH ? AND chunks.document_id = ?
            ORDER BY score
            LIMIT ?
            """,
            (match, document_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT chunk_id, bm25(chunks_fts) AS score
            FROM chunks_fts
            WHERE chunks_fts MATCH ?
            ORDER BY score
            LIMIT ?
            """,
            (match, limit),
        ).fetchall()
    return [{"chunk_id": row["chunk_id"], "score": abs(row["score"])} for row in rows]
