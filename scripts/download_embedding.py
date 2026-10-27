#!/usr/bin/env python3
"""Download BGE-M3 into the local KB data directory (default ~/.local-kb).

The model is ~2.2GB. This script is intentionally not run automatically by the
app; the onboarding UI only tells the user how to run it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

MODEL_NAME = "BAAI/bge-m3"


def target_dir() -> Path:
    data_dir = Path(os.environ.get("KB_DATA_DIR") or (Path.home() / ".local-kb"))
    return data_dir / "models" / "bge-m3"


def main() -> int:
    target = target_dir()
    if target.is_dir() and any(target.iterdir()):
        print(f"embedding model already present: {target}")
        return 0

    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(
            "sentence-transformers is not installed. "
            "Run: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {MODEL_NAME} (~2.2GB) to {target} ...", flush=True)
    model = SentenceTransformer(MODEL_NAME, device="cpu")
    model.save(str(target))
    print(f"embedding model saved to {target}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
