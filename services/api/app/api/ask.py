from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..providers import errors, factory, registry
from ..qa.answer import answer_question

logger = logging.getLogger(__name__)

router = APIRouter()

FULL_TEXT_CHAR_LIMIT = 30000

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
}

PROVIDER_ERRORS = {
    errors.ProviderAuthError: (
        "UPSTREAM_AUTH_ERROR",
        "Provider 密钥无效或没有访问权限。",
    ),
    errors.ProviderRateLimitError: (
        "UPSTREAM_RATE_LIMIT",
        "Provider 请求过于频繁，请稍后重试。",
    ),
    errors.ProviderTimeoutError: (
        "UPSTREAM_TIMEOUT",
        "Provider 响应超时，请稍后重试。",
    ),
    errors.ProviderNotFoundError: (
        "UPSTREAM_NOT_FOUND",
        "Provider 接口地址不存在，请检查 Base URL。",
    ),
    errors.ProviderConfigError: (
        "INVALID_PROVIDER_CONFIG",
        "Provider 配置不完整，请检查类型与地址。",
    ),
}


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def provider_error_event(exc: errors.ProviderError) -> tuple[str, str]:
    """Map an adapter failure to a fixed, secret-free SSE code and message."""
    for error_type, mapped in PROVIDER_ERRORS.items():
        if isinstance(exc, error_type):
            return mapped
    return ("UPSTREAM_ERROR", "Provider 请求失败，请稍后重试。")


@router.post("/ask")
async def ask(payload: dict, request: Request) -> StreamingResponse:
    question = str(payload.get("question") or "").strip()
    if not question:
        return StreamingResponse(
            iter([sse("error", {"code": "QUESTION_REQUIRED", "message": "question required"})]),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    settings = getattr(request.app.state, "settings", {}) or {}
    limit = int(settings.get("context_chunks", 8))
    document_id = str(payload.get("document_id") or "").strip() or None

    async def stream() -> AsyncIterator[str]:
        row = registry.select_provider(request.app.state.db, settings)
        if row is None:
            yield sse(
                "error",
                {
                    "code": "PROVIDER_NOT_CONFIGURED",
                    "message": "尚未配置问答 Provider，请到设置页添加并选择模型。",
                },
            )
            return

        try:
            config = registry.load_config(row)
            provider = factory.create_provider(config)
        except errors.ProviderConfigError:
            code, message = provider_error_event(
                errors.ProviderConfigError("invalid provider config")
            )
            yield sse("error", {"code": code, "message": message})
            return
        except Exception:  # noqa: BLE001 - keyring/loading failures are opaque
            logger.exception("loading provider failed")
            yield sse(
                "error",
                {"code": "PROVIDER_ERROR", "message": "Provider 加载失败，请检查设置。"},
            )
            return

        try:
            if document_id:
                row = request.app.state.db.execute(
                    "SELECT file_name, mime FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                if not row:
                    yield sse(
                        "error",
                        {"code": "DOCUMENT_NOT_FOUND", "message": "文件不存在"},
                    )
                    return
                pages = request.app.state.db.execute(
                    "SELECT page_no, text FROM pages WHERE document_id = ? "
                    "ORDER BY page_no",
                    (document_id,),
                ).fetchall()
                total_chars = sum(len(page["text"] or "") for page in pages)
                if total_chars <= FULL_TEXT_CHAR_LIMIT:
                    contexts = [
                        {
                            "chunk_id": f"page-{page['page_no']}",
                            "document_id": document_id,
                            "page_no": page["page_no"],
                            "text": page["text"],
                            "file_name": row["file_name"],
                            "mime": row["mime"],
                            "score": 1.0,
                        }
                        for page in pages
                        if (page["text"] or "").strip()
                    ]
                    context_mode = "full_text"
                else:
                    contexts = await asyncio.to_thread(
                        request.app.state.retriever.search,
                        question,
                        limit,
                        document_id,
                    )
                    context_mode = "retrieval"
            else:
                contexts = await asyncio.to_thread(
                    request.app.state.retriever.search,
                    question,
                    limit=limit,
                )
                context_mode = "retrieval"
        except Exception:  # noqa: BLE001 - surface retrieval faults as SSE
            logger.exception("retrieval failed")
            yield sse("error", {"code": "RETRIEVAL_FAILED", "message": "检索失败"})
            return

        yield sse(
            "retrieval", {"chunks": contexts, "context_mode": context_mode}
        )

        try:
            async for event, data in answer_question(
                question, contexts, provider, config.model
            ):
                yield sse(event, data)
        except errors.ProviderError as exc:
            code, message = provider_error_event(exc)
            logger.warning("provider request failed: %s", type(exc).__name__)
            yield sse("error", {"code": code, "message": message})
        except Exception:  # noqa: BLE001 - keep the SSE stream intact
            logger.exception("answer generation failed")
            yield sse("error", {"code": "LLM_ERROR", "message": "生成回答失败"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
