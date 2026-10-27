from __future__ import annotations


def rrf_fuse(
    keyword_results: list[dict],
    vector_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """Fuse two ranked lists with reciprocal rank fusion.

    An item that appears in both lists accumulates two contributions, so it
    normally outranks items found by only one retriever. The payload of the
    first list that contained an item is preserved.
    """
    scores: dict[str, float] = {}
    payload: dict[str, dict] = {}

    for rank, item in enumerate(keyword_results, start=1):
        chunk_id = item["chunk_id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        payload.setdefault(chunk_id, item)

    for rank, item in enumerate(vector_results, start=1):
        chunk_id = item["chunk_id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        payload.setdefault(chunk_id, item)

    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return [
        {**payload[chunk_id], "rrf_score": score}
        for chunk_id, score in ordered
    ]
