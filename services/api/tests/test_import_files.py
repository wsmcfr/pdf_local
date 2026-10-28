import time

from docx import Document
from fastapi.testclient import TestClient

from app.main import app

HEADERS = {"X-App-Token": "secret"}
TERMINAL = {"done", "failed", "cancelled"}


def wait_for_job(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    job = {}
    while time.monotonic() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}", headers=HEADERS).json()
        if job.get("status") in TERMINAL:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} stayed {job.get('status')!r}")


def make_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    return TestClient(app)


def test_import_files_ingests_a_docx(tmp_path, monkeypatch):
    path = tmp_path / "notes.docx"
    document = Document()
    document.add_paragraph("项目计划：先做单文件导入。")
    document.save(path)

    with make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/import/files",
            json={"paths": [str(path)]},
            headers=HEADERS,
        )
        assert response.status_code == 200
        job = wait_for_job(client, response.json()["id"])
        assert job["status"] == "done"

        documents = client.get("/api/v1/documents", headers=HEADERS).json()
        imported = [item for item in documents if item["path"] == str(path.resolve())]
        assert len(imported) == 1
        assert imported[0]["status"] == "ready"
        assert imported[0]["source_format"] == "docx"


def test_import_rejects_unsupported_legacy_doc(tmp_path, monkeypatch):
    path = tmp_path / "old.doc"
    path.write_text("legacy", encoding="utf-8")

    with make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/import/files",
            json={"paths": [str(path)]},
            headers=HEADERS,
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "UNSUPPORTED_FILE"


def test_import_requires_paths(tmp_path, monkeypatch):
    with make_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/import/files", json={}, headers=HEADERS
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "paths required"
