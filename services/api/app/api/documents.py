from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


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
