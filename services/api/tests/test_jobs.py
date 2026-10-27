import json
import time
import uuid
from datetime import datetime, timezone

from app.db import connect, init_db
from app.jobs.runner import JobRunner

TERMINAL_STATUSES = {"done", "failed", "cancelled"}


def wait_for_status(conn, job_id, expected, timeout=5.0):
    """Poll until the job reaches one of the expected statuses."""
    deadline = time.monotonic() + timeout
    status = None
    while time.monotonic() < deadline:
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        status = row["status"] if row else None
        if status in expected:
            return status
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} stayed {status!r}, expected {expected}")


def test_job_can_be_paused_and_resumed(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    runner = JobRunner(conn)
    runner.start()
    try:
        job = runner.enqueue(
            "scan",
            {"root_path": str(tmp_path), "steps": 40, "delay": 0.05},
        )
        assert job["status"] in {"queued", "running"}
        assert wait_for_status(conn, job["id"], {"running"}, timeout=2) == "running"

        runner.request_pause(job["id"])
        assert wait_for_status(conn, job["id"], {"paused"}) == "paused"
        time.sleep(0.2)
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        assert row["status"] == "paused"

        runner.request_resume(job["id"])
        assert wait_for_status(conn, job["id"], TERMINAL_STATUSES) == "done"
        row = conn.execute(
            "SELECT status, progress FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        assert row["status"] == "done"
        assert row["progress"] == 1
    finally:
        runner.stop()
        conn.close()


def test_job_can_be_cancelled_while_running(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    runner = JobRunner(conn)
    runner.start()
    try:
        job = runner.enqueue(
            "scan",
            {"root_path": str(tmp_path), "steps": 100, "delay": 0.02},
        )
        assert wait_for_status(conn, job["id"], {"running"}, timeout=2) == "running"

        runner.request_cancel(job["id"])
        assert wait_for_status(conn, job["id"], TERMINAL_STATUSES) == "cancelled"
    finally:
        runner.stop()
        conn.close()


def test_unfinished_jobs_are_requeued_on_restart(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    now = datetime.now(timezone.utc).isoformat()
    running_id = uuid.uuid4().hex
    queued_id = uuid.uuid4().hex
    payload = json.dumps({"steps": 2, "delay": 0.01})

    for job_id, status in ((running_id, "running"), (queued_id, "queued")):
        conn.execute(
            """
            INSERT INTO jobs
              (id, type, status, progress, total, processed, payload_json,
               created_at, updated_at)
            VALUES (?, 'scan', ?, 0, 0, 0, ?, ?, ?)
            """,
            (job_id, status, payload, now, now),
        )
    conn.commit()

    runner = JobRunner(conn)
    runner.start()
    try:
        assert wait_for_status(conn, running_id, TERMINAL_STATUSES) == "done"
        assert wait_for_status(conn, queued_id, TERMINAL_STATUSES) == "done"
    finally:
        runner.stop()
        conn.close()
