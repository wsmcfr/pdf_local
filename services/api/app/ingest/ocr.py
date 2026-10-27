from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import fitz
import numpy as np
from rapidocr_onnxruntime import RapidOCR


@lru_cache(maxsize=1)
def get_engine() -> RapidOCR:
    return RapidOCR()


def ocr_page(pdf_path: Path, page_no: int, dpi: int = 300) -> tuple[str, float]:
    with fitz.open(pdf_path) as doc:
        page = doc[page_no - 1]
        pix = page.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")

    image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    result, _ = get_engine()(image)

    if not result:
        return "", 0.0

    lines = [item[1] for item in result]
    scores = [float(item[2]) for item in result]
    return "\n".join(lines), round(sum(scores) / len(scores), 4)
