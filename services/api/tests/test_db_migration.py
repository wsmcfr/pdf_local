import sqlite3

from app.db import connect, init_db


def test_blocks_table_and_document_columns(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"editable", "source_format", "converted_from"} <= columns

    conn.execute(
        "SELECT id, document_id, page_no, order_index, kind, text, "
        "locator_json, bbox_json, editable, approximate FROM blocks LIMIT 0"
    )
    conn.close()


def test_init_db_migrates_an_old_documents_table(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE documents (
          id TEXT PRIMARY KEY,
          root_path TEXT NOT NULL,
          path TEXT NOT NULL UNIQUE,
          file_name TEXT NOT NULL,
          mime TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          mtime REAL NOT NULL,
          content_hash TEXT NOT NULL,
          page_count INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending',
          ocr_confidence REAL,
          error TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          indexed_at TEXT
        )
        """
    )
    conn.commit()

    init_db(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert {"editable", "source_format", "converted_from"} <= columns
    conn.close()
