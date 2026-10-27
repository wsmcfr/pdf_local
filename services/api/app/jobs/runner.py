from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from ..ingest.pipeline import IngestPipeline

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"done", "failed", "cancelled"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRunner:
    """Single-worker job queue persisted in SQLite.

    Pause/resume/cancel are cooperative: the worker checks the per-job control
    flags between work units. All database access is serialized with a
    re-entrant lock because the API handlers and the worker thread share the
    same connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._lock = threading.RLock()
        self.queue: queue.Queue[str] = queue.Queue()
        self.controls: dict[str, dict[str, bool]] = {}
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()
        self._active_id: str | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self._recover()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)

    def _recover(self) -> None:
        """Requeue persisted work after a restart.

        Jobs left in ``running``/``processing`` when the process died are reset
        to ``queued``; already queued jobs are put back on the in-memory queue.
        Paused jobs stay paused until the user explicitly resumes them.
        """
        with self._lock:
            rows = self.conn.execute(
                "SELECT id FROM jobs "
                "WHERE status IN ('running', 'processing', 'queued')"
            ).fetchall()
            self.conn.execute(
                "UPDATE jobs SET status = 'queued', updated_at = ? "
                "WHERE status IN ('running', 'processing')",
                (utc_now(),),
            )
            self.conn.commit()

        for row in rows:
            job_id = row["id"]
            self.controls.setdefault(job_id, {"pause": False, "cancel": False})
            self.queue.put(job_id)

    # -- queue operations --------------------------------------------------

    def enqueue(self, job_type: str, payload: dict[str, Any]) -> dict:
        job_id = uuid.uuid4().hex
        now = utc_now()
        with self._lock:
            self.conn.execute(
                """
                INSERT INTO jobs (id, type, status, payload_json, created_at, updated_at)
                VALUES (?, ?, 'queued', ?, ?, ?)
                """,
                (job_id, job_type, json.dumps(payload), now, now),
            )
            self.conn.commit()
        self.controls[job_id] = {"pause": False, "cancel": False}
        self.queue.put(job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row else {}

    def list_jobs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def request_pause(self, job_id: str) -> None:
        with self._lock:
            row = self._job_row(job_id)
            if not row or row["status"] in TERMINAL_STATUSES:
                return
            self._control(job_id)["pause"] = True
            self._set_status(job_id, "paused")

    def request_resume(self, job_id: str) -> None:
        with self._lock:
            row = self._job_row(job_id)
            if not row or row["status"] in TERMINAL_STATUSES:
                return
            control = self._control(job_id)
            control["pause"] = False
            if self._active_id == job_id:
                # The worker is blocked in its pause wait; it resumes in place.
                self._set_status(job_id, "running")
                return
            self._set_status(job_id, "queued")
            self.queue.put(job_id)

    def request_cancel(self, job_id: str) -> None:
        with self._lock:
            row = self._job_row(job_id)
            if not row or row["status"] in TERMINAL_STATUSES:
                return
            control = self._control(job_id)
            control["cancel"] = True
            control["pause"] = False
            self._set_status(job_id, "cancelled")

    # -- worker ------------------------------------------------------------

    def _loop(self) -> None:
        while not self.stopping.is_set():
            try:
                job_id = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if self.stopping.is_set():
                return
            try:
                self._run_job(job_id)
            except Exception:  # noqa: BLE001 - keep the worker alive
                logger.exception("job worker crashed while running %s", job_id)

    def _run_job(self, job_id: str) -> None:
        row = self.get(job_id)
        if not row or row["status"] != "queued":
            return

        control = self._control(job_id)
        if control["cancel"]:
            self._set_status(job_id, "cancelled")
            return

        with self._lock:
            self._active_id = job_id
        self._set_status(job_id, "running")
        try:
            payload = json.loads(row["payload_json"] or "{}")
            steps = max(1, int(payload.get("steps", 20)))
            delay = max(0.0, float(payload.get("delay", 0.05)))

            with self._lock:
                self.conn.execute(
                    "UPDATE jobs SET total = ?, processed = 0, progress = 0, "
                    "current_file = NULL, error = NULL, updated_at = ? WHERE id = ?",
                    (steps, utc_now(), job_id),
                )
                self.conn.commit()

            if row["type"] == "scan":
                completed = IngestPipeline(self.conn, lock=self._lock).run_scan(
                    job_id,
                    payload["root_path"],
                    checkpoint=lambda: self._checkpoint(job_id, control),
                )
                if not completed or not self._checkpoint(job_id, control):
                    return
                with self._lock:
                    self.conn.execute(
                        "UPDATE jobs SET status = 'done', progress = 1, "
                        "processed = total, current_file = NULL, updated_at = ? "
                        "WHERE id = ?",
                        (utc_now(), job_id),
                    )
                    self.conn.commit()
                return

            # Non-scan jobs still use the placeholder loop until Task 4/5 land.
            for index in range(steps):
                if not self._checkpoint(job_id, control):
                    return
                time.sleep(delay)
                if not self._checkpoint(job_id, control):
                    return
                with self._lock:
                    self.conn.execute(
                        "UPDATE jobs SET processed = ?, progress = ?, updated_at = ? "
                        "WHERE id = ?",
                        (index + 1, (index + 1) / steps, utc_now(), job_id),
                    )
                    self.conn.commit()

            with self._lock:
                self.conn.execute(
                    "UPDATE jobs SET status = 'done', progress = 1, processed = ?, "
                    "updated_at = ? WHERE id = ?",
                    (steps, utc_now(), job_id),
                )
                self.conn.commit()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.conn.execute(
                    "UPDATE jobs SET status = 'failed', error = ?, updated_at = ? "
                    "WHERE id = ?",
                    (str(exc), utc_now(), job_id),
                )
                self.conn.commit()
        finally:
            with self._lock:
                if self._active_id == job_id:
                    self._active_id = None

    def _checkpoint(self, job_id: str, control: dict[str, bool]) -> bool:
        """Return False when the job should stop; otherwise block while paused."""
        if self.stopping.is_set():
            return False
        if control["cancel"]:
            self._set_status(job_id, "cancelled")
            return False
        if not control["pause"]:
            return True

        self._set_status(job_id, "paused")
        while control["pause"]:
            if self.stopping.is_set():
                return False
            if control["cancel"]:
                self._set_status(job_id, "cancelled")
                return False
            time.sleep(0.05)
        self._set_status(job_id, "running")
        return True

    # -- helpers -----------------------------------------------------------

    def _job_row(self, job_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()

    def _control(self, job_id: str) -> dict[str, bool]:
        return self.controls.setdefault(
            job_id, {"pause": False, "cancel": False}
        )

    def _set_status(self, job_id: str, status: str) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, utc_now(), job_id),
            )
            self.conn.commit()
