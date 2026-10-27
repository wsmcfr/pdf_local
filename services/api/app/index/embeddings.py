from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-m3"


class LocalModelUnavailable(RuntimeError):
    """The local BGE-M3 directory is missing, empty, or incomplete."""


def model_available(path: str | os.PathLike[str] | None) -> bool:
    """Return True when ``path`` contains real model weights.

    Checking for weight files (instead of just a non-empty directory) avoids
    treating an interrupted download or a half-copied folder as usable and
    then spending seconds importing torch only to fail.
    """
    if not path:
        return False
    candidate = Path(path)
    if not candidate.is_dir():
        return False

    try:
        for pattern in ("*.safetensors", "*.bin"):
            for file in candidate.rglob(pattern):
                if file.is_file() and file.suffix != ".incomplete":
                    return True
    except OSError:  # pragma: no cover - unreadable directory
        return False
    return False


def local_model_available() -> bool:
    """Return True when ``KB_EMBEDDING_MODEL_PATH`` points at usable weights."""
    return model_available(os.environ.get("KB_EMBEDDING_MODEL_PATH"))


def model_source() -> str:
    """Return the local BGE-M3 directory.

    Never silently download from the Hugging Face hub: the app is local-first
    and the model is large. Users explicitly run ``scripts/download_embedding.py``
    (which stores it under the chosen data directory) or switch to external/FTS.
    """
    override = os.environ.get("KB_EMBEDDING_MODEL_PATH")
    if override and local_model_available():
        return override
    raise LocalModelUnavailable(
        "local BGE-M3 model is not downloaded; run scripts/download_embedding.py"
    )


@lru_cache(maxsize=1)
def get_model() -> "SentenceTransformer":
    """Load BGE-M3 lazily on the first encode call.

    ``model_source()`` runs before importing ``sentence_transformers`` so a
    missing model fails in milliseconds instead of importing torch first. The
    import itself is deferred too, so importing this module never downloads or
    loads the ~2.2GB model.
    """
    source = model_source()
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(source, device="cpu")


def encode(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    model = get_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
