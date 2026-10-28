from datetime import datetime, timezone

from app.db import connect, init_db
from app.index.fts import search as fts_search
from app.index.indexer import index_document
from app.index.vector_store import VectorStore
from app.qa.retriever import HybridRetriever

DIMENSION = 1024


def fake_encode(texts):
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSION
        vector[0 if "合同" in text else 1] = 1.0
        vectors.append(vector)
    return vectors


def now():
    return datetime.now(timezone.utc).isoformat()


def seed(conn):
    for doc_id, name, text in [
        ("doc1", "a.md", "合同有效期为三年。"),
        ("doc2", "b.md", "合同发票需要核对。"),
    ]:
        conn.execute(
            """
            INSERT INTO documents (
              id, root_path, path, file_name, mime, size_bytes, mtime,
              content_hash, status, created_at, updated_at
            ) VALUES (?, '/root', ?, ?, 'text/plain', 10, 1.0,
                      ?, 'ready', ?, ?)
            """,
            (doc_id, f"/root/{name}", name, doc_id, now(), now()),
        )
        conn.execute(
            "INSERT INTO pages (document_id, page_no, text, is_scanned, created_at) "
            "VALUES (?, 1, ?, 0, ?)",
            (doc_id, text, now()),
        )
    conn.commit()


def test_fts_and_hybrid_search_can_be_scoped_to_one_document(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed(conn)
    store = VectorStore(tmp_path / "vectors.faiss")
    index_document(conn, store, "doc1", encode_fn=fake_encode)
    index_document(conn, store, "doc2", encode_fn=fake_encode)

    fts_all = fts_search(conn, "合同", limit=20)
    fts_doc1 = fts_search(conn, "合同", limit=20, document_id="doc1")
    assert len(fts_all) == 2
    assert len(fts_doc1) == 1

    retriever = HybridRetriever(conn, store, encode_fn=fake_encode)
    all_results = retriever.search("合同", limit=8)
    doc1_results = retriever.search("合同", limit=8, document_id="doc1")
    assert {item["document_id"] for item in all_results} == {"doc1", "doc2"}
    assert {item["document_id"] for item in doc1_results} == {"doc1"}
    conn.close()
