"""MCP tools exposing the local knowledge base.

The same functions are used by the SSE/HTTP transport mounted at ``/mcp`` and
by the stdio entry point in :mod:`app.mcp_stdio`. ``create_mcp_server`` takes a
``get_state`` callable so tools resolve ``app.state`` lazily; this avoids
depending on the FastAPI lifespan having run at import time.

Every tool returns plain JSON-serialisable data. Retrieval failures degrade to
empty results, provider failures map to fixed error codes, and API keys are
never included in any response.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Annotated, Any, Callable

import fitz
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .providers import errors, factory, registry
from .providers.base import ChatMessage, ChatRequest
from .qa.rag import (
    citations_from_chunks,
    inject_context,
    retrieve_context,
)

logger = logging.getLogger(__name__)

NO_ANSWER = "资料中没有找到。"

DEFAULT_SEARCH_TOP_K = 8
MAX_SEARCH_TOP_K = 50
DEFAULT_DOCUMENT_LIMIT = 20
MAX_DOCUMENT_LIMIT = 100

PAGE_IMAGE_DPI = 150


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _clamp_int(value: Any, lower: int, upper: int, default: int) -> int:
    """Clamp ``value`` into ``[lower, upper]``; invalid values use ``default``."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(lower, min(upper, number))


def _search_item(item: dict) -> dict:
    return {
        "document_id": item.get("document_id"),
        "file_name": item.get("file_name"),
        "page_no": item.get("page_no"),
        "text": item.get("text"),
        "score": item.get("score"),
        "chunk_id": item.get("chunk_id"),
    }


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, errors.ProviderAuthError):
        return "UPSTREAM_AUTH_ERROR"
    if isinstance(exc, errors.ProviderRateLimitError):
        return "UPSTREAM_RATE_LIMIT"
    if isinstance(exc, errors.ProviderTimeoutError):
        return "UPSTREAM_TIMEOUT"
    if isinstance(exc, errors.ProviderNotFoundError):
        return "UPSTREAM_NOT_FOUND"
    if isinstance(exc, errors.ProviderConfigError):
        return "INVALID_PROVIDER_CONFIG"
    return "UPSTREAM_ERROR"


def _page_image_base64(path: str | None, page_no: int) -> str | None:
    """Render one PDF page as a base64 PNG, or ``None`` when unavailable."""
    if not path:
        return None
    target = Path(path)
    if target.suffix.lower() != ".pdf" or not target.is_file():
        return None
    try:
        with fitz.open(target) as doc:
            if page_no < 1 or page_no > doc.page_count:
                return None
            pix = doc[page_no - 1].get_pixmap(dpi=PAGE_IMAGE_DPI)
            return base64.b64encode(pix.tobytes("png")).decode("ascii")
    except Exception:  # noqa: BLE001 - a corrupt PDF must not fail the tool
        logger.warning("MCP page image render failed", exc_info=True)
        return None


def _missing_page(document_id: Any, page_no: Any) -> dict:
    return {
        "document_id": str(document_id) if document_id is not None else None,
        "file_name": None,
        "page_no": page_no,
        "text": None,
        "is_scanned": False,
        "ocr_confidence": None,
        "low_confidence": False,
        "error": "PAGE_NOT_FOUND",
    }


# ---------------------------------------------------------------------------
# tool implementations (directly testable)
# ---------------------------------------------------------------------------
def kb_search_impl(state: Any, query: str, top_k: int = DEFAULT_SEARCH_TOP_K) -> dict:
    """Search the indexed knowledge base and return hydrated chunks."""
    limit = _clamp_int(top_k, 1, MAX_SEARCH_TOP_K, DEFAULT_SEARCH_TOP_K)
    question = (query or "").strip()
    if not question:
        return {"results": []}

    retriever = getattr(state, "retriever", None)
    if retriever is None:
        return {"results": []}

    try:
        results = retriever.search(question, limit=limit)
    except Exception:  # noqa: BLE001 - search failures yield empty results
        logger.warning("MCP search failed", exc_info=True)
        return {"results": []}

    items = [
        _search_item(item)
        for item in (results or [])
        if isinstance(item, dict)
    ]
    return {"results": items[:limit]}


