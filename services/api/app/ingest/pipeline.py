from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..index.indexer import index_document
from ..index.vector_store import VectorStore
from .ocr import ocr_page
from .pdf import extract_pdf_pages
from .scanner import ScannedFile, scan_folder

logger = logging.getLogger(__name__)

Checkpoint = Callable[[], bool]

OCR_DPI = 300
LOW_CONFIDENCE_THRESHOLD = 0.75


class OcrFailed(Exception):
    """The document was already marked as failed after an OCR error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestPipeline:
    """Ingest supported files into the documents/pages tables.

    A single connection is shared with :class:`app.jobs.runner.JobRunner`, so
    database writes can optionally be serialized with the runner's lock.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        lock: threading.RLock | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self.conn = conn
        self._lock = lock
        self.vector_store = vector_store

    def _guard(self):
        return self._lock if self._lock is not None else nullcontext()

    def run_scan(
        self,
        job_id: str,
        root_path: str,
        checkpoint: Checkpoint | None = None,
    ) -> bool:
        """Scan ``root_path`` and ingest every supported file.

        Returns ``True`` when the whole folder was processed and ``False`` when
        a checkpoint asked to stop (cancel or shutdown).
        """
        root = Path(root_path).resolve()
        files = list(scan_folder(root))
        with self._guard():
            self.conn.execute(
                "UPDATE jobs SET total = ?, processed = 0, progress = 0, "
                "current_file = NULL, updated_at = ? WHERE id = ?",
                (len(files), utc_now(), job_id),
            )
            self.conn.commit()

        total = len(files)
        for index, item in enumerate(files, start=1):
            if checkpoint is not None and not checkpoint():
                return False
            with self._guard():
                self.conn.execute(
                    "UPDATE jobs SET current_file = ?, updated_at = ? WHERE id = ?",
                    (str(item.path), utc_now(), job_id),
                )
                self.conn.commit()
            try:
                completed = self._ingest_file(item, checkpoint)
            except Exception:  # noqa: BLE001 - one bad file must not stop the batch
                logger.exception("failed to ingest %s", item.path)
                completed = True
            if not completed:
                return False
            with self._guard():
                self.conn.execute(
                    "UPDATE jobs SET processed = ?, progress = ?, current_file = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        index,
                        index / max(total, 1),
                        str(item.path),
                        utc_now(),
                        job_id,
                    ),
                )
                self.conn.commit()
        return True

    def _ingest_file(
        self,
        item: ScannedFile,
        checkpoint: Checkpoint | None = None,
    ) -> bool:
        """Ingest a single file. Returns ``False`` when cancelled/shutdown."""
        with self._guard():
            existing = self.conn.execute(
                "SELECT id, content_hash, status FROM documents WHERE path = ?",
                (str(item.path),),
            ).fetchone()
        if (
            existing
            and existing["content_hash"] == item.content_hash
            and existing["status"] == "ready"
        ):
            return True

        doc_id = existing["id"] if existing else uuid.uuid4().hex
        now = utc_now()
        suffix = item.path.suffix.lower()
        mime = "application/pdf" if suffix == ".pdf" else "text/plain"

        with self._guard():
            self.conn.execute(
                """
                INSERT INTO documents (
                  id, root_path, path, file_name, mime, size_bytes, mtime,
                  content_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                  content_hash = excluded.content_hash,
                  size_bytes = excluded.size_bytes,
                  mtime = excluded.mtime,
                  status = 'processing',
                  error = NULL,
                  updated_at = excluded.updated_at
                """,
                (
                    doc_id,
                    str(item.root),
                    str(item.path),
                    item.path.name,
                    mime,
                    item.size_bytes,
                    item.mtime,
                    item.content_hash,
                    now,
                    now,
                ),
            )
            self.conn.execute("DELETE FROM pages WHERE document_id = ?", (doc_id,))
            self.conn.commit()

        try:
            page_count, ocr_confidence = self._write_pages(item, doc_id, now)
            if self.vector_store is not None:
                completed = index_document(
                    self.conn,
                    self.vector_store,
                    doc_id,
                    checkpoint=checkpoint,
                    lock=self._lock,
                )
                if not completed:
                    # Cancelled/shutdown mid-index: keep pages, requeue the
                    # document so a later scan finishes indexing it.
                    with self._guard():
                        self.conn.execute(
                            "UPDATE documents SET status = 'pending', "
                            "updated_at = ? WHERE id = ?",
                            (utc_now(), doc_id),
                        )
                        self.conn.commit()
                    return False
            with self._guard():
                self.conn.execute(
                    """
                    UPDATE documents
                    SET page_count = ?, ocr_confidence = ?, status = 'ready',
                        error = NULL, indexed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (page_count, ocr_confidence, now, now, doc_id),
                )
                self.conn.commit()
            return True
        except OcrFailed:
            return True
        except Exception as exc:  # noqa: BLE001 - record and continue the batch
            logger.exception("failed to extract %s", item.path)
            with self._guard():
                self.conn.rollback()
                self.conn.execute(
                    "UPDATE documents SET status = 'failed', error = ?, "
                    "updated_at = ? WHERE id = ?",
                    (str(exc), utc_now(), doc_id),
                )
                self.conn.commit()
            return True

    def _write_pages(
        self, item: ScannedFile, doc_id: str, now: str
    ) -> tuple[int, float | None]:
        if item.path.suffix.lower() == ".pdf":
            pages = extract_pdf_pages(item.path)
            rows: list[tuple] = []
            ocr_scores: list[float] = []
            for page in pages:
                text = page.text
                ocr_confidence: float | None = None
                low_confidence = 0
                if page.is_scanned:
                    try:
                        text, ocr_confidence = ocr_page(
                            item.path, page.page_no, dpi=OCR_DPI
                        )
                    except Exception as exc:  # noqa: BLE001 - isolate one document
                        logger.exception(
                            "failed to OCR %s page %s", item.path, page.page_no
                        )
                        self._mark_failed(doc_id, "OCR_FAILED")
                        raise OcrFailed from exc
                    ocr_scores.append(ocr_confidence)
                    low_confidence = int(
                        ocr_confidence < LOW_CONFIDENCE_THRESHOLD
                    )
                rows.append(
                    (
                        doc_id,
                        page.page_no,
                        text,
                        int(page.is_scanned),
                        ocr_confidence,
                        low_confidence,
                        now,
                    )
                )

            with self._guard():
                self.conn.executemany(
                    """
                    INSERT INTO pages (
                      document_id, page_no, text, is_scanned, ocr_confidence,
                      low_confidence, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
            average = (
                round(sum(ocr_scores) / len(ocr_scores), 4) if ocr_scores else None
            )
            return len(pages), average

        text = item.path.read_text(encoding="utf-8", errors="replace")
        with self._guard():
            self.conn.execute(
                """
                INSERT INTO pages (document_id, page_no, text, is_scanned, created_at)
                VALUES (?, 1, ?, 0, ?)
                """,
                (doc_id, text, now),
            )
        return 1, None

    def _mark_failed(self, doc_id: str, error: str) -> None:
        with self._guard():
            self.conn.execute(
                "UPDATE documents SET status = 'failed', error = ?, "
                "updated_at = ? WHERE id = ?",
                (error, utc_now(), doc_id),
            )
            self.conn.commit()
