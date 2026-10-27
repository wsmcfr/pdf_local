from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..utils.hashing import sha256_file
from ..utils.paths import is_supported


@dataclass(frozen=True)
class ScannedFile:
    path: Path
    root: Path
    content_hash: str
    size_bytes: int
    mtime: float


def scan_folder(root: Path) -> Iterator[ScannedFile]:
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not is_supported(path):
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        stat = path.stat()
        yield ScannedFile(
            path=path,
            root=root,
            content_hash=sha256_file(path),
            size_bytes=stat.st_size,
            mtime=stat.st_mtime,
        )
