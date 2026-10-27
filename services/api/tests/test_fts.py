from app.db import connect, init_db
from app.index.fts import insert_chunk, search


def test_chinese_two_char_query(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    insert_chunk(conn, "c1", "doc1", 1, "本合同的有效期为三年。")
    insert_chunk(conn, "c2", "doc1", 2, "项目验收标准如下。")

    results = search(conn, "合同", limit=5)
    assert results
    assert results[0]["chunk_id"] == "c1"


def test_query_without_tokens_returns_empty(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    insert_chunk(conn, "c1", "doc1", 1, "本合同的有效期为三年。")

    assert search(conn, "。！？") == []
