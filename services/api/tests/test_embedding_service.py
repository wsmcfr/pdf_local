from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.db import connect, init_db
from app.index.embedding_service import EmbeddingService
from app.index.fts import search
from app.index.indexer import index_document
from app.index.vector_store import VectorStore
from app.main import app
from app.qa.retriever import HybridRetriever

HEADERS = {"X-App-Token": "secret"}
DIMENSION = 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fake_encode(texts):
    """Deterministic stand-in for BGE-M3: keyword -> one-hot vector."""
    vectors = []
    for text in texts:
        vector = [0.0] * DIMENSION
        vector[0 if "合同" in text else 1] = 1.0
        vectors.append(vector)
    return vectors


def seed_document(conn, doc_id="doc1"):
    conn.execute(
        """
        INSERT INTO documents (
          id, root_path, path, file_name, mime, size_bytes, mtime,
          content_hash, status, created_at, updated_at
        ) VALUES (?, '/root', '/root/a.md', 'a.md', 'text/plain', 10, 1.0,
                  'hash', 'ready', ?, ?)
        """,
        (doc_id, now(), now()),
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


# -- EmbeddingService ------------------------------------------------------


def test_local_mode_uses_local_encoder(tmp_path):
    conn = connect(tmp_path / "app.db")
    service = EmbeddingService(
        conn,
        {"embedding_mode": "local", "embedding_model": "BAAI/bge-m3"},
        local_encode=lambda texts: [[1.0, 2.0] for _ in texts],
    )

    assert service.mode == "local"
    assert service.embed(["a", "b"]) == [[1.0, 2.0], [1.0, 2.0]]


def test_auto_mode_falls_back_to_external_when_local_fails(tmp_path):
    conn = connect(tmp_path / "app.db")

    def broken_local(texts):
        raise RuntimeError("model missing")

    service = EmbeddingService(
        conn,
        {"embedding_mode": "auto"},
        local_encode=broken_local,
        external_embed=lambda texts: [[0.5, 0.25] for _ in texts],
    )

    assert service.embed(["hello"]) == [[0.5, 0.25]]


def test_external_mode_without_provider_returns_none(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    service = EmbeddingService(
        conn,
        {"embedding_mode": "external", "embedding_provider_id": ""},
    )

    assert service.embed(["hello"]) is None


def test_external_failure_returns_none(tmp_path):
    conn = connect(tmp_path / "app.db")

    def broken_external(texts):
        raise RuntimeError("upstream down")

    service = EmbeddingService(
        conn,
        {"embedding_mode": "external"},
        external_embed=broken_external,
    )

    assert service.embed(["hello"]) is None


def test_fts_mode_returns_none(tmp_path):
    conn = connect(tmp_path / "app.db")
    service = EmbeddingService(conn, {"embedding_mode": "fts"})

    assert service.mode == "fts"
    assert service.embed(["hello"]) is None


def test_info_and_test_never_expose_keys(tmp_path):
    conn = connect(tmp_path / "app.db")
    service = EmbeddingService(
        conn,
        {"embedding_mode": "local"},
        local_encode=lambda texts: [[0.1, 0.2, 0.3]],
    )

    info = service.info()
    assert info["mode"] == "local"
    assert info["local_model"] == "BAAI/bge-m3"
    assert "api_key" not in info
    assert service.test("hello") == {"ok": True, "mode": "local", "dimension": 3}


# -- indexer ---------------------------------------------------------------


def test_indexer_without_embedding_writes_fts_and_null_ids(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")

    assert index_document(conn, store, "doc1", encode_fn=None)

    rows = conn.execute(
        "SELECT id, embedding_id FROM chunks WHERE document_id = 'doc1'"
    ).fetchall()
    assert len(rows) == 2
    assert all(row["embedding_id"] is None for row in rows)
    assert store.index.ntotal == 0

    hits = search(conn, "合同", limit=5)
    assert [hit["chunk_id"] for hit in hits] == [rows[0]["id"]]
    assert conn.execute(
        "SELECT value FROM index_meta WHERE key = 'next_embedding_id'"
    ).fetchone()["value"] == "1"


def test_indexer_with_none_vectors_still_writes_chunks(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")

    assert index_document(conn, store, "doc1", encode_fn=lambda texts: None)

    assert conn.execute(
        "SELECT COUNT(*) AS c FROM chunks WHERE document_id = 'doc1'"
    ).fetchone()["c"] == 2
    assert store.index.ntotal == 0
    assert search(conn, "合同")


# -- retriever -------------------------------------------------------------


def _indexed_retriever_fixture(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    seed_document(conn)
    store = VectorStore(tmp_path / "vectors.faiss")
    index_document(conn, store, "doc1", encode_fn=fake_encode)
    return conn, store


def test_retriever_with_encode_fn_none_returns_fts(tmp_path):
    conn, store = _indexed_retriever_fixture(tmp_path)
    retriever = HybridRetriever(conn, store, encode_fn=None)

    results = retriever.search("合同", limit=8)
    assert results
    assert "合同" in results[0]["text"]


def test_retriever_degrades_when_encode_returns_none_or_raises(tmp_path):
    conn, store = _indexed_retriever_fixture(tmp_path)

    def returns_none(texts):
        return None

    def raises(texts):
        raise RuntimeError("embedding unavailable")

    for encode_fn in (returns_none, raises):
        retriever = HybridRetriever(conn, store, encode_fn=encode_fn)
        results = retriever.search("合同", limit=8)
        assert results
        assert "合同" in results[0]["text"]


# -- API -------------------------------------------------------------------


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    return TestClient(app)


class FakeEmbeddingService:
    mode = "local"

    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, texts):
        return self.vectors


def test_embedding_config_requires_token(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/embedding/config").status_code == 401
        assert (
            client.get(
                "/api/v1/embedding/config",
                headers={"X-App-Token": "wrong"},
            ).status_code
            == 401
        )


def test_get_embedding_config(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        body = client.get("/api/v1/embedding/config", headers=HEADERS).json()

    assert body["mode"] == "local"
    assert body["model"] == "BAAI/bge-m3"
    assert body["provider_id"] == ""
    assert body["local_model"] == "BAAI/bge-m3"
    assert body["providers"] == []
    assert "ok" in body and "status" in body


def test_patch_embedding_config_validates_and_persists(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        invalid = client.patch(
            "/api/v1/embedding/config",
            headers=HEADERS,
            json={"mode": "magic"},
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"] == "INVALID_MODE"

        missing = client.patch(
            "/api/v1/embedding/config",
            headers=HEADERS,
            json={"mode": "external", "provider_id": "nope", "model": "m"},
        )
        assert missing.status_code == 400
        assert missing.json()["detail"] == "PROVIDER_NOT_FOUND"

        created = client.post(
            "/api/v1/providers",
            headers=HEADERS,
            json={
                "name": "embed",
                "kind": "openai_chat",
                "base_url": "https://example.invalid/v1",
                "model": "text-embedding-3-small",
            },
        ).json()

        patched = client.patch(
            "/api/v1/embedding/config",
            headers=HEADERS,
            json={
                "mode": "external",
                "provider_id": created["id"],
                "model": "text-embedding-3-small",
            },
        )
        assert patched.status_code == 200
        body = patched.json()
        assert body["mode"] == "external"
        assert body["provider_id"] == created["id"]
        assert body["model"] == "text-embedding-3-small"
        assert body["providers"] == [{"id": created["id"], "name": "embed"}]

        settings = client.get("/api/v1/settings", headers=HEADERS).json()
        assert settings["embedding_mode"] == "external"
        assert settings["embedding_provider_id"] == created["id"]


def test_patch_external_requires_model(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        created = client.post(
            "/api/v1/providers",
            headers=HEADERS,
            json={
                "name": "embed",
                "kind": "openai_chat",
                "base_url": "https://example.invalid/v1",
                "model": "m",
            },
        ).json()
        response = client.patch(
            "/api/v1/embedding/config",
            headers=HEADERS,
            json={"mode": "external", "provider_id": created["id"], "model": ""},
        )

    assert response.status_code == 400
    assert response.json()["detail"] == "MODEL_REQUIRED"


def test_embedding_test_endpoint_with_mock_service(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        client.app.state.embedding_service = FakeEmbeddingService(
            [[0.1, 0.2, 0.3]]
        )
        ok = client.post(
            "/api/v1/embedding/test", headers=HEADERS, json={"text": "hello"}
        ).json()
        assert ok == {"ok": True, "mode": "local", "dimension": 3}

        client.app.state.embedding_service = FakeEmbeddingService(None)
        fallback = client.post(
            "/api/v1/embedding/test", headers=HEADERS, json={}
        ).json()
        assert fallback == {"ok": False, "mode": "local", "fallback": "fts"}
