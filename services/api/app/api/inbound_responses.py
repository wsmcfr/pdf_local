"""Inbound OpenAI Responses-compatible gateway (``POST /v1/responses``).

The Responses API is event-based rather than choice-based: a non-streaming call
returns one ``response`` object, while a streaming call emits typed SSE events.
Request parsing and RAG behaviour intentionally mirror the Chat Completions
gateway so both headers share the same knowledge-base behaviour.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ..providers import errors, factory, registry
from ..providers.base import ChatChunk, ChatMessage, ChatRequest
from ..qa.rag import citations_from_chunks, inject_context, retrieve_context
from .gateway_auth import require_gateway
from .inbound_openai import (
    NO_ANSWER,
    _content_text,
    _error,
    _last_user_text,
    _max_tokens,
    _number,
    _provider_error,
    _settings,
    _top_k,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_gateway)])


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------
def _part_text(part) -> str:
    """Extract text from one Responses content part."""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
        if isinstance(text, str):
            return text
    return ""


def _input_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _part_text(content)
    return _content_text(content)


def _parse_input(raw) -> list[ChatMessage]:
    """Convert a Responses ``input`` value into chat messages.

    A plain string becomes a single user message. An array accepts message
    objects (``role`` + ``content``), bare strings, and content parts. Content
    parts may be strings or objects carrying ``text`` / ``input_text`` text.
    """
    messages: list[ChatMessage] = []

    if isinstance(raw, str):
        return [ChatMessage(role="user", content=raw)]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return messages

    for item in raw:
        if isinstance(item, str):
            messages.append(ChatMessage(role="user", content=item))
            continue
        if not isinstance(item, dict):
            continue

        content = item.get("content")
        if content is None:
            # Tolerate a bare content part such as {"type": "input_text", ...}.
            text = _part_text(item)
            if text:
                messages.append(ChatMessage(role="user", content=text))
            continue

        role = item.get("role")
        if not isinstance(role, str) or not role.strip():
            role = "user"
        messages.append(
            ChatMessage(role=role.strip(), content=_input_content_text(content))
        )
    return messages


def _instructions_text(instructions) -> str:
    if isinstance(instructions, str):
        return instructions
    if isinstance(instructions, list):
        return "\n".join(
            text for text in (_part_text(part) for part in instructions) if text
        )
    return ""


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------
def _response_id() -> str:
    return f"resp_{uuid.uuid4().hex}"


def _message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _created() -> int:
    return int(time.time())


def _part(text: str) -> dict:
    return {"type": "output_text", "text": text, "annotations": []}


def _message_item(message_id: str, *, status: str, content: list[dict]) -> dict:
    return {
        "id": message_id,
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": content,
    }


def _response_object(
    response_id: str,
    created: int,
    model: str,
    *,
    status: str,
) -> dict:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created,
        "status": status,
        "model": model,
    }


def _response_body(
    response_id: str,
    message_id: str,
    created: int,
    model: str,
    text: str,
    citations: list[dict],
    usage: dict | None = None,
    status: str = "completed",
) -> dict:
    body = _response_object(response_id, created, model, status=status)
    body["output"] = [
        _message_item(message_id, status="completed", content=[_part(text)])
    ]
    body["output_text"] = text
    body["kb_citations"] = citations
    if usage is not None:
        body["usage"] = usage
    return body


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _failure_info(exc: Exception) -> dict:
    """Map an upstream failure to a client-safe ``response.failed`` error."""
    mapped = _provider_error(exc) if isinstance(exc, errors.ProviderError) else None
    if mapped is None:
        return {
            "code": "UPSTREAM_ERROR",
            "type": "upstream_error",
            "message": "upstream provider request failed",
        }
    try:
        body = json.loads(mapped.body.decode("utf-8"))
    except (TypeError, ValueError):  # pragma: no cover - defensive
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        error = {}
    info: dict = {
        "code": error.get("code") or "UPSTREAM_ERROR",
        "type": error.get("type") or "upstream_error",
        "message": error.get("message") or "upstream provider request failed",
    }
    return info


def _no_answer_body(
    response_id: str, message_id: str, created: int, model: str
) -> dict:
    return _response_body(
        response_id, message_id, created, model, NO_ANSWER, []
    )


async def _static_chunks(text: str):
    yield ChatChunk(type="text", text=text)
    yield ChatChunk(type="done")


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------
async def _non_stream_response(
    provider,
    chat_request: ChatRequest,
    response_id: str,
    message_id: str,
    created: int,
    model: str,
    citations: list[dict],
) -> JSONResponse:
    text = ""
    usage = None
    try:
        async for chunk in provider.stream_chat(chat_request):
            if chunk.type == "text":
                text += chunk.text or ""
            elif chunk.type == "usage":
                usage = chunk.usage
            elif chunk.type == "error":
                raise errors.ProviderError(chunk.error or "provider error")
    except errors.ProviderError as exc:
        return _provider_error(exc)
    except Exception:  # noqa: BLE001 - generic upstream failure, no stack leak
        logger.warning("upstream responses request failed", exc_info=True)
        return _error(
            502,
            "upstream provider request failed",
            "upstream_error",
            "UPSTREAM_ERROR",
        )

    return JSONResponse(
        _response_body(
            response_id,
            message_id,
            created,
            model,
            text,
            citations,
            usage=usage,
        )
    )


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def _stream_response(
    chunks,
    response_id: str,
    message_id: str,
    created: int,
    model: str,
    citations: list[dict],
) -> StreamingResponse:
    async def event_stream():
        sequence = 0

        def payload(event_type: str, **fields) -> dict:
            nonlocal sequence
            data = {"type": event_type, "sequence_number": sequence}
            sequence += 1
            data.update(fields)
            return data

        yield _sse_event(
            "response.created",
            payload(
                "response.created",
                response=_response_object(
                    response_id, created, model, status="in_progress"
                ),
            ),
        )
        yield _sse_event(
            "response.output_item.added",
            payload(
                "response.output_item.added",
                output_index=0,
                item=_message_item(message_id, status="in_progress", content=[]),
            ),
        )
        yield _sse_event(
            "response.content_part.added",
            payload(
                "response.content_part.added",
                item_id=message_id,
                output_index=0,
                content_index=0,
                part=_part(""),
            ),
        )

        text = ""
        usage = None
        try:
            async for chunk in chunks:
                if chunk.type == "text" and chunk.text:
                    text += chunk.text
                    yield _sse_event(
                        "response.output_text.delta",
                        payload(
                            "response.output_text.delta",
                            item_id=message_id,
                            output_index=0,
                            content_index=0,
                            delta=chunk.text,
                        ),
                    )
                elif chunk.type == "usage":
                    usage = chunk.usage
                elif chunk.type == "error":
                    raise errors.ProviderError(chunk.error or "provider error")
        except Exception as exc:  # noqa: BLE001 - never leak upstream details
            if not isinstance(exc, errors.ProviderError):
                logger.warning("upstream responses stream failed", exc_info=True)
            failed = _response_object(
                response_id, created, model, status="failed"
            )
            failed["error"] = _failure_info(exc)
            yield _sse_event(
                "response.failed", payload("response.failed", response=failed)
            )
            return

        yield _sse_event(
            "response.output_text.done",
            payload(
                "response.output_text.done",
                item_id=message_id,
                output_index=0,
                content_index=0,
                text=text,
            ),
        )
        yield _sse_event(
            "response.content_part.done",
            payload(
                "response.content_part.done",
                item_id=message_id,
                output_index=0,
                content_index=0,
                part=_part(text),
            ),
        )
        yield _sse_event(
            "response.output_item.done",
            payload(
                "response.output_item.done",
                output_index=0,
                item=_message_item(
                    message_id, status="completed", content=[_part(text)]
                ),
            ),
        )
        yield _sse_event(
            "response.completed",
            payload(
                "response.completed",
                response=_response_body(
                    response_id,
                    message_id,
                    created,
                    model,
                    text,
                    citations,
                    usage=usage,
                ),
            ),
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.post("/responses")
async def create_response(request: Request):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed JSON is a client error
        return _error(400, "request body must be valid JSON", code="INVALID_JSON")
    if not isinstance(payload, dict):
        return _error(400, "request body must be a JSON object", code="INVALID_JSON")

    parsed = _parse_input(payload.get("input"))
    if not parsed:
        return _error(400, "input is required", code="INPUT_REQUIRED")

    messages = parsed
    instructions = _instructions_text(payload.get("instructions"))
    if instructions:
        messages = [ChatMessage(role="system", content=instructions), *messages]

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
    max_tokens = _max_tokens(payload.get("max_output_tokens"))

    response_id = _response_id()
    message_id = _message_id()
    created = _created()

    rag_mode = str(settings.get("gateway_rag_mode") or "auto").lower()
    contexts: list[dict] = []
    if rag_mode == "auto":
        question = _last_user_text(messages)
        contexts = retrieve_context(
            getattr(request.app.state, "retriever", None),
            question,
            _top_k(settings),
        )
        if not contexts:
            if stream:
                return _stream_response(
                    _static_chunks(NO_ANSWER),
                    response_id,
                    message_id,
                    created,
                    model or config.model or "unknown",
                    [],
                )
            return JSONResponse(
                _no_answer_body(
                    response_id,
                    message_id,
                    created,
                    model or config.model or "unknown",
                )
            )
        messages = inject_context(messages, contexts)

    citations = citations_from_chunks(contexts) if rag_mode == "auto" else []

    chat_request = ChatRequest(
        model=model or config.model,
        messages=messages,
        stream=stream,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    used_model = chat_request.model or config.model or "unknown"

    if stream:
        return _stream_response(
            provider.stream_chat(chat_request),
            response_id,
            message_id,
            created,
            used_model,
            citations,
        )
    return await _non_stream_response(
        provider,
        chat_request,
        response_id,
        message_id,
        created,
        used_model,
        citations,
    )
