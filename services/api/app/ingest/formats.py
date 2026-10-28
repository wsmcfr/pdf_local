"""Office and text parsing into pages and editable blocks.

The retrieval pipeline only needs plain page text; the editor needs to know
where each piece of text lives inside the original file. Both are produced
together here so a document is parsed exactly once per ingest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PAGE_CHAR_BUDGET = 2000
XLSX_MAX_CELLS = 20000

TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx"}


@dataclass
class ParsedBlock:
    page_no: int
    order_index: int
    kind: str
    text: str
    locator: dict
    bbox: dict | None = None
    editable: bool = False
    approximate: bool = False


@dataclass
class ParsedPage:
    page_no: int
    text: str


@dataclass
class ParsedDocument:
    pages: list[ParsedPage] = field(default_factory=list)
    blocks: list[ParsedBlock] = field(default_factory=list)


def parse_office_file(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix == ".pptx":
        return _parse_pptx(path)
    if suffix == ".xlsx":
        return _parse_xlsx(path)
    if suffix in TEXT_SUFFIXES:
        return _parse_text(path)
    raise ValueError(f"unsupported office parser suffix: {suffix}")


class _PageBuilder:
    """Group paragraphs into pseudo-pages for Word and plain text.

    Word has no reliable page numbers without opening it in Office, so pages
    are 2k-character sections. That keeps citations readable and stable.
    """

    def __init__(self) -> None:
        self.pages: list[ParsedPage] = []
        self.blocks: list[ParsedBlock] = []
        self._texts: list[str] = []
        self._length = 0
        self.page_no = 1

    def add(
        self,
        text: str,
        *,
        kind: str,
        locator: dict,
        editable: bool,
        bbox: dict | None = None,
    ) -> None:
        if not text.strip():
            return
        long_enough = self._length + len(text) + 2 > PAGE_CHAR_BUDGET
        if self._texts and long_enough:
            self._flush()

        self.blocks.append(
            ParsedBlock(
                page_no=self.page_no,
                order_index=len(self.blocks),
                kind=kind,
                text=text,
                locator=locator,
                bbox=bbox,
                editable=editable,
            )
        )
        self._texts.append(text)
        self._length += len(text) + 2

    def _flush(self) -> None:
        self.pages.append(ParsedPage(self.page_no, "\n\n".join(self._texts)))
        self._texts = []
        self._length = 0
        self.page_no += 1

    def finish(self) -> ParsedDocument:
        if self._texts:
            self._flush()
        return ParsedDocument(pages=self.pages, blocks=self.blocks)


def _parse_docx(path: Path) -> ParsedDocument:
    from docx import Document

    document = Document(str(path))
    builder = _PageBuilder()

    for index, paragraph in enumerate(document.paragraphs):
        builder.add(
            paragraph.text.strip(),
            kind="paragraph",
            locator={"paragraph": index},
            editable=True,
        )

    for table_index, table in enumerate(document.tables):
        for row_index, row in enumerate(table.rows):
            for cell_index, cell in enumerate(row.cells):
                builder.add(
                    cell.text.strip(),
                    kind="cell",
                    locator={
                        "table": table_index,
                        "row": row_index,
                        "col": cell_index,
                    },
                    editable=True,
                )

    return builder.finish()


def _parse_pptx(path: Path) -> ParsedDocument:
    from pptx import Presentation

    presentation = Presentation(str(path))
    slide_width = presentation.slide_width or 1
    slide_height = presentation.slide_height or 1
    pages: list[ParsedPage] = []
    blocks: list[ParsedBlock] = []

    for slide_no, slide in enumerate(presentation.slides, start=1):
        page_texts: list[str] = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                bbox = _shape_bbox(shape, slide_width, slide_height)
                for paragraph_index, paragraph in enumerate(
                    shape.text_frame.paragraphs
                ):
                    text = paragraph.text.strip()
                    if not text:
                        continue
                    blocks.append(
                        ParsedBlock(
                            page_no=slide_no,
                            order_index=len(blocks),
                            kind="text_frame",
                            text=text,
                            locator={
                                "slide": slide_no,
                                "shape": shape.shape_id,
                                "paragraph": paragraph_index,
                            },
                            bbox=bbox,
                            editable=True,
                        )
                    )
                    page_texts.append(text)

            if shape.has_table:
                for row_index, row in enumerate(shape.table.rows):
                    for cell_index, cell in enumerate(row.cells):
                        text = cell.text.strip()
                        if not text:
                            continue
                        blocks.append(
                            ParsedBlock(
                                page_no=slide_no,
                                order_index=len(blocks),
                                kind="cell",
                                text=text,
                                locator={
                                    "slide": slide_no,
                                    "table": shape.shape_id,
                                    "row": row_index,
                                    "col": cell_index,
                                },
                                editable=False,
                            )
                        )
                        page_texts.append(text)

        pages.append(ParsedPage(slide_no, "\n".join(page_texts)))

    return ParsedDocument(pages=pages, blocks=blocks)


def _shape_bbox(shape, slide_width: int, slide_height: int) -> dict | None:
    try:
        left = int(shape.left)
        top = int(shape.top)
        width = int(shape.width)
        height = int(shape.height)
    except (TypeError, ValueError):
        return None
    return {
        "x": round(left / slide_width, 4),
        "y": round(top / slide_height, 4),
        "w": round(width / slide_width, 4),
        "h": round(height / slide_height, 4),
    }


def _parse_xlsx(path: Path) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), read_only=True, data_only=True)
    pages: list[ParsedPage] = []
    blocks: list[ParsedBlock] = []
    cell_count = 0
    truncated = False

    try:
        for sheet_no, sheet in enumerate(workbook.worksheets, start=1):
            lines: list[str] = []
            for row_index, row in enumerate(
                sheet.iter_rows(values_only=True), start=1
            ):
                values: list[str] = []
                for col_index, value in enumerate(row, start=1):
                    if value is None:
                        continue
                    text = str(value).strip()
                    if not text:
                        continue
                    if cell_count >= XLSX_MAX_CELLS:
                        truncated = True
                        break
                    cell_count += 1
                    values.append(text)
                    blocks.append(
                        ParsedBlock(
                            page_no=sheet_no,
                            order_index=len(blocks),
                            kind="cell",
                            text=text,
                            locator={
                                "sheet": sheet.title,
                                "row": row_index,
                                "col": col_index,
                            },
                            editable=False,
                        )
                    )
                if values:
                    lines.append(" | ".join(values))
                if truncated:
                    break

            page_text = f"{sheet.title}\n" + "\n".join(lines)
            if truncated:
                page_text += "\n……（单元格过多，已截断）"
            pages.append(ParsedPage(sheet_no, page_text))
            if truncated:
                break
    finally:
        workbook.close()

    return ParsedDocument(pages=pages, blocks=blocks)


def _parse_text(path: Path) -> ParsedDocument:
    text = path.read_text(encoding="utf-8", errors="replace")
    pages: list[ParsedPage] = []
    blocks: list[ParsedBlock] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped:
            blocks.append(
                ParsedBlock(
                    page_no=1,
                    order_index=len(blocks),
                    kind="text",
                    text=stripped,
                    locator={"offset": offset},
                    editable=True,
                )
            )
        offset += len(line)

    if text.strip():
        pages.append(ParsedPage(1, text))
    return ParsedDocument(pages=pages, blocks=blocks)
