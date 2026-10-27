from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

PDF_RE = re.compile(r"\[([^\[\]]+?)\s+p\.(\d+)\]")
MD_RE = re.compile(r"\[([^\[\]]+?)\s+§(\d+)\]")


def citation_kind(file_name: str, mime: str | None = None) -> str:
    """Return ``pdf`` for PDFs and ``markdown`` for everything else."""
    if mime == "application/pdf":
        return "pdf"
    if Path(file_name).suffix.lower() == ".pdf":
        return "pdf"
    return "markdown"


def citation_label(file_name: str, page_no: int, mime: str | None = None) -> str:
    """Format the source label the model must copy into its answer."""
    if citation_kind(file_name, mime) == "pdf":
        return f"{file_name} p.{int(page_no)}"
    return f"{file_name} §{int(page_no)}"


def extract_citations(text: str) -> list[dict]:
    """Extract inline citations in document order.

    PDF citations use ``[文件名 p.页码]``; Markdown/TXT citations use
    ``[文件名 §章节]``. ``page_no`` holds the page or section number so both
    kinds share one validation shape.
    """
    matches: list[tuple[int, dict]] = []
    for match in PDF_RE.finditer(text):
        matches.append(
            (
                match.start(),
                {
                    "file_name": match.group(1),
                    "page_no": int(match.group(2)),
                    "kind": "pdf",
                },
            )
        )
    for match in MD_RE.finditer(text):
        matches.append(
            (
                match.start(),
                {
                    "file_name": match.group(1),
                    "page_no": int(match.group(2)),
                    "kind": "markdown",
                },
            )
        )
    matches.sort(key=lambda pair: pair[0])
    return [citation for _, citation in matches]


def validate_citations(
    citations: Iterable[dict],
    retrieved: Iterable[dict],
) -> tuple[list[dict], list[dict]]:
    """Split citations into those backed by a retrieved chunk and the rest."""
    allowed = {
        (item["file_name"], int(item["page_no"]))
        for item in retrieved
        if item.get("file_name") is not None and item.get("page_no") is not None
    }
    valid: list[dict] = []
    invalid: list[dict] = []
    for citation in citations:
        key = (citation["file_name"], int(citation["page_no"]))
        if key in allowed:
            valid.append(citation)
        else:
            invalid.append(citation)
    return valid, invalid
