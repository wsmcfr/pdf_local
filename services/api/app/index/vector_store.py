from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np

DEFAULT_DIMENSION = 1024


class VectorStore:
    """FAISS inner-product index with explicit int64 ids.

    Vectors are expected to be L2-normalized (BGE-M3 with
    ``normalize_embeddings=True``), so inner product is cosine similarity.
    """

    def __init__(self, path: Path, dimension: int = DEFAULT_DIMENSION) -> None:
        self.path = Path(path)
        self.dimension = dimension
        self.index = self._load()

    def _load(self) -> faiss.Index:
        if self.path.exists():
            return faiss.read_index(str(self.path))
        return faiss.IndexIDMap2(faiss.IndexFlatIP(self.dimension))

    def add(
        self, ids: list[int], vectors: list[list[float]], save: bool = True
    ) -> None:
        if not ids:
            return
        array = np.asarray(vectors, dtype="float32")
        self.index.add_with_ids(array, np.asarray(ids, dtype="int64"))
        if save:
            self.save()

    def search(
        self, vector: list[float], limit: int = 20
    ) -> list[tuple[int, float]]:
        if self.index.ntotal == 0 or limit <= 0:
            return []
        array = np.asarray([vector], dtype="float32")
        scores, ids = self.index.search(array, limit)
        return [
            (int(index_id), float(score))
            for index_id, score in zip(ids[0], scores[0])
            if index_id != -1
        ]

    def remove(self, ids: list[int], save: bool = True) -> None:
        if not ids:
            return
        self.index.remove_ids(np.asarray(ids, dtype="int64"))
        if save:
            self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.path))
