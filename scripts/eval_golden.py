#!/usr/bin/env python3
"""End-to-end golden-set evaluation with FTS retrieval and a mock provider.

The script uses a temporary ``KB_DATA_DIR`` so it never touches real user data,
forces ``embedding_mode=fts`` so no model or network is needed, inserts a
``mock`` provider row, and monkeypatches ``app.api.ask.factory.create_provider``
so the mock echoes retrieved evidence with citations.

Run from the repository root:

    $env:PYTHONPATH="services/api"
    .\\.venv\\Scripts\\python.exe scripts\\eval_golden.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = ROOT / "tests" / "golden"
QUESTIONS_PATH = GOLDEN_DIR / "questions.json"
CORPUS_DIR = GOLDEN_DIR / "corpus"

TERMINAL = {"done", "failed", "cancelled"}
HEADERS = {"X-App-Token": "golden-token"}


def parse_sse(body: str) -> list[tuple[str | None, dict]]:
    """Return ``(event, data)`` pairs from an SSE response body."""
    events: list[tuple[str | None, dict]] = []
    event: str | None = None
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            events.append((event, json.loads(line[len("data: ") :])))
    return events


def collect_answer(events: list[tuple[str | None, dict]]) -> dict:
    retrieval = next((data for name, data in events if name == "retrieval"), {})
    tokens = "".join(
        str(data.get("text") or "")
        for name, data in events
        if name == "token"
    )
    citations = next(
        (data for name, data in events if name == "citations"), {"citations": [], "invalid": []}
    )
    errors = [data for name, data in events if name == "error"]
    return {
        "chunks": retrieval.get("chunks") or [],
        "answer": tokens,
        "citations": citations.get("citations") or [],
        "invalid": citations.get("invalid") or [],
        "errors": errors,
    }


def missing_evidence(expected_files: list[str], expected_pages: list[int], items: list[dict]) -> list[str]:
    missing: list[str] = []
    for file_name in expected_files:
        found = any(
            item.get("file_name") == file_name and int(item.get("page_no") or 0) in expected_pages
            for item in items
        )
        if not found:
            missing.append(file_name)
    return missing


def check_question(question: dict, result: dict) -> list[str]:
    problems: list[str] = []
    answer = result["answer"]
    citations = result["citations"]
    chunks = result["chunks"]

    if result["errors"]:
        problems.append(f"SSE error: {result['errors']}")
        return problems

    if result["invalid"]:
        problems.append(f"invalid citations: {result['invalid']}")

    expected_files = question.get("expected_files") or []
    expected_pages = question.get("expected_pages") or []
    missing_phrases = [
        phrase for phrase in question.get("expected_answer_contains") or [] if phrase not in answer
    ]
    if missing_phrases:
        problems.append(f"answer missing {missing_phrases!r}")

    if question.get("answerable"):
        if not chunks:
            problems.append("no retrieved chunks")
        missing_chunks = missing_evidence(expected_files, expected_pages, chunks)
        if missing_chunks:
            problems.append(f"retrieval missing {missing_chunks}")
        missing_citations = missing_evidence(expected_files, expected_pages, citations)
        if missing_citations:
            problems.append(f"citations missing {missing_citations}")
    elif citations:
        problems.append(f"expected no citations, got {citations}")

    return problems


def wait_for_job(client, job_id: str, timeout: float = 60.0) -> dict:
    """Poll the jobs list until ``job_id`` reaches a terminal status."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        jobs = client.get("/api/v1/jobs", headers=HEADERS).json()
        last = next((job for job in jobs if job.get("id") == job_id), {})
        if last.get("status") in TERMINAL:
            return last
        time.sleep(0.1)
    raise RuntimeError(f"job {job_id} did not finish, last status {last.get('status')!r}")


def main() -> int:
    if not QUESTIONS_PATH.is_file():
        print(f"missing golden questions: {QUESTIONS_PATH}", file=sys.stderr)
        return 2
    if not CORPUS_DIR.is_dir():
        print(f"missing golden corpus: {CORPUS_DIR}", file=sys.stderr)
        return 2

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    sys.path.insert(0, str(ROOT / "services" / "api"))
    sys.path.insert(0, str(ROOT / "services" / "api" / "tests"))

    temp_dir = tempfile.TemporaryDirectory(prefix="local-kb-golden-")
    os.environ["KB_DATA_DIR"] = temp_dir.name
    os.environ["KB_TOKEN"] = HEADERS["X-App-Token"]
    os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.null.Keyring"
    os.environ.pop("KB_GATEWAY_TOKEN", None)

    from fastapi.testclient import TestClient

    import app.api.ask as ask_module
    from app.main import app
    from mock_provider import MockProvider

    golden = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = golden.get("questions") or []
    passed = 0
    failures: list[str] = []
    timings: list[float] = []

    with TestClient(app) as client:
        # The mock provider row is never contacted at runtime: the factory is
        # patched below. It only satisfies provider selection and config load.
        settings = app.state.settings
        settings.update(
            {
                "embedding_mode": "fts",
                "context_chunks": 8,
                "llm_provider_id": "mock",
            }
        )
        # EmbeddingService keeps a reference to this dict; reattach defensively.
        app.state.embedding_service.settings = settings

        now = datetime.now(timezone.utc).isoformat()
        conn = app.state.db
        conn.execute(
            "INSERT INTO providers "
            "(id, name, kind, base_url, model, enabled, key_ref, extra_json,"
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "mock",
                "Mock Provider",
                "openai_chat",
                "http://127.0.0.1:9/v1/mock",
                "mock",
                1,
                None,
                "{}",
                now,
                now,
            ),
        )
        conn.commit()

        scan_start = time.perf_counter()
        response = client.post(
            "/api/v1/scan",
            json={"root_path": str(CORPUS_DIR)},
            headers=HEADERS,
        )
        response.raise_for_status()
        job_id = response.json()["id"]
        job = wait_for_job(client, job_id)
        scan_seconds = time.perf_counter() - scan_start
        if job.get("status") != "done":
            print(f"scan failed: {job.get('status')} {job.get('error')}", file=sys.stderr)
            return 2

        documents = client.get("/api/v1/documents", headers=HEADERS).json()
        ready = [document for document in documents if document.get("status") == "ready"]
        print(
            f"scan: {len(ready)} documents ready in {scan_seconds:.2f}s "
            f"(job {job_id})"
        )

        with patch.object(
            ask_module.factory, "create_provider", lambda config: MockProvider()
        ):
            for question in questions:
                started = time.perf_counter()
                response = client.post(
                    "/api/v1/ask",
                    json={"question": question["question"]},
                    headers=HEADERS,
                )
                elapsed = time.perf_counter() - started
                timings.append(elapsed)
                result = collect_answer(parse_sse(response.text))
                problems = check_question(question, result)
                if problems:
                    failures.append(question["id"])
                    print(f"FAIL {question['id']} ({elapsed * 1000:.0f} ms): {'; '.join(problems)}")
                    print(f"     answer={result['answer']!r}")
                    print(f"     citations={result['citations']}")
                else:
                    passed += 1
                    print(f"PASS {question['id']} ({elapsed * 1000:.0f} ms)")

    total = len(questions)
    average_ms = (sum(timings) / len(timings) * 1000) if timings else 0.0
    print(
        f"\ngolden: {passed}/{total} passed "
        f"(avg ask latency {average_ms:.0f} ms, scan {scan_seconds:.2f}s)"
    )
    if failures:
        print(f"failed: {', '.join(failures)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
