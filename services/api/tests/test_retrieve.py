from datetime import datetime, timezone

from app.db import connect, init_db
from app.index.indexer import index_document
from app.index.vector_store import VectorStore
from app.qa.retrieve import rrf_fuse
from app.qa.retriever import HybridRetriever

DIMENSION = 1024


def test_rrf_prefers_items_in_both_lists():
    keyword = [{"chunk_id": "a", "score": 1.0}, {"chunk_id": "b", "score": 0.8}]
    vector = [{"chunk_id": "b", "score": 0.9}, {"chunk_id": "c", "score": 0.7}]
    fused = rrf_fuse(keyword, vector, k=60)
    assert fused[0]["chunk_id"] == "b"


def test_rrf_preserves_payload_and_adds_score():
    keyword = [{"chunk_id": "a", "score": 1.0}]
    fused = rrf_fuse(keyword, [], k=60)
    assert fused == [{"chunk_id": "a", "score": 1.0, "rrf_score": 1 / 61}]


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


def seed_document(conn):
    conn.execute(
        """
        INSERT INTO documents (
          id, root_path, path, file_name, mime, size_bytes, mtime,
          content_hash, status, created_at, updated_at
        ) VALUES ('doc1', '/root', '/root/a.md', 'a.md', 'text/plain', 10, 1.0,
                  'hash', 'ready', ?, ?)
        """,
        (now(), now()),
    )
    conn.execute(
        "INSERT INTO pages (document_id, page_no, text, is_scanned, created_at) "
        "VALUES ('doc1', 1, ?, 0, ?)",
        ("本合同的有效期为三年。", now()),
    )
    conn.execute(
        "INSERT INTO pages (document_id, page_no, text, is_scanned, created_at) "
        "VALUES ('doc1', 2, ?, 0, ?)",
        ("项目验收标准如下。", now()),
    )
    conn.commit()


def test_hybrid_retriever_hydrates_fused_chunks(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")
    index_document(conn, store, "doc1", encode_fn=fake_encode)

    retriever = HybridRetriever(conn, store, encode_fn=fake_encode)
    results = retriever.search("合同", limit=8)

    assert results
    first = results[0]
    assert first["file_name"] == "a.md"
    assert first["mime"] == "text/plain"
    assert first["page_no"] == 1
    assert "合同" in first["text"]
    assert "score" in first

    conn.close()


def test_hybrid_retriever_skips_embedding_when_index_is_empty(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    store = VectorStore(tmp_path / "vectors.faiss")

    def fail_encode(texts):
        raise AssertionError("embedding model must not load on an empty index")

    retriever = HybridRetriever(conn, store, encode_fn=fail_encode)
    assert retriever.search("合同") == []

    conn.close()

