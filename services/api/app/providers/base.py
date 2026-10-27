from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict = field(default_factory=dict)


@dataclass
class ChatChunk:
    type: Literal["text", "usage", "done", "error"]
    text: str = ""
    usage: dict | None = None
    error: str | None = None


@dataclass
class ModelInfo:
    id: str
    name: str | None = None


class Provider(Protocol):
    async def list_models(self) -> list[ModelInfo]: ...

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...

    async def embed(self, texts: list[str]) -> list[list[float]] | None: ...
