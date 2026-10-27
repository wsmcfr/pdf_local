import json

from fastapi import APIRouter, Request

router = APIRouter()

DEFAULTS = {
    "data_dir": "",
    "ocr_dpi": 300,
    "embedding_mode": "local",
    "embedding_provider_id": "",
    "embedding_model": "BAAI/bge-m3",
    "llm_provider_id": "",
    "context_chunks": 8,
    "offline_mode": True,
    "gateway_enabled": False,
    "gateway_rag_mode": "auto",
    "mcp_enabled": True,
}


@router.get("/settings")
def get_settings(request: Request) -> dict:
    rows = request.app.state.db.execute("SELECT key, value FROM settings").fetchall()
    stored = {row["key"]: json.loads(row["value"]) for row in rows}
    body = {**DEFAULTS, **stored}
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir is not None:
        body["data_dir"] = str(data_dir)
    return body


@router.patch("/settings")
def patch_settings(payload: dict, request: Request) -> dict:
    conn = request.app.state.db
    for key, value in payload.items():
        if key not in DEFAULTS:
            continue
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
    conn.commit()
    return get_settings(request)
