from app.ingest.chunker import chunk_text


def test_chunk_respects_max_size_and_overlap():
    text = "第一段。" * 100 + "\n\n" + "第二段。" * 100
    chunks = chunk_text(text, max_chars=120, overlap=20)
    assert len(chunks) >= 2
    assert all(len(c.text) <= 120 for c in chunks)
    assert all(c.text.strip() for c in chunks)


def test_chunk_offsets_point_into_normalized_text():
    text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
    chunks = chunk_text(text, max_chars=20, overlap=4)
    assert chunks
    for index, chunk in enumerate(chunks):
        assert chunk.chunk_index == index
        assert chunk.start_offset < chunk.end_offset
        assert text[chunk.start_offset : chunk.end_offset] == chunk.text
        assert len(chunk.text) <= 20
    assert chunks[-1].text.endswith("第三段内容。")


def test_chunks_overlap_at_boundaries():
    text = "句子内容。" * 50
    chunks = chunk_text(text, max_chars=40, overlap=10)
    assert len(chunks) > 1
    for previous, chunk in zip(chunks, chunks[1:]):
        assert chunk.start_offset < previous.end_offset
        assert previous.end_offset - chunk.start_offset == 10


def test_empty_text_returns_no_chunks():
    assert chunk_text("   \n\n  ") == []
