"""Filesystem defaults shared by the API and MCP stdio entry points."""

from __future__ import annotations

import os
from pathlib import Path


def default_data_dir() -> Path:
    """Return ``KB_DATA_DIR`` or a repo-local ``data`` directory in development.

    A packaged sidecar does not sit inside the repository, so it falls back to
    the user's home directory. In development this keeps the large model,
    FAISS index and page cache off the C: drive by default.
    """
    override = os.environ.get("KB_DATA_DIR")
    if override:
        return Path(override)

    repo_root = Path(__file__).resolve().parents[3]
    if (repo_root / "services" / "api").is_dir() and (repo_root / "apps").is_dir():
        return repo_root / "data"
    return Path.home() / ".local-kb"


def configure_huggingface_cache(data_dir: Path) -> None:
    """Keep Hugging Face downloads under the chosen data directory.

    This must run before ``sentence_transformers`` / ``huggingface_hub`` is
    imported for the environment variables to take effect.
    """
    cache_root = Path(data_dir) / "hf-cache"
    os.environ.setdefault("HF_HOME", str(cache_root))
    os.environ.setdefault("HF_HUB_CACHE", str(cache_root / "hub"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_root / "transformers"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
