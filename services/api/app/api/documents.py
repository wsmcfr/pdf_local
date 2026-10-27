from pathlib import Path

import fitz
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

router = APIRouter()

PAGE_IMAGE_DPI = 150


@router.get("/documents")
def list_documents(request: Request, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM documents"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT 500"
    rows = request.app.state.db.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


@router.get("/documents/{document_id}")
def get_document(document_id: str, request: Request) -> dict:
    row = request.app.state.db.execute(
        "SELECT * FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    return dict(row)


@router.get("/documents/{document_id}/pages/{page_no}")
def get_page(document_id: str, page_no: int, request: Request) -> dict:
    row = request.app.state.db.execute(
        "SELECT * FROM pages WHERE document_id = ? AND page_no = ?",
        (document_id, page_no),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    return dict(row)


@router.get("/documents/{document_id}/pages/{page_no}/image")
def get_page_image(document_id: str, page_no: int, request: Request) -> Response:
    row = request.app.state.db.execute(
        "SELECT path FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")

    path = Path(row["path"])
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")

    try:
        with fitz.open(path) as doc:
            if page_no < 1 or page_no > doc.page_count:
                raise HTTPException(status_code=404, detail="PAGE_NOT_FOUND")
            pix = doc[page_no - 1].get_pixmap(dpi=PAGE_IMAGE_DPI)
            png = pix.tobytes("png")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - corrupt PDF is reported as missing
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND") from exc

    return Response(content=png, media_type="image/png")
