from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..llm.ollama import OllamaUnavailable
from ..qa.answer import answer_question

logger = logging.getLogger(__name__)

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
}


def sse(event: str, data: object) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
async def ask(payload: dict, request: Request) -> StreamingResponse:
    question = str(payload.get("question") or "").strip()
    if not question:
        return StreamingResponse(
            iter([sse("error", {"code": "QUESTION_REQUIRED", "message": "question required"})]),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    settings = getattr(request.app.state, "settings", {})
    model = settings.get("llm_model", "qwen2.5:7b")
    limit = int(settings.get("context_chunks", 8))

    async def stream() -> AsyncIterator[str]:
        try:
            contexts = await asyncio.to_thread(
                request.app.state.retriever.search,
                question,
                limit=limit,
            )
        except Exception:  # noqa: BLE001 - surface retrieval faults as SSE
            logger.exception("retrieval failed")
            yield sse("error", {"code": "RETRIEVAL_FAILED", "message": "检索失败"})
            return

        yield sse("retrieval", {"chunks": contexts})

        try:
            async for event, data in answer_question(question, contexts, model):
                yield sse(event, data)
        except OllamaUnavailable:
            logger.warning("ollama is unavailable")
            yield sse(
                "error",
                {"code": "OLLAMA_UNAVAILABLE", "message": "无法连接本地模型服务"},
            )
        except Exception:  # noqa: BLE001 - keep the SSE stream intact
            logger.exception("answer generation failed")
            yield sse("error", {"code": "LLM_ERROR", "message": "生成回答失败"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
