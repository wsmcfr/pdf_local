from pathlib import Path

SUPPORTED_SUFFIXES = {
    ".pdf",
    ".md",
    ".markdown",
    ".txt",
    ".docx",
    ".pptx",
    ".xlsx",
}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def ensure_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