def kb_get_page_impl(state: Any, document_id: str, page_no: int) -> dict:
    """Return one indexed page plus an optional base64 page image."""
    if isinstance(page_no, bool):
        return _missing_page(document_id, page_no)
    try:
        page = int(page_no)
    except (TypeError, ValueError):
        return _missing_page(document_id, page_no)

    conn = getattr(state, "db", None)
    if conn is None:
        return _missing_page(document_id, page_no)

    try:
        row = conn.execute(
            """
            SELECT p.document_id, p.page_no, p.text, p.is_scanned,
                   p.ocr_confidence, p.low_confidence,
                   d.file_name, d.mime, d.path
            FROM pages p
            JOIN documents d ON d.id = p.document_id
            WHERE p.document_id = ? AND p.page_no = ?
            """,
            (str(document_id), page),
        ).fetchone()
    except Exception:  # noqa: BLE001 - malformed ids must not crash the tool
        logger.warning("MCP page lookup failed", exc_info=True)
        return _missing_page(document_id, page_no)

    if row is None:
        return _missing_page(document_id, page_no)

    result = {
        "document_id": str(row["document_id"]),
        "file_name": row["file_name"],
        "page_no": int(row["page_no"]),
        "text": row["text"],
        "is_scanned": bool(row["is_scanned"]),
        "ocr_confidence": row["ocr_confidence"],
        "low_confidence": bool(row["low_confidence"]),
    }
    image = _page_image_base64(row["path"], page)
    if image:
        result["image_base64"] = image
    return result


def kb_list_documents_impl(
    state: Any, query: str = "", limit: int = DEFAULT_DOCUMENT_LIMIT
) -> dict:
    """List indexed documents, optionally filtering by file name or path."""
    max_rows = _clamp_int(limit, 1, MAX_DOCUMENT_LIMIT, DEFAULT_DOCUMENT_LIMIT)
    conn = getattr(state, "db", None)
    if conn is None:
        return {"documents": []}

    sql = (
        "SELECT id, root_path, path, file_name, mime, status, page_count, "
        "size_bytes, updated_at FROM documents"
    )
    params: list[Any] = []
    term = (query or "").strip()
    if term:
        escaped = (
            term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        pattern = f"%{escaped}%"
        sql += " WHERE file_name LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\'"
        params.extend([pattern, pattern])
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max_rows)

    try:
        rows = conn.execute(sql, params).fetchall()
    except Exception:  # noqa: BLE001 - never fail the tool on storage errors
        logger.warning("MCP document listing failed", exc_info=True)
        return {"documents": []}
    return {"documents": [dict(row) for row in rows]}


