"""Inbound OpenAI-compatible gateway (Chat Completions + model listing)."""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..providers import errors, factory, registry
from ..providers.base import ChatMessage, ChatRequest
from ..qa.rag import citations_from_chunks, inject_context, retrieve_context
from .gateway_auth import require_gateway

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_gateway)])

NO_ANSWER = "资料中没有找到。"
DEFAULT_TOP_K = 8


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------
def _settings(request: Request) -> dict:
    return getattr(request.app.state, "settings", {}) or {}


def _content_text(content) -> str:
    """Flatten an OpenAI message content string or parts array to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _parse_messages(raw) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    if not isinstance(raw, list):
        return messages
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if not isinstance(role, str) or not role.strip():
            role = "user"
        messages.append(
            ChatMessage(role=role.strip(), content=_content_text(item.get("content")))
        )
    return messages


def _last_user_text(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content
    return ""


def _top_k(settings: dict) -> int:
    try:
        value = int(settings.get("context_chunks", DEFAULT_TOP_K))
    except (TypeError, ValueError):
        return DEFAULT_TOP_K
    return value if value > 0 else DEFAULT_TOP_K


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _max_tokens(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


# ---------------------------------------------------------------------------
# OpenAI-shaped errors
# ---------------------------------------------------------------------------
def _error_body(message: str, error_type: str, code: str | None = None) -> dict:
    error: dict = {"message": message, "type": error_type}
    if code:
        error["code"] = code
    return {"error": error}


def _error(
    status: int, message: str, error_type: str = "invalid_request_error", code=None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=_error_body(message, error_type, code),
    )


def _provider_error(exc: errors.ProviderError) -> JSONResponse:
    """Map adapter errors without exposing upstream payloads or secrets."""
    if isinstance(exc, errors.ProviderAuthError):
        return _error(
            401,
            "upstream provider rejected the credentials",
            "authentication_error",
            "UPSTREAM_AUTH_ERROR",
        )
    if isinstance(exc, errors.ProviderRateLimitError):
        return _error(
            429,
            "upstream provider rate limit exceeded",
            "rate_limit_error",
            "UPSTREAM_RATE_LIMIT",
        )
    if isinstance(exc, errors.ProviderTimeoutError):
        return _error(
            504,
            "upstream provider timed out",
            "timeout_error",
            "UPSTREAM_TIMEOUT",
        )
    if isinstance(exc, errors.ProviderNotFoundError):
        return _error(
            404,
            "upstream provider endpoint not found",
            "not_found_error",
            "UPSTREAM_NOT_FOUND",
        )
    if isinstance(exc, errors.ProviderConfigError):
        return _error(
            400,
            "provider configuration is invalid",
            "invalid_request_error",
            "INVALID_PROVIDER_CONFIG",
        )
    return _error(
        502,
        "upstream provider request failed",
        "upstream_error",
        "UPSTREAM_ERROR",
    )


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------
def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex}"


def _created() -> int:
    return int(time.time())


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chunk_payload(
    request_id: str,
    created: int,
    model: str,
    *,
    content: str | None = None,
    finish_reason: str | None = None,
) -> dict:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _no_answer_non_stream(request_id: str, created: int, model: str) -> JSONResponse:
    return JSONResponse(
        {
            "id": request_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": NO_ANSWER},
                    "finish_reason": "stop",
                }
            ],
            "kb_citations": [],
        }
    )


def _no_answer_stream(
    request_id: str, created: int, model: str
) -> StreamingResponse:
    async def event_stream():
        yield _sse(
            _chunk_payload(request_id, created, model, content=NO_ANSWER)
        )
        final = _chunk_payload(
            request_id, created, model, finish_reason="stop"
        )
        final["kb_citations"] = []
        yield _sse(final)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _non_stream_response(
    provider,
    chat_request: ChatRequest,
    request_id: str,
    created: int,
    model: str,
    citations: list[dict],
) -> JSONResponse:
    content = ""
    usage = None
    try:
        async for chunk in provider.stream_chat(chat_request):
            if chunk.type == "text":
                content += chunk.text or ""
            elif chunk.type == "usage":
                usage = chunk.usage
            elif chunk.type == "error":
                raise errors.ProviderError(chunk.error or "provider error")
    except errors.ProviderError as exc:
        return _provider_error(exc)
    except Exception:  # noqa: BLE001 - generic upstream failure, no stack leak
        logger.warning("upstream chat request failed", exc_info=True)
        return _error(
            502,
            "upstream provider request failed",
            "upstream_error",
            "UPSTREAM_ERROR",
        )

    body: dict = {
        "id": request_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "kb_citations": citations,
    }
    if usage is not None:
        body["usage"] = usage
    return JSONResponse(body)


def _stream_response(
    provider,
    chat_request: ChatRequest,
    request_id: str,
    created: int,
    model: str,
    citations: list[dict],
) -> StreamingResponse:
    async def event_stream():
        usage = None
        try:
            async for chunk in provider.stream_chat(chat_request):
                if chunk.type == "text" and chunk.text:
                    yield _sse(
                        _chunk_payload(
                            request_id, created, model, content=chunk.text
                        )
                    )
                elif chunk.type == "usage":
                    usage = chunk.usage
                elif chunk.type == "error":
                    raise errors.ProviderError(chunk.error or "provider error")
        except errors.ProviderError as exc:
            mapped = _provider_error(exc)
            yield _sse(json.loads(mapped.body.decode("utf-8")))
            yield "data: [DONE]\n\n"
            return
        except Exception:  # noqa: BLE001 - never leak upstream details
            logger.warning("upstream chat stream failed", exc_info=True)
            yield _sse(
                _error_body(
                    "upstream provider request failed",
                    "upstream_error",
                    "UPSTREAM_ERROR",
                )
            )
            yield "data: [DONE]\n\n"
            return

        final = _chunk_payload(request_id, created, model, finish_reason="stop")
        if usage is not None:
            final["usage"] = usage
        final["kb_citations"] = citations
        yield _sse(final)
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@router.get("/models")
def list_models(request: Request) -> dict:
    settings = _settings(request)
    conn = request.app.state.db
    row = registry.select_provider(conn, settings)

    model_ids: list[str] = []
    if row is not None:
        cached = conn.execute(
            "SELECT models_json FROM model_cache WHERE provider_id = ?",
            (row["id"],),
        ).fetchone()
        if cached:
            try:
                entries = json.loads(cached["models_json"])
            except (TypeError, ValueError):
                entries = []
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id"):
                        model_ids.append(str(entry["id"]))
                    elif isinstance(entry, str) and entry:
                        model_ids.append(entry)
        if not model_ids and str(row["model"] or "").strip():
            model_ids = [str(row["model"]).strip()]

    created = _created()
    return {
        "object": "list",
        "data": [
            {
                "id": model_id,
                "object": "model",
                "created": created,
                "owned_by": "local-kb",
            }
            for model_id in model_ids
        ],
    }


@router.post("/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed JSON is a client error
        return _error(400, "request body must be valid JSON", code="INVALID_JSON")
    if not isinstance(payload, dict):
        return _error(400, "request body must be a JSON object", code="INVALID_JSON")

    messages = _parse_messages(payload.get("messages"))
    if not messages:
        return _error(
            400, "messages is required", code="MESSAGES_REQUIRED"
        )

    settings = _settings(request)
    conn = request.app.state.db
    row = registry.select_provider(conn, settings)
    if row is None:
        return _error(
            400, "no provider is configured", code="PROVIDER_NOT_CONFIGURED"
        )

    try:
        config = registry.load_config(row)
        provider = factory.create_provider(config)
    except errors.ProviderConfigError:
        return _error(
            400,
            "provider configuration is invalid",
            code="INVALID_PROVIDER_CONFIG",
        )
    except Exception:  # noqa: BLE001 - keyring/loading failures are opaque
        logger.warning("loading provider failed", exc_info=True)
        return _error(
            502, "provider could not be loaded", "upstream_error", "PROVIDER_ERROR"
        )

    requested_model = payload.get("model")
    if isinstance(requested_model, str) and requested_model.strip():
        model = requested_model.strip()
    else:
        model = config.model

    stream = bool(payload.get("stream", False))
    temperature = _number(payload.get("temperature"))
    max_tokens = _max_tokens(payload.get("max_tokens"))

    rag_mode = str(settings.get("gateway_rag_mode") or "auto").lower()
    contexts: list[dict] = []
    if rag_mode == "auto":
        question = _last_user_text(messages)
        contexts = retrieve_context(
            getattr(request.app.state, "retriever", None),
            question,
            _top_k(settings),
        )
        request_id = _completion_id()
        if not contexts:
            created = _created()
            if stream:
                return _no_answer_stream(request_id, created, model)
            return _no_answer_non_stream(request_id, created, model)
        messages = inject_context(messages, contexts)

    citations = citations_from_chunks(contexts) if rag_mode == "auto" else []

    chat_request = ChatRequest(
        model=model or config.model,
        messages=messages,
        stream=stream,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    request_id = _completion_id()
    created = _created()
    used_model = chat_request.model or config.model or "unknown"

    if stream:
        return _stream_response(
            provider, chat_request, request_id, created, used_model, citations
        )
    return await _non_stream_response(
        provider, chat_request, request_id, created, used_model, citations
    )
