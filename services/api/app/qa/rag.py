"""RAG helpers for the inbound gateway.

Retrieval degrades to an empty context on errors, and context formatting reuses
the same citation labels as the local Q&A flow so upstream models cite sources
in a consistent shape.
"""

from __future__ import annotations

import logging

from ..providers.base import ChatMessage
from .citations import citation_kind, citation_label
from .prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)


def retrieve_context(retriever, question: str, top_k: int = 8) -> list[dict]:
    """Retrieve chunks for ``question``; failures yield an empty list."""
    if retriever is None or top_k <= 0 or not (question or "").strip():
        return []
    try:
        results = retriever.search(question, limit=top_k)
    except Exception:  # noqa: BLE001 - never fail the gateway on retrieval
        logger.warning("RAG retrieval failed", exc_info=True)
        return []
    return [item for item in (results or []) if isinstance(item, dict)]


def _source(item: dict) -> tuple[str, int, str | None] | None:
    file_name = item.get("file_name")
    page_no = item.get("page_no")
    if file_name is None or page_no is None:
        return None
    try:
        page = int(page_no)
    except (TypeError, ValueError):
        return None
    return str(file_name), page, item.get("mime")


def format_context(contexts: list[dict]) -> str:
    blocks: list[str] = []
    for item in contexts:
        source = _source(item)
        text = item.get("text")
        if source is None or not isinstance(text, str) or not text:
            continue
        file_name, page_no, mime = source
        blocks.append(
            f"[{citation_label(file_name, page_no, mime)}]\n{text}"
        )
    return "\n\n".join(blocks)


def inject_context(
    messages: list[ChatMessage], contexts: list[dict]
) -> list[ChatMessage]:
    """Prepend one system message with retrieved context.

    The caller's messages are preserved unchanged; a new list is returned.
    """
    context_text = format_context(contexts)
    if not context_text:
        return list(messages)
    system = ChatMessage(
        role="system",
        content=f"{SYSTEM_PROMPT}\n资料：\n{context_text}",
    )
    return [system, *messages]


def citations_from_chunks(contexts: list[dict]) -> list[dict]:
    """Return client-safe citation metadata for retrieved chunks."""
    citations: list[dict] = []
    seen: set[tuple] = set()
    for item in contexts:
        source = _source(item)
        if source is None:
            continue
        file_name, page_no, mime = source
        key = (item.get("chunk_id"), file_name, page_no)
        if key in seen:
            continue
        seen.add(key)

        citation = {
            "file_name": file_name,
            "page_no": page_no,
            "kind": citation_kind(file_name, mime),
        }
        if item.get("chunk_id"):
            citation["chunk_id"] = str(item["chunk_id"])
        if item.get("document_id"):
            citation["document_id"] = str(item["document_id"])
        citations.append(citation)
    return citations
