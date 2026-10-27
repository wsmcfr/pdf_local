import asyncio

from app.providers.base import ChatMessage, ChatRequest
from mock_provider import MockProvider
from app.qa.answer import answer_question

CONTEXT = {
    "chunk_id": "c1",
    "document_id": "d1",
    "page_no": 1,
    "text": "产品 A 标准保修期为 24 个月。",
    "file_name": "product.md",
    "mime": "text/plain",
    "score": 1.0,
}

QUESTION = "产品 A 的保修期是多少个月？"


def collect(request):
    async def run():
        return [chunk async for chunk in MockProvider().stream_chat(request)]

    return asyncio.run(run())


def render_user_message():
    from app.qa.prompt import build_messages

    return build_messages(QUESTION, [CONTEXT])[1]["content"]


def test_mock_provider_echoes_context_with_citation():
    request = ChatRequest(
        model="mock",
        messages=[
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content=render_user_message()),
        ],
    )
    chunks = collect(request)
    text = "".join(chunk.text for chunk in chunks if chunk.type == "text")

    assert "产品 A 标准保修期为 24 个月。" in text
    assert "[product.md §1]" in text


def test_mock_provider_refuses_without_context_blocks():
    request = ChatRequest(
        model="mock",
        messages=[ChatMessage(role="user", content="问题：产品 A 的保修期是多少个月？")],
    )
    chunks = collect(request)

    assert [chunk.text for chunk in chunks] == ["资料中没有找到。"]


def test_mock_provider_answer_is_validated_by_answer_question():
    async def run():
        return [
            event
            async for event in answer_question(
                QUESTION, [CONTEXT], MockProvider(), "mock"
            )
        ]

    events = asyncio.run(run())
    names = [name for name, _ in events]
    assert names == ["token", "citations", "done"]
    citations = dict(events)["citations"]
    assert citations == {
        "citations": [
            {"file_name": "product.md", "page_no": 1, "kind": "markdown"}
        ],
        "invalid": [],
    }
