from __future__ import annotations

from typing import AsyncIterator

from ..llm.ollama import stream_chat
from .citations import extract_citations, validate_citations
from .prompt import build_messages

NO_ANSWER = "资料中没有找到。"


async def answer_question(
    question: str,
    contexts: list[dict],
    model: str,
) -> AsyncIterator[tuple[str, object]]:
    """Stream an answer and its citation check.

    With no retrieved evidence the model is not called at all; the refusal is
    streamed as the only token so clients can render it like any other answer.
    """
    if not contexts:
        yield ("token", {"text": NO_ANSWER})
        yield ("citations", {"citations": [], "invalid": []})
        yield ("done", {"text": NO_ANSWER, "model": None})
        return

    messages = build_messages(question, contexts)
    full_text = ""
    async for token in stream_chat(model, messages):
        full_text += token
        yield ("token", {"text": token})

    citations = extract_citations(full_text)
    valid, invalid = validate_citations(citations, contexts)
    yield ("citations", {"citations": valid, "invalid": invalid})
    yield ("done", {"text": full_text, "model": model})
