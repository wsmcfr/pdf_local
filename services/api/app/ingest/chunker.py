from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from ..utils.text import normalize_text

_SENTENCE_BOUNDARY = re.compile(r"[。！？!?；;\n]+")


@dataclass
class Chunk:
    chunk_index: int
    text: str
    start_offset: int
    end_offset: int


def _paragraph_spans(text: str) -> list[tuple[int, int]]:
    """Return trimmed paragraph spans in ``text``."""
    spans: list[tuple[int, int]] = []
    cursor = 0
    for paragraph in text.split("\n\n"):
        if not paragraph.strip():
            continue
        start = text.find(paragraph, cursor)
        if start == -1:
            start = cursor
        end = start + len(paragraph)
        cursor = end
        spans.append((start, end))
    return spans


def _split_long_spans(
    text: str, spans: list[tuple[int, int]], max_chars: int
) -> list[tuple[int, int]]:
    """Make every span at most ``max_chars`` wide.

    Fitting paragraphs are kept whole. Longer paragraphs are split at the
    first sentence boundary inside a ``max_chars`` window so that units stay
    small; only a boundary-less run is hard-cut at ``max_chars``.
    """
    units: list[tuple[int, int]] = []
    for start, end in spans:
        if end - start <= max_chars:
            units.append((start, end))
            continue

        cursor = start
        while cursor < end:
            window = text[cursor : min(cursor + max_chars, end)]
            match = _SENTENCE_BOUNDARY.search(window)
            cut = cursor + (match.end() if match else len(window))
            if cut <= cursor:  # defensive, cannot happen with a non-empty window
                cut = cursor + len(window)
            units.append((cursor, cut))
            cursor = cut
    return units


def _emit(chunks: list[Chunk], text: str, start: int, end: int) -> None:
    raw = text[start:end]
    if not raw.strip():
        return
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    clean_start = start + leading
    clean_end = end - trailing
    if clean_end <= clean_start:
        return
    chunks.append(
        Chunk(
            chunk_index=len(chunks),
            text=text[clean_start:clean_end],
            start_offset=clean_start,
            end_offset=clean_end,
        )
    )


def chunk_text(text: str, max_chars: int = 800, overlap: int = 80) -> list[Chunk]:
    """Split text into paragraph-aware chunks of at most ``max_chars``.

    Paragraphs longer than ``max_chars`` are first split at sentence
    boundaries (falling back to a hard cut). Pieces are then packed greedily
    into a chunk; the next chunk starts ``overlap`` characters before the end
    of the current one so context is not lost at the boundary. A chunk may
    therefore start mid-sentence, which is acceptable for retrieval.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    overlap = max(0, min(overlap, max_chars - 1))

    normalized = normalize_text(text)
    if not normalized:
        return []

    pieces = _split_long_spans(
        normalized, _paragraph_spans(normalized), max_chars
    )
    if not pieces:
        return []

    piece_ends = [end for _, end in pieces]
    total = len(pieces)
    chunks: list[Chunk] = []
    start = 0
    while start < len(normalized):
        first = bisect_right(piece_ends, start)
        if first >= total:
            break

        chunk_start = start
        end = start
        index = first
        while index < total and pieces[index][1] - chunk_start <= max_chars:
            end = pieces[index][1]
            index += 1
        if end <= chunk_start:  # defensive: a single piece exceeded the limit
            end = min(len(normalized), chunk_start + max_chars)

        _emit(chunks, normalized, chunk_start, end)
        if end >= len(normalized):
            break
        start = max(chunk_start + 1, end - overlap)

    return chunks
