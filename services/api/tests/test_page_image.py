import fitz
import pytest
from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-App-Token": "secret"}
NOW = "2026-10-27T00:00:00+00:00"


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello local knowledge base")
    doc.save(path)
    doc.close()


def insert_document(document_id, path, mime):
    app.state.db.execute(
        """
        INSERT INTO documents (
          id, root_path, path, file_name, mime, size_bytes, mtime,
          content_hash, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 0, 0, 'hash', 'ready', ?, ?)
        """,
        (document_id, str(path.parent), str(path), path.name, mime, NOW, NOW),
    )
    app.state.db.commit()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


def test_page_image_renders_pdf_as_png(client, tmp_path):
    pdf = tmp_path / "report.pdf"
    make_pdf(pdf)
    insert_document("doc1", pdf, "application/pdf")

    response = client.get("/api/v1/documents/doc1/pages/1/image", headers=HEADERS)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_page_image_rejects_out_of_range_page(client, tmp_path):
    pdf = tmp_path / "report.pdf"
    make_pdf(pdf)
    insert_document("doc1", pdf, "application/pdf")

    response = client.get("/api/v1/documents/doc1/pages/9/image", headers=HEADERS)

    assert response.status_code == 404


def test_page_image_returns_404_for_non_pdf(client, tmp_path):
    text = tmp_path / "notes.txt"
    text.write_text("hello", encoding="utf-8")
    insert_document("doc2", text, "text/plain")

    response = client.get("/api/v1/documents/doc2/pages/1/image", headers=HEADERS)

    assert response.status_code == 404


def test_page_image_returns_404_when_file_is_missing(client, tmp_path):
    insert_document("doc3", tmp_path / "gone.pdf", "application/pdf")

    response = client.get("/api/v1/documents/doc3/pages/1/image", headers=HEADERS)

    assert response.status_code == 404
