from datetime import datetime, timezone

from app.db import connect, init_db
from app.index.fts import search
from app.index.indexer import index_document
from app.index.vector_store import VectorStore
from app.ingest import pipeline as pipeline_module
from app.ingest.pipeline import IngestPipeline

DIMENSION = 1024


def fake_encode(texts):
    """Deterministic stand-in for BGE-M3: keyword -> one-hot vector."""
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSION
        vector[0 if "合同" in text else 1] = 1.0
        vectors.append(vector)
    return vectors


def now():
    return datetime.now(timezone.utc).isoformat()


def seed_document(conn, doc_id="doc1"):
    conn.execute(
        """
        INSERT INTO documents (
          id, root_path, path, file_name, mime, size_bytes, mtime,
          content_hash, status, created_at, updated_at
        ) VALUES (?, '/root', '/root/a.md', 'a.md', 'text/plain', 10, 1.0,
                  'hash', 'processing', ?, ?)
        """,
        (doc_id, now(), now()),
    )
    conn.execute(
        "UPDATE documents SET status = 'ready' WHERE id = ?", (doc_id,)
    )
    conn.execute(
        "INSERT INTO pages (document_id, page_no, text, is_scanned, created_at) "
        "VALUES (?, 1, ?, 0, ?)",
        (doc_id, "本合同的有效期为三年。", now()),
    )
    conn.execute(
        "INSERT INTO pages (document_id, page_no, text, is_scanned, created_at) "
        "VALUES (?, 2, ?, 0, ?)",
        (doc_id, "项目验收标准如下。", now()),
    )
    conn.commit()


def test_index_document_writes_chunks_fts_and_vectors(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")

    assert index_document(conn, store, "doc1", encode_fn=fake_encode)

    rows = conn.execute(
        "SELECT id, page_no, embedding_id FROM chunks "
        "WHERE document_id = 'doc1' ORDER BY page_no"
    ).fetchall()
    assert len(rows) == 2
    assert all(row["embedding_id"] is not None for row in rows)
    assert len({row["embedding_id"] for row in rows}) == 2

    hits = search(conn, "合同", limit=5)
    assert [hit["chunk_id"] for hit in hits] == [rows[0]["id"]]

    contract_vector = [0.0] * DIMENSION
    contract_vector[0] = 1.0
    matches = store.search(contract_vector, limit=5)
    assert matches
    assert matches[0][0] == rows[0]["embedding_id"]
    assert matches[0][1] == 1.0

    # The index is persisted and reloadable.
    reloaded = VectorStore(tmp_path / "vectors.faiss")
    assert reloaded.index.ntotal == 2


def test_reindex_replaces_old_chunks_and_vectors(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")

    index_document(conn, store, "doc1", encode_fn=fake_encode)
    first = conn.execute(
        "SELECT id, embedding_id FROM chunks WHERE document_id = 'doc1'"
    ).fetchall()
    assert store.index.ntotal == 2

    index_document(conn, store, "doc1", encode_fn=fake_encode)
    second = conn.execute(
        "SELECT id, embedding_id FROM chunks WHERE document_id = 'doc1'"
    ).fetchall()

    assert len(second) == 2
    assert store.index.ntotal == 2
    assert {row["id"] for row in first}.isdisjoint({row["id"] for row in second})
    assert {row["embedding_id"] for row in first}.isdisjoint(
        {row["embedding_id"] for row in second}
    )


def test_checkpoint_can_stop_indexing(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")

    assert not index_document(
        conn, store, "doc1", encode_fn=fake_encode, checkpoint=lambda: False
    )
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE document_id = 'doc1'"
    ).fetchone()["c"] == 0
    assert conn.execute(
        "SELECT value FROM index_meta WHERE key = 'next_embedding_id'"
    ).fetchone()["value"] == "1"

    assert index_document(conn, store, "doc1", encode_fn=fake_encode)
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE document_id = 'doc1'"
    ).fetchone()["c"] == 2


def test_pipeline_indexes_files_with_vector_store(tmp_path, monkeypatch):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    job_id = "job1"
    conn.execute(
        "INSERT INTO jobs (id, type, status, payload_json, created_at, updated_at) "
        "VALUES (?, 'scan', 'queued', '{}', ?, ?)",
        (job_id, now(), now()),
    )
    conn.commit()

    source = tmp_path / "note.txt"
    source.write_text("本合同的有效期为三年。", encoding="utf-8")
    store = VectorStore(tmp_path / "vectors.faiss")

    real_index = pipeline_module.index_document
    monkeypatch.setattr(
        pipeline_module,
        "index_document",
        lambda conn, vector_store, doc_id, **kwargs: real_index(
            conn,
            vector_store,
            doc_id,
            encode_fn=fake_encode,
            **kwargs,
        ),
    )

    pipeline = IngestPipeline(conn, vector_store=store)
    assert pipeline.run_scan(job_id, str(tmp_path))

    document = conn.execute(
        "SELECT status FROM documents WHERE path = ?", (str(source),)
    ).fetchone()
    assert document["status"] == "ready"
    assert store.index.ntotal == 1
    assert search(conn, "合同")