async def kb_ask_impl(state: Any, question: str, top_k: int = DEFAULT_SEARCH_TOP_K) -> dict:
    """Answer ``question`` with the configured provider using retrieved context."""
    limit = _clamp_int(top_k, 1, MAX_SEARCH_TOP_K, DEFAULT_SEARCH_TOP_K)
    text = (question or "").strip()
    if not text:
        return {"answer": NO_ANSWER, "citations": [], "model": ""}

    contexts = retrieve_context(getattr(state, "retriever", None), text, limit)
    if not contexts:
        return {"answer": NO_ANSWER, "citations": [], "model": ""}

    citations = citations_from_chunks(contexts)
    settings = getattr(state, "settings", {}) or {}
    conn = getattr(state, "db", None)
    if conn is None:
        return {
            "answer": "",
            "citations": citations,
            "model": "",
            "error": "PROVIDER_NOT_CONFIGURED",
        }

    try:
        row = registry.select_provider(conn, settings)
    except Exception:  # noqa: BLE001 - unreadable settings are opaque
        logger.warning("MCP provider selection failed", exc_info=True)
        row = None
    if row is None:
        return {
            "answer": "",
            "citations": citations,
            "model": "",
            "error": "PROVIDER_NOT_CONFIGURED",
        }

    try:
        config = registry.load_config(row)
        provider = factory.create_provider(config)
    except errors.ProviderConfigError:
        return {
            "answer": "",
            "citations": citations,
            "model": str(row["model"] or ""),
            "error": "INVALID_PROVIDER_CONFIG",
        }
    except Exception:  # noqa: BLE001 - keyring/loading failures stay opaque
        logger.warning("MCP provider loading failed", exc_info=True)
        return {
            "answer": "",
            "citations": citations,
            "model": str(row["model"] or ""),
            "error": "PROVIDER_ERROR",
        }

    model = config.model or str(row["model"] or "")
    messages = inject_context([ChatMessage(role="user", content=text)], contexts)
    chat_request = ChatRequest(model=model, messages=messages, stream=False)

    answer = ""
    try:
        async for chunk in provider.stream_chat(chat_request):
            if chunk.type == "text":
                answer += chunk.text or ""
            elif chunk.type == "error":
                raise errors.ProviderError(chunk.error or "provider error")
    except errors.ProviderError as exc:
        return {
            "answer": "",
            "citations": citations,
            "model": model,
            "error": _error_code(exc),
        }
    except Exception:  # noqa: BLE001 - never leak upstream payloads
        logger.warning("MCP provider request failed", exc_info=True)
        return {
            "answer": "",
            "citations": citations,
            "model": model,
            "error": "UPSTREAM_ERROR",
        }

    return {"answer": answer, "citations": citations, "model": model}


# ---------------------------------------------------------------------------
# FastMCP server
# ---------------------------------------------------------------------------
def create_mcp_server(get_state: Callable[[], Any]) -> FastMCP:
    """Build the MCP server with tools that resolve state lazily."""
    server = FastMCP(
        name="local-kb",
        instructions=(
            "本地知识库工具。使用 kb_search 检索资料，kb_get_page 读取完整页面，"
            "kb_list_documents 浏览资料列表；回答由调用方模型完成。"
        ),
        host="127.0.0.1",
    )

    @server.tool(
        name="kb_search",
        description=(
            "检索本地知识库，返回与查询最相关的文本块。每项包含 document_id、"
            "file_name、page_no、text、score 和 chunk_id。top_k 取值 1-50。"
        ),
    )
    def kb_search(
        query: Annotated[str, Field(description="检索问题或关键词")],
        top_k: Annotated[int, Field(description="返回结果数量，范围 1-50，默认 8")] = 8,
    ) -> dict:
        return kb_search_impl(get_state(), query, top_k)

    @server.tool(
        name="kb_get_page",
        description=(
            "读取指定文档某一页的完整文本和 OCR 信息。PDF 页面存在时还会返回 "
            "image_base64（PNG）。"
        ),
    )
    def kb_get_page(
        document_id: Annotated[str, Field(description="文档 id（来自 kb_search）")],
        page_no: Annotated[int, Field(description="页码，从 1 开始")],
    ) -> dict:
        return kb_get_page_impl(get_state(), document_id, page_no)

    @server.tool(
        name="kb_list_documents",
        description=(
            "列出已入库资料，可按文件名或路径模糊过滤。limit 取值 1-100。"
        ),
    )
    def kb_list_documents(
        query: Annotated[str, Field(description="可选的文件名/路径过滤词")] = "",
        limit: Annotated[int, Field(description="返回数量上限，范围 1-100，默认 20")] = 20,
    ) -> dict:
        return kb_list_documents_impl(get_state(), query, limit)

    @server.tool(
        name="kb_ask",
        description=(
            "使用本机配置的上游模型，基于知识库检索结果回答问题，并返回引用。"
            "资料不足时返回“资料中没有找到。”"
        ),
    )
    async def kb_ask(
        question: Annotated[str, Field(description="用户问题")],
        top_k: Annotated[int, Field(description="检索结果数量，范围 1-50，默认 8")] = 8,
    ) -> dict:
        return await kb_ask_impl(get_state(), question, top_k)

    return server
