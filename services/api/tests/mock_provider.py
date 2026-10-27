"""Deterministic in-process provider for golden-set evaluation and tests.

The mock never opens a socket and never reads credentials. It parses the
retrieved context blocks that :func:`app.qa.prompt.build_messages` embeds in the
user message (``[file.md §1]`` followed by the chunk text) and echoes each chunk
with its citation label. That makes every emitted citation valid against the
retrieved chunks, so the evaluation harness can check retrieval quality without
a real model.
"""

from __future__ import annotations

import re

from app.providers.base import ChatChunk, ChatRequest, ModelInfo

NO_ANSWER = "资料中没有找到。"

_LABEL = re.compile(r"\[([^\[\]\n]+)\]\n")
_QUESTION_MARKER = "\n\n问题："
_CONTEXT_PREFIX = "资料："


def parse_context_blocks(content: str) -> list[tuple[str, str]]:
    """Return ``(label, text)`` pairs from a rendered user prompt.

    Blocks look like ``[product.md §1]\ntext`` and are separated by a blank
    line. Anything after the ``问题：`` marker is ignored.
    """
    marker = content.find(_QUESTION_MARKER)
    context = content[:marker] if marker != -1 else content
    if context.startswith(_CONTEXT_PREFIX):
        context = context[len(_CONTEXT_PREFIX) :].lstrip("\n")

    labels = list(_LABEL.finditer(context))
    blocks: list[tuple[str, str]] = []
    for index, match in enumerate(labels):
        end = labels[index + 1].start() if index + 1 < len(labels) else len(context)
        text = context[match.end() : end].strip()
        if text:
            blocks.append((match.group(1), text))
    return blocks


class MockProvider:
    """Provider protocol implementation that only echoes retrieved evidence."""

    def __init__(self, model: str = "mock") -> None:
        self.model = model
        self.requests: list[ChatRequest] = []

    async def list_models(self) -> list[ModelInfo]:
        return [ModelInfo("mock")]

    async def embed(self, texts: list[str]) -> list[list[float]] | None:
        return None

    async def stream_chat(self, request: ChatRequest):
        self.requests.append(request)
        user = next(
            (message for message in reversed(request.messages) if message.role == "user"),
            None,
        )
        blocks = parse_context_blocks(user.content if user else "")
        if not blocks:
            yield ChatChunk(type="text", text=NO_ANSWER)
            return

        for index, (label, text) in enumerate(blocks):
            prefix = "" if index == 0 else "\n"
            yield ChatChunk(type="text", text=f"{prefix}{text} [{label}]")
