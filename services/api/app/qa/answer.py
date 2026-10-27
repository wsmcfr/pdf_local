from __future__ import annotations

from typing import AsyncIterator

from ..providers.base import ChatMessage, ChatRequest, Provider
from ..providers.errors import ProviderError
from .citations import extract_citations, validate_citations
from .prompt import build_messages

NO_ANSWER = "资料中没有找到。"


async def answer_question(
    question: str,
    contexts: list[dict],
    provider: Provider,
    model: str,
) -> AsyncIterator[tuple[str, object]]:
    """Stream an answer and its citation check through a provider adapter.

    With no retrieved evidence the provider is not called at all; the refusal
    is streamed as the only token so clients can render it like any other
    answer. Provider failures are re-raised as ``ProviderError`` so the API
    layer can map them to safe SSE error codes.
    """
    if not contexts:
        yield ("token", {"text": NO_ANSWER})
        yield ("citations", {"citations": [], "invalid": []})
        yield ("done", {"text": NO_ANSWER, "model": None})
        return

    messages = [
        ChatMessage(role=message["role"], content=message["content"])
        for message in build_messages(question, contexts)
    ]
    request = ChatRequest(model=model, messages=messages, stream=True)

    full_text = ""
    async for chunk in provider.stream_chat(request):
        if chunk.type == "text":
            text = chunk.text or ""
            if text:
                full_text += text
                yield ("token", {"text": text})
        elif chunk.type == "error":
            raise ProviderError(chunk.error or "provider error")
        # Usage chunks are intentionally ignored by the desktop ask endpoint.

    citations = extract_citations(full_text)
    valid, invalid = validate_citations(citations, contexts)
    yield ("citations", {"citations": valid, "invalid": invalid})
    yield ("done", {"text": full_text, "model": model})
