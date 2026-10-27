from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-m3"


def model_source() -> str:
    """Return the local BGE-M3 directory when present, otherwise the hub name.

    ``KB_EMBEDDING_MODEL_PATH`` is set by the FastAPI lifespan to
    ``<data_dir>/models/bge-m3``. If that directory has not been downloaded yet
    we fall back to the Sentence Transformers model name (requires network on
    first use).
    """
    override = os.environ.get("KB_EMBEDDING_MODEL_PATH")
    if override:
        candidate = Path(override)
        if candidate.is_dir() and any(candidate.iterdir()):
            return str(candidate)
    return MODEL_NAME


@lru_cache(maxsize=1)
def get_model() -> "SentenceTransformer":
    """Load BGE-M3 lazily on the first encode call.

    Importing ``sentence_transformers`` (and the model itself) is deferred so
    importing this module never downloads or loads the ~2.2GB model.
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_source(), device="cpu")


def encode(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    model = get_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
