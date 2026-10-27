#!/usr/bin/env python3
"""Download BGE-M3 into the local KB data directory (default <repo>/data).

The model is ~2.2GB. This script is intentionally not run automatically by the
app; the onboarding UI only tells the user how to run it.

Everything (model files and the Hugging Face metadata cache) is written under
the data directory, so nothing ends up in ``C:\\Users\\<user>\\.cache``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.index.embeddings import MODEL_NAME, model_available  # noqa: E402


def data_dir() -> Path:
    override = os.environ.get("KB_DATA_DIR")
    if override:
        return Path(override)
    if (ROOT / "services" / "api").is_dir() and (ROOT / "apps").is_dir():
        return ROOT / "data"
    return Path.home() / ".local-kb"


def target_dir() -> Path:
    return data_dir() / "models" / "bge-m3"


def configure_cache() -> Path:
    """Point every Hugging Face cache at the data directory.

    This must run before ``huggingface_hub`` is imported; the library reads
    these variables at import time. The values are set explicitly (not via
    ``setdefault``) so a global ``HF_HOME`` pointing at C: does not win.
    """
    cache_root = data_dir() / "hf-cache"
    os.environ["HF_HOME"] = str(cache_root)
    os.environ["HF_HUB_CACHE"] = str(cache_root / "hub")
    os.environ["TRANSFORMERS_CACHE"] = str(cache_root / "transformers")
    os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
    return cache_root


def main() -> int:
    target = target_dir()
    if model_available(target):
        print(f"embedding model already present: {target}")
        return 0

    cache_root = configure_cache()
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print(
            "huggingface_hub is not installed. "
            "Run: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    # ``local_dir`` writes the files straight into the target directory instead
    # of caching a second copy in the hub cache (saves ~2.2GB of disk).
    target.mkdir(parents=True, exist_ok=True)
    print(f"cache: {cache_root}", flush=True)
    print(f"downloading {MODEL_NAME} (~2.2GB) to {target} ...", flush=True)
    snapshot_download(repo_id=MODEL_NAME, local_dir=str(target))

    if not model_available(target):
        print(
            f"download finished but no model weights were found in {target}",
            file=sys.stderr,
        )
        return 1
    print(f"embedding model saved to {target}", flush=True)
    print(
        "restart the app (or re-run the backend) to load it from the data directory",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
