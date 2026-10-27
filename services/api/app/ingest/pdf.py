from dataclasses import dataclass
from pathlib import Path

import fitz


@dataclass
class PageText:
    page_no: int
    text: str
    is_scanned: bool
    ocr_confidence: float | None = None


def extract_pdf_pages(path: Path) -> list[PageText]:
    pages: list[PageText] = []
    with fitz.open(path) as doc:
        for index, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            is_scanned = len(text) < 20
            pages.append(
                PageText(
                    page_no=index,
                    text=text,
                    is_scanned=is_scanned,
                )
            )
    return pages
