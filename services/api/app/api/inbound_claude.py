"""Inbound Claude-compatible gateway (``POST /v1/messages``).

The request shape mirrors the Anthropic Messages API closely enough for
standard Claude clients: ``messages`` content may be a string or text blocks,
``system`` may be a string or content blocks, and ``max_tokens`` is required by
Claude (defaulted to 1024 when omitted). Responses and streaming events follow
the Claude SSE vocabulary, and errors use the Claude error envelope.

Knowledge-base behaviour is shared with the OpenAI inbound gateway: ``auto``
retrieves and injects context, ``off``/``tools`` proxy the request untouched.
"""

from __future__ import annotations

import json
import logging
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
    _last_user_text,
    _number,
    _parse_messages,
    _settings,
    _top_k,
)

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(require_gateway)])

DEFAULT_MAX_TOKENS = 1024
STOP_REASON = "end_turn"


# ---------------------------------------------------------------------------
# Claude-shaped errors
# ---------------------------------------------------------------------------
def _error_body(message: str, error_type: str) -> dict:
    return {"type": "error", "error": {"type": error_type, "message": message}}


def _error(
    status: int, message: str, error_type: str = "invalid_request_error"
) -> JSONResponse:
    return JSONResponse(status_code=status, content=_error_body(message, error_type))


def _provider_error(exc: errors.ProviderError) -> JSONResponse:
    """Map adapter failures to Claude error types without leaking details."""
    if isinstance(exc, errors.ProviderAuthError):
        return _error(
            401, "upstream provider rejected the credentials", "authentication_error"
        )
    if isinstance(exc, errors.ProviderRateLimitError):
        return _error(429, "upstream provider rate limit exceeded", "rate_limit_error")
    if isinstance(exc, errors.ProviderTimeoutError):
        return _error(504, "upstream provider timed out", "timeout_error")
    if isinstance(exc, errors.ProviderNotFoundError):
        return _error(404, "upstream provider endpoint not found", "not_found_error")
    if isinstance(exc, errors.ProviderConfigError):
        return _error(400, "provider configuration is invalid", "invalid_request_error")
    return _error(502, "upstream provider request failed", "api_error")


def _failure_body(exc: Exception) -> dict:
    """Client-safe body for an error raised mid-stream."""
    if isinstance(exc, errors.ProviderError):
        mapped = _provider_error(exc)
        try:
            return json.loads(mapped.body.decode("utf-8"))
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass
    return _error_body("upstream provider request failed", "api_error")


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------
def _system_text(system) -> str:
    """Flatten a Claude ``system`` string or content-block array to text."""
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return _content_text(system)
    if isinstance(system, dict):
        return _content_text([system])
    return ""


def _parse_max_tokens(value):
    """Return a positive int, or ``None`` when the caller sent an invalid value."""
    if value is None:
        return DEFAULT_MAX_TOKENS
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


# ---------------------------------------------------------------------------
# Response builders
# ---------------------------------------------------------------------------
def _message_id() -> str:
    return f"msg_{uuid.uuid4().hex}"


def _usage_dict(usage) -> dict:
    """Normalise provider usage to Claude's ``input_tokens``/``output_tokens``."""
    source = usage if isinstance(usage, dict) else {}
    result: dict = {}
    for key in ("input_tokens", "output_tokens"):
        value = source.get(key)
        valid = isinstance(value, int) and not isinstance(value, bool)
        result[key] = value if valid else 0
    return result


def _message_object(
    message_id: str,
    model: str,
    *,
    text: str,
    citations: list[dict],
    usage: dict | None,
    stop_reason: str | None,
) -> dict:
    message = {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": _usage_dict(usage),
        "metadata": {"kb_citations": citations},
    }
    return message


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _static_chunks(text: str):
    yield ChatChunk(type="text", text=text)
    yield ChatChunk(type="done")


# ---------------------------------------------------------------------------
# Non-streaming
# ---------------------------------------------------------------------------
async def _non_stream_response(
    provider,
    chat_request: ChatRequest,
    message_id: str,
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
        logger.warning("upstream claude messages request failed", exc_info=True)
        return _error(502, "upstream provider request failed", "api_error")

    return JSONResponse(
        _message_object(
            message_id,
            model,
            text=text,
            citations=citations,
            usage=usage,
            stop_reason=STOP_REASON,
        )
    )


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------
def _stream_response(
    chunks,
    message_id: str,
    model: str,
    citations: list[dict],
) -> StreamingResponse:
    async def event_stream():
        # Input usage is only known once the upstream finishes; Claude clients
        # tolerate a zeroed value here, mirroring providers that omit it.
        start_message = {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "metadata": {"kb_citations": citations},
        }
        yield _sse("message_start", {"type": "message_start", "message": start_message})
        yield _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        )

        usage = None
        try:
            async for chunk in chunks:
                if chunk.type == "text" and chunk.text:
                    yield _sse(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": chunk.text},
                        },
                    )
                elif chunk.type == "usage":
                    usage = chunk.usage
                elif chunk.type == "error":
                    raise errors.ProviderError(chunk.error or "provider error")
        except Exception as exc:  # noqa: BLE001 - never leak upstream details
            if not isinstance(exc, errors.ProviderError):
                logger.warning("upstream claude stream failed", exc_info=True)
            yield _sse("error", _failure_body(exc))
            return

        yield _sse(
            "content_block_stop", {"type": "content_block_stop", "index": 0}
        )
        yield _sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": STOP_REASON, "stop_sequence": None},
                "usage": {"output_tokens": _usage_dict(usage)["output_tokens"]},
            },
        )
        yield _sse("message_stop", {"type": "message_stop"})

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------
@router.post("/messages")
async def create_message(request: Request):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed JSON is a client error
        return _error(400, "request body must be valid JSON")
    if not isinstance(payload, dict):
        return _error(400, "request body must be a JSON object")

    messages = _parse_messages(payload.get("messages"))
    if not messages:
        return _error(400, "messages is required")

    max_tokens = _parse_max_tokens(payload.get("max_tokens"))
    if max_tokens is None:
        return _error(400, "max_tokens must be a positive integer")

    system_text = _system_text(payload.get("system"))
    if system_text:
        messages = [ChatMessage(role="system", content=system_text), *messages]

    settings = _settings(request)
    conn = request.app.state.db
    row = registry.select_provider(conn, settings)
    if row is None:
        return _error(400, "no provider is configured")

    try:
        config = registry.load_config(row)
        provider = factory.create_provider(config)
    except errors.ProviderConfigError:
        return _error(400, "provider configuration is invalid")
    except Exception:  # noqa: BLE001 - keyring/loading failures are opaque
        logger.warning("loading provider failed", exc_info=True)
        return _error(502, "provider could not be loaded", "api_error")

    requested_model = payload.get("model")
    if isinstance(requested_model, str) and requested_model.strip():
        model = requested_model.strip()
    else:
        model = config.model

    stream = bool(payload.get("stream", False))
    temperature = _number(payload.get("temperature"))

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
            used_model = model or config.model or "unknown"
            message_id = _message_id()
            if stream:
                return _stream_response(
                    _static_chunks(NO_ANSWER), message_id, used_model, []
                )
            return JSONResponse(
                _message_object(
                    message_id,
                    used_model,
                    text=NO_ANSWER,
                    citations=[],
                    usage=None,
                    stop_reason=STOP_REASON,
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
    message_id = _message_id()
    used_model = chat_request.model or config.model or "unknown"

    if stream:
        return _stream_response(
            provider.stream_chat(chat_request), message_id, used_model, citations
        )
    return await _non_stream_response(
        provider, chat_request, message_id, used_model, citations
    )
