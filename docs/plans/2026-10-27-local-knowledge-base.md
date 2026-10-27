# 本地知识库问答桌面应用 Implementation Plan

> **For Codex/Trae:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 构建一个完全本地的 Windows 桌面应用：导入本地 PDF/Markdown/TXT，OCR 扫描件，建立关键词 + 向量混合索引，并给出带原文页码引用的问答。

**Architecture:** Tauri 2 桌面壳 + React/TypeScript UI；Python FastAPI 作为 sidecar；SQLite 存元数据与 FTS5；FAISS 存向量；RapidOCR 做扫描件 OCR；BGE-M3 做 embedding；Ollama 跑 Qwen2.5。OCR/索引使用单后台 worker，避免 16GB 内存被并发任务打爆。

**Tech Stack:** Tauri 2、React 18、TypeScript、Vite、Tailwind CSS、FastAPI、SQLite、FAISS、RapidOCR、sentence-transformers、Ollama、PyInstaller。

---

## Prerequisites

当前 `f:\xiangfa` 还不是 git 仓库，Trellis 也尚未初始化。开始实现前由用户执行：

```powershell
cd f:\xiangfa
git init
trellis init -u <your-name>
```

本计划假定：

- `.trellis/` 已存在
- git 已初始化
- 当前分支不是 `main`，建议 `git checkout -b feat/local-kb`
- Windows 已安装 WebView2（Windows 11 默认有）
- Node 22、npm 11、Rust 1.93 已安装
- Ollama 已安装（本计划会引导安装/启动）

---

## 约定

- 每个 Task 先写失败测试，再写最小实现，再跑测试，再提交。
- 每个 Task 只改自己列出的文件；共享 schema/API 改动必须单独开 Task。
- 提交信息使用 `feat:` / `fix:` / `test:` / `chore:` 前缀。
- 所有路径使用 POSIX 风格写在文档里，Windows 命令使用 PowerShell。
- 测试不依赖真实 BGE-M3 / Ollama 下载；真实模型只在集成测试和手动验收中使用。

---

## Task 0: 工具链与项目骨架

**Files:**
- Create: `requirements.txt`
- Create: `scripts/dev-api.ps1`
- Create: `services/api/app/__init__.py`
- Create: `services/api/run_server.py`
- Create: `apps/desktop/`（由 create-tauri-app 生成）
- Modify: `apps/desktop/package.json`
- Create: `.gitignore`

**Step 1: 验证 Python 3.12**

Run:

```powershell
py -3.12 --version
```

Expected:

```text
Python 3.12.x
```

如果没有 3.12，先安装：

```powershell
winget install -e --id Python.Python.3.12
```

**Step 2: 创建虚拟环境并安装后端依赖**

Run:

```powershell
cd f:\xiangfa
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Create `requirements.txt`:

```text
fastapi==0.115.*
uvicorn[standard]==0.34.*
pydantic==2.*
pydantic-settings==2.*
httpx==0.28.*
PyMuPDF==1.25.*
rapidocr_onnxruntime==1.4.*
sentence-transformers==3.*
faiss-cpu==1.9.*
jieba==0.42.*
numpy==2.*
Pillow==11.*
pytest==8.*
pytest-asyncio==0.25.*
```

Run:

```powershell
python -m pip install -r requirements.txt
```

Verify imports:

```powershell
python -c "import fitz, faiss, rapidocr_onnxruntime, sentence_transformers, jieba, fastapi; print('ok')"
```

Expected:

```text
ok
```

**Step 3: 创建 Tauri + React 项目**

Run:

```powershell
cd f:\xiangfa
npm create tauri-app@latest apps/desktop -- --template react-ts --manager npm --identifier com.local.kb
```

Install UI dependencies:

```powershell
cd apps\desktop
npm install
npm install react-router-dom lucide-react clsx
npm run tauri add dialog
```

Verify dev window:

```powershell
npm run tauri dev
```

Expected: 打开一个 Tauri 窗口，显示默认 React 页面。

**Step 4: 创建后端入口**

Create `services/api/app/__init__.py`:

```python
"""Local KB FastAPI sidecar."""
```

Create `services/api/run_server.py`:

```python
#!/usr/bin/env python3
"""Start the FastAPI sidecar and announce its port to Tauri."""
from __future__ import annotations

import json
import os
import socket

import uvicorn

from app.main import app


def find_free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def main() -> None:
    port = int(os.environ.get("KB_PORT", "0") or "0") or find_free_port()
    print(f"KB_READY {json.dumps({'port': port})}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
```

Create `scripts/dev-api.ps1`:

```powershell
$env:KB_TOKEN = "dev-token"
$env:PYTHONPATH = "services/api"
.\.venv\Scripts\python.exe services\api\run_server.py
```

Create `.gitignore`:

```text
.venv/
__pycache__/
*.pyc
.pytest_cache/
node_modules/
dist/
target/
*.log
data/
models/
```

**Step 5: Commit**

```powershell
git add .gitignore requirements.txt scripts services apps/desktop
git commit -m "chore: scaffold tauri react and python sidecar"
```

---

## Task 1: FastAPI 鉴权、健康检查与 sidecar 生命周期

**Files:**
- Create: `services/api/app/main.py`
- Create: `services/api/app/api/__init__.py`
- Create: `services/api/app/api/deps.py`
- Create: `services/api/app/api/health.py`
- Create: `services/api/tests/test_health.py`
- Create: `apps/desktop/src-tauri/src/sidecar.rs`
- Modify: `apps/desktop/src-tauri/src/lib.rs`
- Modify: `apps/desktop/src-tauri/Cargo.toml`
- Create: `apps/desktop/src/api/client.ts`
- Modify: `apps/desktop/src/App.tsx`

**Step 1: 写失败测试**

Create `services/api/tests/test_health.py`:

```python
import os

from fastapi.testclient import TestClient

from app.main import app


def test_health_requires_token(monkeypatch):
    monkeypatch.setenv("KB_TOKEN", "secret")
    client = TestClient(app)

    assert client.get("/api/v1/health").status_code == 401

    response = client.get(
        "/api/v1/health",
        headers={"X-App-Token": "secret"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
```

Run:

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_health.py -v
```

Expected: FAIL（`app.main` 或 `/api/v1/health` 不存在）。

**Step 2: 实现最小后端**

Create `services/api/app/api/__init__.py`:

```python
"""API routers."""
```

Create `services/api/app/api/deps.py`:

```python
import os

from fastapi import Header, HTTPException


def require_token(x_app_token: str | None = Header(default=None, alias="X-App-Token")) -> None:
    expected = os.environ.get("KB_TOKEN")
    if not expected or x_app_token != expected:
        raise HTTPException(status_code=401, detail="UNAUTHORIZED")
```

Create `services/api/app/api/health.py`:

```python
import httpx
from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    ollama = False
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            ollama = response.status_code == 200
    except Exception:
        ollama = False

    return {
        "ok": True,
        "ollama": ollama,
        "version": "0.1.0",
    }
```

Create `services/api/app/main.py`:

```python
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import require_token
from .api.health import router as health_router

app = FastAPI(title="Local KB", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
        "http://tauri.localhost",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    health_router,
    prefix="/api/v1",
    dependencies=[Depends(require_token)],
)
```

**Step 3: 跑测试**

Run:

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_health.py -v
```

Expected: PASS。

**Step 4: 实现 Tauri sidecar 启动**

Add to `apps/desktop/src-tauri/Cargo.toml`:

```toml
[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
uuid = { version = "1", features = ["v4"] }
tauri-plugin-dialog = "2"
```

Create `apps/desktop/src-tauri/src/sidecar.rs`:

```rust
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Manager};

#[derive(Clone, Serialize)]
pub struct ServerInfo {
    pub base_url: String,
    pub token: String,
}

#[derive(Default)]
pub struct SidecarState {
    pub child: Mutex<Option<Child>>,
    pub info: Mutex<Option<ServerInfo>>,
}

impl Drop for SidecarState {
    fn drop(&mut self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            let _ = child.kill();
        }
    }
}

fn api_dir() -> PathBuf {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.join("../../../services/api")
}

pub fn spawn(app: &AppHandle) -> Result<(ServerInfo, Child), String> {
    let token = uuid::Uuid::new_v4().to_string();
    let mut cmd = if cfg!(debug_assertions) {
        let mut c = Command::new("py");
        c.args(["-3.12", "run_server.py"])
            .current_dir(api_dir())
            .env("PYTHONPATH", api_dir());
        c
    } else {
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|e| e.to_string())?;
        let exe = resource_dir
            .join("binaries")
            .join("kb-server")
            .join("kb-server.exe");
        Command::new(exe)
    };

    cmd.env("KB_TOKEN", &token)
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = cmd.spawn().map_err(|e| e.to_string())?;
    let stdout = child.stdout.take().ok_or("sidecar stdout missing")?;
    let reader = BufReader::new(stdout);

    let mut port = None;
    for line in reader.lines() {
        let line = line.map_err(|e| e.to_string())?;
        if let Some(payload) = line.strip_prefix("KB_READY ") {
            let value: serde_json::Value =
                serde_json::from_str(payload).map_err(|e| e.to_string())?;
            port = value.get("port").and_then(|v| v.as_u64());
            break;
        }
    }

    let port = port.ok_or("sidecar did not report a port")?;
    let info = ServerInfo {
        base_url: format!("http://127.0.0.1:{port}"),
        token,
    };

    // Keep the stdout reader alive in a background thread so the pipe never blocks.
    std::thread::spawn(move || {
        for _ in reader.lines() {}
    });

    Ok((info, child))
}
```

Modify `apps/desktop/src-tauri/src/lib.rs`:

```rust
mod sidecar;

use sidecar::{ServerInfo, SidecarState};
use tauri::Manager;

#[tauri::command]
fn get_server_info(state: tauri::State<SidecarState>) -> Option<ServerInfo> {
    state.info.lock().unwrap().clone()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(SidecarState::default())
        .setup(|app| {
            let (info, child) = sidecar::spawn(app.handle())?;
            let state = app.state::<SidecarState>();
            *state.info.lock().unwrap() = Some(info);
            *state.child.lock().unwrap() = Some(child);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![get_server_info])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

**Step 5: React 读取 server info**

Create `apps/desktop/src/api/client.ts`:

```ts
import { invoke } from "@tauri-apps/api/core";

export type ServerInfo = {
  base_url: string;
  token: string;
};

export async function getServerInfo(): Promise<ServerInfo> {
  const info = await invoke<ServerInfo | null>("get_server_info");
  if (!info) throw new Error("sidecar is not ready");
  return info;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const info = await getServerInfo();
  return fetch(`${info.base_url}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-App-Token": info.token,
      ...(init?.headers ?? {}),
    },
  });
}
```

Modify `apps/desktop/src/App.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiFetch, getServerInfo } from "./api/client";

export default function App() {
  const [status, setStatus] = useState("starting...");

  useEffect(() => {
    (async () => {
      try {
        await getServerInfo();
        const response = await apiFetch("/health");
        const data = await response.json();
        setStatus(`sidecar ok · ollama=${data.ollama}`);
      } catch (error) {
        setStatus(`error: ${String(error)}`);
      }
    })();
  }, []);

  return (
    <main style={{ padding: 24, fontFamily: "Segoe UI, sans-serif" }}>
      <h1>Local KB</h1>
      <p>{status}</p>
    </main>
  );
}
```

**Step 6: 手动验证**

Run:

```powershell
cd apps\desktop
npm run tauri dev
```

Expected: 窗口显示 `sidecar ok · ollama=false`（Ollama 未安装时为 false）。

**Step 7: Commit**

```powershell
git add services/api apps/desktop/src-tauri apps/desktop/src
git commit -m "feat: add authenticated health check and tauri sidecar lifecycle"
```

---

## Task 2: SQLite schema、设置与后台任务队列

**Files:**
- Create: `services/api/app/db.py`
- Create: `services/api/app/schema.sql`
- Create: `services/api/app/api/settings.py`
- Create: `services/api/app/api/jobs.py`
- Create: `services/api/app/jobs/__init__.py`
- Create: `services/api/app/jobs/runner.py`
- Create: `services/api/tests/test_jobs.py`
- Modify: `services/api/app/main.py`

**Step 1: 写失败测试**

Create `services/api/tests/test_jobs.py`:

```python
import time

from app.db import connect, init_db
from app.jobs.runner import JobRunner


def test_job_can_be_paused_and_resumed(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    runner = JobRunner(conn)
    runner.start()

    job = runner.enqueue("scan", {"root_path": str(tmp_path)})
    assert job["status"] in {"queued", "running"}

    runner.request_pause(job["id"])
    time.sleep(0.2)
    runner.request_resume(job["id"])

    for _ in range(50):
        time.sleep(0.1)
        row = conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job["id"],)
        ).fetchone()
        if row["status"] in {"done", "failed", "cancelled"}:
            break

    assert row["status"] == "done"
    runner.stop()
```

**Step 2: 跑测试确认失败**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_jobs.py -v
```

Expected: FAIL（模块不存在）。

**Step 3: 写 schema**

Create `services/api/app/schema.sql`:

```sql
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE,
  file_name TEXT NOT NULL,
  mime TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  mtime REAL NOT NULL,
  content_hash TEXT NOT NULL,
  page_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  ocr_confidence REAL,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  indexed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents(content_hash);

CREATE TABLE IF NOT EXISTS pages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  is_scanned INTEGER NOT NULL DEFAULT 0,
  ocr_confidence REAL,
  low_confidence INTEGER NOT NULL DEFAULT 0,
  width INTEGER,
  height INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, page_no)
);

CREATE TABLE IF NOT EXISTS chunks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  token_count INTEGER NOT NULL DEFAULT 0,
  start_offset INTEGER NOT NULL DEFAULT 0,
  end_offset INTEGER NOT NULL DEFAULT 0,
  embedding_id INTEGER,
  created_at TEXT NOT NULL,
  UNIQUE(document_id, page_no, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED,
  text_tokens,
  tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  progress REAL NOT NULL DEFAULT 0,
  total INTEGER NOT NULL DEFAULT 0,
  processed INTEGER NOT NULL DEFAULT 0,
  current_file TEXT,
  error TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  citations_json TEXT NOT NULL DEFAULT '[]',
  model TEXT,
  latency_ms INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS index_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

PRAGMA user_version = 1;
```

Create `services/api/app/db.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
```

**Step 4: 实现 JobRunner**

Create `services/api/app/jobs/__init__.py`:

```python
"""Background job runner."""
```

Create `services/api/app/jobs/runner.py`:

```python
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from datetime import datetime, timezone

import sqlite3


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobRunner:
    """Single-worker job queue persisted in SQLite.

    Pause/cancel are cooperative: the worker checks flags between files.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.queue: queue.Queue[str] = queue.Queue()
        self.controls: dict[str, dict] = {}
        self.thread: threading.Thread | None = None
        self.stopping = threading.Event()

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)

    def enqueue(self, job_type: str, payload: dict) -> dict:
        job_id = uuid.uuid4().hex
        now = utc_now()
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
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return dict(row) if row else {}

    def request_pause(self, job_id: str) -> None:
        self.controls.setdefault(job_id, {})["pause"] = True
        self._set_status(job_id, "paused")

    def request_resume(self, job_id: str) -> None:
        self.controls.setdefault(job_id, {})["pause"] = False
        self._set_status(job_id, "queued")
        self.queue.put(job_id)

    def request_cancel(self, job_id: str) -> None:
        self.controls.setdefault(job_id, {})["cancel"] = True
        self._set_status(job_id, "cancelled")

    def _set_status(self, job_id: str, status: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
            (status, utc_now(), job_id),
        )
        self.conn.commit()

    def _loop(self) -> None:
        while not self.stopping.is_set():
            try:
                job_id = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self._run_job(job_id)

    def _run_job(self, job_id: str) -> None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row or row["status"] in {"cancelled", "done"}:
            return

        control = self.controls.setdefault(job_id, {"pause": False, "cancel": False})
        if control["cancel"]:
            self._set_status(job_id, "cancelled")
            return

        self._set_status(job_id, "running")
        try:
            # Task 3/4/5 will replace this with the real pipeline.
            for _ in range(5):
                if control["cancel"]:
                    self._set_status(job_id, "cancelled")
                    return
                while control["pause"]:
                    time.sleep(0.1)
                    if control["cancel"]:
                        self._set_status(job_id, "cancelled")
                        return
                time.sleep(0.1)
            self.conn.execute(
                "UPDATE jobs SET status='done', progress=1, updated_at=? WHERE id=?",
                (utc_now(), job_id),
            )
            self.conn.commit()
        except Exception as exc:  # noqa: BLE001
            self.conn.execute(
                "UPDATE jobs SET status='failed', error=?, updated_at=? WHERE id=?",
                (str(exc), utc_now(), job_id),
            )
            self.conn.commit()
```

**Step 5: 暴露 API**

Create `services/api/app/api/jobs.py`:

```python
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


def runner(request: Request):
    return request.app.state.runner


@router.post("/scan")
def scan(payload: dict, request: Request) -> dict:
    root_path = payload.get("root_path")
    if not root_path:
        raise HTTPException(status_code=400, detail="root_path required")
    return runner(request).enqueue("scan", {"root_path": root_path})


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict]:
    rows = runner(request).conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/jobs/{job_id}/pause")
def pause(job_id: str, request: Request) -> dict:
    runner(request).request_pause(job_id)
    return runner(request).get(job_id)


@router.post("/jobs/{job_id}/resume")
def resume(job_id: str, request: Request) -> dict:
    runner(request).request_resume(job_id)
    return runner(request).get(job_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, request: Request) -> dict:
    runner(request).request_cancel(job_id)
    return runner(request).get(job_id)
```

Create `services/api/app/api/settings.py`:

```python
import json

from fastapi import APIRouter, Request

router = APIRouter()

DEFAULTS = {
    "data_dir": "",
    "ocr_dpi": 300,
    "embedding_model": "BAAI/bge-m3",
    "llm_model": "qwen2.5:7b",
    "context_chunks": 8,
    "offline_mode": True,
}


@router.get("/settings")
def get_settings(request: Request) -> dict:
    rows = request.app.state.db.execute("SELECT key, value FROM settings").fetchall()
    stored = {row["key"]: json.loads(row["value"]) for row in rows}
    return {**DEFAULTS, **stored}


@router.patch("/settings")
def patch_settings(payload: dict, request: Request) -> dict:
    conn = request.app.state.db
    for key, value in payload.items():
        if key not in DEFAULTS:
            continue
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )
    conn.commit()
    return get_settings(request)
```

Modify `services/api/app/main.py` to add lifespan setup:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.deps import require_token
from .api.health import router as health_router
from .api.jobs import router as jobs_router
from .api.settings import router as settings_router
from .db import connect, init_db
from .jobs.runner import JobRunner


@asynccontextmanager
async def lifespan(app: FastAPI):
    data_dir = Path(os.environ.get("KB_DATA_DIR", Path.home() / ".local-kb"))
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir / "app.db")
    init_db(conn)
    app.state.db = conn
    app.state.runner = JobRunner(conn)
    app.state.runner.start()
    yield
    app.state.runner.stop()
    conn.close()


app = FastAPI(title="Local KB", version="0.1.0", lifespan=lifespan)
# ... existing CORS ...
app.include_router(health_router, prefix="/api/v1", dependencies=[Depends(require_token)])
app.include_router(jobs_router, prefix="/api/v1", dependencies=[Depends(require_token)])
app.include_router(settings_router, prefix="/api/v1", dependencies=[Depends(require_token)])
```

Add `import os` at the top.

**Step 6: 跑测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_jobs.py services/api/tests/test_health.py -v
```

Expected: PASS。

**Step 7: Commit**

```powershell
git add services/api
git commit -m "feat: add sqlite schema, settings and persisted job queue"
```

---

## Task 3: 文件夹扫描与文字版 PDF 提取

**Files:**
- Create: `services/api/app/utils/__init__.py`
- Create: `services/api/app/utils/hashing.py`
- Create: `services/api/app/utils/paths.py`
- Create: `services/api/app/ingest/__init__.py`
- Create: `services/api/app/ingest/scanner.py`
- Create: `services/api/app/ingest/pdf.py`
- Create: `services/api/app/ingest/pipeline.py`
- Create: `services/api/app/api/documents.py`
- Create: `services/api/tests/test_pdf.py`
- Modify: `services/api/app/main.py`
- Modify: `services/api/app/jobs/runner.py`

**Step 1: 写失败测试**

Create `services/api/tests/test_pdf.py`:

```python
import fitz

from app.ingest.pdf import extract_pdf_pages


def test_extract_text_pdf(tmp_path):
    path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello local knowledge base")
    doc.save(path)
    doc.close()

    pages = extract_pdf_pages(path)
    assert len(pages) == 1
    assert pages[0].page_no == 1
    assert "Hello local knowledge base" in pages[0].text
    assert pages[0].is_scanned is False
```

Run:

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_pdf.py -v
```

Expected: FAIL。

**Step 2: 实现工具与解析**

Create `services/api/app/utils/__init__.py`:

```python
"""Shared utilities."""
```

Create `services/api/app/utils/hashing.py`:

```python
import hashlib
from pathlib import Path


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()
```

Create `services/api/app/utils/paths.py`:

```python
from pathlib import Path

SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_SUFFIXES


def ensure_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
```

Create `services/api/app/ingest/__init__.py`:

```python
"""Ingestion pipeline."""
```

Create `services/api/app/ingest/scanner.py`:

```python
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
```

Create `services/api/app/ingest/pdf.py`:

```python
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
```

**Step 3: 跑测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_pdf.py -v
```

Expected: PASS。

**Step 4: 接入 pipeline**

Create `services/api/app/ingest/pipeline.py`:

```python
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .pdf import extract_pdf_pages
from .scanner import ScannedFile, scan_folder


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IngestPipeline:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def run_scan(self, job_id: str, root_path: str) -> None:
        root = Path(root_path).resolve()
        files = list(scan_folder(root))
        self.conn.execute(
            "UPDATE jobs SET total=?, updated_at=? WHERE id=?",
            (len(files), utc_now(), job_id),
        )
        for index, item in enumerate(files, start=1):
            self._ingest_file(item)
            self.conn.execute(
                "UPDATE jobs SET processed=?, progress=?, current_file=?, updated_at=? WHERE id=?",
                (index, index / max(len(files), 1), str(item.path), utc_now(), job_id),
            )
            self.conn.commit()

    def _ingest_file(self, item: ScannedFile) -> None:
        existing = self.conn.execute(
            "SELECT id, content_hash FROM documents WHERE path = ?",
            (str(item.path),),
        ).fetchone()
        if existing and existing["content_hash"] == item.content_hash:
            return

        doc_id = existing["id"] if existing else uuid.uuid4().hex
        now = utc_now()
        mime = "application/pdf" if item.path.suffix.lower() == ".pdf" else "text/plain"

        self.conn.execute(
            """
            INSERT INTO documents (
              id, root_path, path, file_name, mime, size_bytes, mtime,
              content_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'processing', ?, ?)
            ON CONFLICT(path) DO UPDATE SET
              content_hash=excluded.content_hash,
              size_bytes=excluded.size_bytes,
              mtime=excluded.mtime,
              status='processing',
              updated_at=excluded.updated_at
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

        if item.path.suffix.lower() == ".pdf":
            pages = extract_pdf_pages(item.path)
            for page in pages:
                self.conn.execute(
                    """
                    INSERT INTO pages (document_id, page_no, text, is_scanned, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (doc_id, page.page_no, page.text, int(page.is_scanned), now),
                )
            page_count = len(pages)
        else:
            text = item.path.read_text(encoding="utf-8", errors="replace")
            self.conn.execute(
                """
                INSERT INTO pages (document_id, page_no, text, is_scanned, created_at)
                VALUES (?, 1, ?, 0, ?)
                """,
                (doc_id, text, now),
            )
            page_count = 1

        self.conn.execute(
            """
            UPDATE documents
            SET page_count = ?, status = 'ready', indexed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (page_count, now, now, doc_id),
        )
        self.conn.commit()
```

Then `JobRunner` calls `IngestPipeline.run_scan(job_id, root_path)` for `scan` jobs. Modify `_run_job`:

```python
        if row["type"] == "scan":
            payload = json.loads(row["payload_json"])
            IngestPipeline(self.conn).run_scan(job_id, payload["root_path"])
            self._set_status(job_id, "done")
            self.conn.execute("UPDATE jobs SET progress=1, updated_at=? WHERE id=?", (utc_now(), job_id))
            self.conn.commit()
            return
```

Import `IngestPipeline` in `runner.py`.

**Step 5: 暴露 documents API**

Create `services/api/app/api/documents.py`:

```python
from fastapi import APIRouter, HTTPException, Request

router = APIRouter()


@router.get("/documents")
def list_documents(request: Request, status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM documents"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    sql += " ORDER BY updated_at DESC LIMIT 500"
    rows = request.app.state.db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/documents/{document_id}")
def get_document(document_id: str, request: Request) -> dict:
    row = request.app.state.db.execute(
        "SELECT * FROM documents WHERE id = ?", (document_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    return dict(row)


@router.get("/documents/{document_id}/pages/{page_no}")
def get_page(document_id: str, page_no: int, request: Request) -> dict:
    row = request.app.state.db.execute(
        "SELECT * FROM pages WHERE document_id = ? AND page_no = ?",
        (document_id, page_no),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="FILE_NOT_FOUND")
    return dict(row)
```

Register in `main.py`:

```python
from .api.documents import router as documents_router
app.include_router(documents_router, prefix="/api/v1", dependencies=[Depends(require_token)])
```

**Step 6: 跑测试 + 手动扫描**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests -v
```

Manual: start API, POST `/api/v1/scan` with a test folder, then GET `/api/v1/documents`.

**Step 7: Commit**

```powershell
git add services/api
git commit -m "feat: scan folders and extract text from pdf/markdown/txt"
```

---

## Task 4: 扫描 PDF OCR 与置信度

**Files:**
- Create: `services/api/app/ingest/ocr.py`
- Modify: `services/api/app/ingest/pipeline.py`
- Create: `services/api/tests/test_ocr.py`

**Step 1: 写失败测试**

Create `services/api/tests/test_ocr.py`:

```python
import fitz
from pathlib import Path

from app.ingest.ocr import ocr_page


def test_ocr_returns_text_and_confidence(monkeypatch, tmp_path):
    pdf_path = tmp_path / "dummy.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    class FakeEngine:
        def __call__(self, _image):
            return (
                [
                    ([[0, 0], [10, 0], [10, 10], [0, 10]], "测试文本", 0.9),
                    ([[0, 12], [10, 12], [10, 22], [0, 22]], "Hello", 0.8),
                ],
                None,
            )

    monkeypatch.setattr("app.ingest.ocr.get_engine", lambda: FakeEngine())
    text, confidence = ocr_page(pdf_path, 1, 150)
    assert "测试文本" in text
    assert "Hello" in text
    assert confidence == 0.85
```

Run:

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_ocr.py -v
```

Expected: FAIL。

**Step 2: 实现 OCR**

Create `services/api/app/ingest/ocr.py`:

```python
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
    return "\n".join(lines), sum(scores) / len(scores)
```

**Step 3: 跑测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_ocr.py -v
```

Expected: PASS。

**Step 4: 接入 pipeline**

Modify `services/api/app/ingest/pipeline.py`:

- For each page with `is_scanned`, call `ocr_page(item.path, page.page_no, dpi=300)`.
- Update `pages.text`, `is_scanned=1`, `ocr_confidence`, `low_confidence = int(confidence < 0.75)`.
- If OCR fails, set `documents.status='failed'`, `error='OCR_FAILED'`, and continue.

Code:

```python
from .ocr import ocr_page

# inside _ingest_file, after inserting pages:
if page.is_scanned:
    try:
        text, confidence = ocr_page(item.path, page.page_no, dpi=300)
    except Exception as exc:
        self.conn.execute(
            "UPDATE documents SET status='failed', error=?, updated_at=? WHERE id=?",
            ("OCR_FAILED", now, doc_id),
        )
        self.conn.commit()
        return
    self.conn.execute(
        """
        UPDATE pages
        SET text=?, is_scanned=1, ocr_confidence=?, low_confidence=?
        WHERE document_id=? AND page_no=?
        """,
        (text, confidence, int(confidence < 0.75), doc_id, page.page_no),
    )
```

**Step 5: 集成测试（可选，标记 integration）**

Create a test that builds a scanned PDF by rendering text into an image:

```python
import fitz
from PIL import Image, ImageDraw

def make_scanned_pdf(path):
    image = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 120), "Local Knowledge Base", fill="black")
    image_path = path.with_suffix(".png")
    image.save(image_path)

    doc = fitz.open()
    page = doc.new_page(width=1200, height=400)
    page.insert_image(page.rect, filename=str(image_path))
    doc.save(path)
    doc.close()
```

Run only when RapidOCR models are available. Mark with `@pytest.mark.integration`.

**Step 6: Commit**

```powershell
git add services/api
git commit -m "feat: ocr scanned pdf pages with confidence tracking"
```

---

## Task 5: 切分、FTS5、BGE-M3 与 FAISS

**Files:**
- Create: `services/api/app/utils/text.py`
- Create: `services/api/app/ingest/chunker.py`
- Create: `services/api/app/index/__init__.py`
- Create: `services/api/app/index/fts.py`
- Create: `services/api/app/index/embeddings.py`
- Create: `services/api/app/index/vector_store.py`
- Create: `services/api/tests/test_chunker.py`
- Create: `services/api/tests/test_fts.py`

**Step 1: 写失败测试**

Create `services/api/tests/test_chunker.py`:

```python
from app.ingest.chunker import chunk_text


def test_chunk_respects_max_size_and_overlap():
    text = "第一段。" * 100 + "\n\n" + "第二段。" * 100
    chunks = chunk_text(text, max_chars=120, overlap=20)
    assert len(chunks) >= 2
    assert all(len(c.text) <= 120 for c in chunks)
    assert all(c.text.strip() for c in chunks)
```

Create `services/api/tests/test_fts.py`:

```python
from app.db import connect, init_db
from app.index.fts import insert_chunk, search


def test_chinese_two_char_query(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    insert_chunk(conn, "c1", "doc1", 1, "本合同的有效期为三年。")
    insert_chunk(conn, "c2", "doc1", 2, "项目验收标准如下。")

    results = search(conn, "合同", limit=5)
    assert results
    assert results[0]["chunk_id"] == "c1"
```

Run:

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_chunker.py services/api/tests/test_fts.py -v
```

Expected: FAIL。

**Step 2: 实现切分与分词**

Create `services/api/app/utils/text.py`:

```python
import re

import jieba


def tokenize_zh(text: str) -> str:
    return " ".join(token for token in jieba.cut_for_search(text) if token.strip())


def normalize_text(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()
```

Create `services/api/app/ingest/chunker.py`:

```python
from dataclasses import dataclass

from ..utils.text import normalize_text


@dataclass
class Chunk:
    chunk_index: int
    text: str
    start_offset: int
    end_offset: int


def chunk_text(text: str, max_chars: int = 800, overlap: int = 80) -> list[Chunk]:
    text = normalize_text(text)
    if not text:
        return []

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: list[Chunk] = []
    buffer = ""
    buffer_start = 0
    cursor = 0
    chunk_index = 0

    for paragraph in paragraphs:
        start = text.find(paragraph, cursor)
        if start == -1:
            start = cursor
        cursor = start + len(paragraph)

        if buffer and len(buffer) + len(paragraph) + 2 > max_chars:
            end = buffer_start + len(buffer)
            chunks.append(
                Chunk(
                    chunk_index=chunk_index,
                    text=buffer.strip(),
                    start_offset=buffer_start,
                    end_offset=end,
                )
            )
            chunk_index += 1
            tail = buffer[-overlap:] if overlap else ""
            buffer = (tail + "\n\n" + paragraph).strip()
            buffer_start = max(0, end - len(tail))
        else:
            if not buffer:
                buffer_start = start
            buffer = (buffer + "\n\n" + paragraph).strip() if buffer else paragraph

    if buffer.strip():
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                text=buffer.strip(),
                start_offset=buffer_start,
                end_offset=buffer_start + len(buffer),
            )
        )

    return chunks
```

**Step 3: 实现 FTS**

Create `services/api/app/index/__init__.py`:

```python
"""Indexing primitives."""
```

Create `services/api/app/index/fts.py`:

```python
import sqlite3

from ..utils.text import tokenize_zh


def insert_chunk(
    conn: sqlite3.Connection,
    chunk_id: str,
    document_id: str,
    page_no: int,
    text: str,
) -> None:
    tokens = tokenize_zh(text)
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, text_tokens) VALUES (?, ?)",
        (chunk_id, tokens),
    )
    conn.commit()


def delete_chunk(conn: sqlite3.Connection, chunk_id: str) -> None:
    conn.execute("DELETE FROM chunks_fts WHERE chunk_id = ?", (chunk_id,))
    conn.commit()


def search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    tokens = tokenize_zh(query)
    match = " OR ".join(f'"{token}"' for token in tokens.split() if token)
    if not match:
        return []

    rows = conn.execute(
        """
        SELECT chunk_id, bm25(chunks_fts) AS score
        FROM chunks_fts
        WHERE chunks_fts MATCH ?
        ORDER BY score
        LIMIT ?
        """,
        (match, limit),
    ).fetchall()
    return [{"chunk_id": r["chunk_id"], "score": abs(r["score"])} for r in rows]
```

**Step 4: 跑测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_chunker.py services/api/tests/test_fts.py -v
```

Expected: PASS（FTS 测试应能命中“合同”这个 2 字词）。

**Step 5: 实现 embedding 与 FAISS**

Create `services/api/app/index/embeddings.py`:

```python
from __future__ import annotations

from functools import lru_cache

from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    return SentenceTransformer("BAAI/bge-m3", device="cpu")


def encode(texts: list[str], batch_size: int = 8) -> list[list[float]]:
    model = get_model()
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
```

Create `services/api/app/index/vector_store.py`:

```python
from __future__ import annotations

from pathlib import Path

import faiss
import numpy as np


class VectorStore:
    def __init__(self, path: Path, dimension: int = 1024) -> None:
        self.path = path
        self.dimension = dimension
        self.index = self._load()

    def _load(self) -> faiss.Index:
        if self.path.exists():
            return faiss.read_index(str(self.path))
        base = faiss.IndexFlatIP(self.dimension)
        return faiss.IndexIDMap2(base)

    def add(self, ids: list[int], vectors: list[list[float]], save: bool = True) -> None:
        array = np.asarray(vectors, dtype="float32")
        self.index.add_with_ids(array, np.asarray(ids, dtype="int64"))
        if save:
            self.save()

    def search(self, vector: list[float], limit: int = 20) -> list[tuple[int, float]]:
        array = np.asarray([vector], dtype="float32")
        scores, ids = self.index.search(array, limit)
        return [
            (int(i), float(s))
            for i, s in zip(ids[0], scores[0])
            if i != -1
        ]

    def remove(self, ids: list[int]) -> None:
        self.index.remove_ids(np.asarray(ids, dtype="int64"))
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self.index, str(self.path))
```

**Step 6: 接入 pipeline**

After pages are ready, for each page:

1. `chunks = chunk_text(page.text)`
2. Insert `chunks` rows.
3. Insert FTS tokens.
4. Encode all chunk texts in batches.
5. Add vectors with `embedding_id = auto-increment` (use a global counter in `index_meta`).
6. Update `chunks.embedding_id`.

For MVP, `embedding_id` can be a stable integer derived from a counter; use `IndexIDMap2` with IDs.

```python
import uuid
from datetime import datetime, timezone

from .chunker import chunk_text
from ..index.fts import insert_chunk
from ..index.embeddings import encode
from ..index.vector_store import VectorStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_meta(conn, key, default=None):
    row = conn.execute(
        "SELECT value FROM index_meta WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else default


def set_meta(conn, key, value) -> None:
    conn.execute(
        "INSERT INTO index_meta(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def index_document(conn, vector_store, doc_id: str, pages) -> None:
    next_id = int(get_meta(conn, "next_embedding_id", "1"))
    for page in pages:
        chunks = chunk_text(page["text"])
        if not chunks:
            continue

        texts = [chunk.text for chunk in chunks]
        vectors = encode(texts)
        for chunk, vector in zip(chunks, vectors):
            chunk_id = uuid.uuid4().hex
            conn.execute(
                """
                INSERT INTO chunks (
                  id, document_id, page_no, chunk_index, text,
                  token_count, start_offset, end_offset, embedding_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    doc_id,
                    page["page_no"],
                    chunk.chunk_index,
                    chunk.text,
                    max(1, len(chunk.text) // 2),
                    chunk.start_offset,
                    chunk.end_offset,
                    next_id,
                    utc_now(),
                ),
            )
            insert_chunk(conn, chunk_id, doc_id, page["page_no"], chunk.text)
            vector_store.add([next_id], [vector], save=False)
            next_id += 1

    vector_store.save()
    conn.commit()
    set_meta(conn, "next_embedding_id", str(next_id))
```

**Step 7: Commit**

```powershell
git add services/api
git commit -m "feat: chunk text, build fts index and faiss embeddings"
```

---

## Task 6: 混合检索、带引用问答与无答案策略

**Files:**
- Create: `services/api/app/qa/__init__.py`
- Create: `services/api/app/qa/retrieve.py`
- Create: `services/api/app/qa/retriever.py`
- Create: `services/api/app/qa/prompt.py`
- Create: `services/api/app/qa/citations.py`
- Create: `services/api/app/qa/answer.py`
- Create: `services/api/app/llm/__init__.py`
- Create: `services/api/app/llm/ollama.py`
- Create: `services/api/app/api/ask.py`
- Create: `services/api/tests/test_citations.py`
- Create: `services/api/tests/test_retrieve.py`
- Modify: `services/api/app/main.py`

**Step 1: 写失败测试**

Create `services/api/tests/test_citations.py`:

```python
from app.qa.citations import extract_citations, validate_citations

RETRIEVED = [
    {"document_id": "d1", "file_name": "报告.pdf", "page_no": 12},
    {"document_id": "d1", "file_name": "报告.pdf", "page_no": 13},
]


def test_extract_pdf_citation():
    text = "根据资料，营收增长 20% [报告.pdf p.12]。"
    citations = extract_citations(text)
    assert citations[0]["file_name"] == "报告.pdf"
    assert citations[0]["page_no"] == 12


def test_validate_rejects_unknown_page():
    text = "结论 [报告.pdf p.99]。"
    citations = extract_citations(text)
    valid, invalid = validate_citations(citations, RETRIEVED)
    assert valid == []
    assert invalid[0]["page_no"] == 99
```

Create `services/api/tests/test_retrieve.py`:

```python
from app.qa.retrieve import rrf_fuse


def test_rrf_prefers_items_in_both_lists():
    keyword = [{"chunk_id": "a", "score": 1.0}, {"chunk_id": "b", "score": 0.8}]
    vector = [{"chunk_id": "b", "score": 0.9}, {"chunk_id": "c", "score": 0.7}]
    fused = rrf_fuse(keyword, vector, k=60)
    assert fused[0]["chunk_id"] == "b"
```

Run:

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_citations.py services/api/tests/test_retrieve.py -v
```

Expected: FAIL。

**Step 2: 实现 RRF 与引用**

Create `services/api/app/qa/__init__.py`:

```python
"""Question answering."""
```

Create `services/api/app/qa/retrieve.py`:

```python
def rrf_fuse(
    keyword_results: list[dict],
    vector_results: list[dict],
    k: int = 60,
) -> list[dict]:
    scores: dict[str, float] = {}
    payload: dict[str, dict] = {}

    for rank, item in enumerate(keyword_results, start=1):
        chunk_id = item["chunk_id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        payload.setdefault(chunk_id, item)

    for rank, item in enumerate(vector_results, start=1):
        chunk_id = item["chunk_id"]
        scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
        payload.setdefault(chunk_id, item)

    ordered = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    return [
        {**payload[chunk_id], "rrf_score": score}
        for chunk_id, score in ordered
    ]
```

Create `services/api/app/qa/citations.py`:

```python
import re
from typing import Iterable

PDF_RE = re.compile(r"\[([^\[\]]+?)\s+p\.(\d+)\]")
MD_RE = re.compile(r"\[([^\[\]]+?)\s+§(\d+)\]")


def extract_citations(text: str) -> list[dict]:
    citations: list[dict] = []
    for match in PDF_RE.finditer(text):
        citations.append({"file_name": match.group(1), "page_no": int(match.group(2))})
    for match in MD_RE.finditer(text):
        citations.append({"file_name": match.group(1), "page_no": int(match.group(2))})
    return citations


def validate_citations(
    citations: Iterable[dict],
    retrieved: Iterable[dict],
) -> tuple[list[dict], list[dict]]:
    allowed = {(item["file_name"], int(item["page_no"])) for item in retrieved}
    valid: list[dict] = []
    invalid: list[dict] = []
    for citation in citations:
        key = (citation["file_name"], int(citation["page_no"]))
        if key in allowed:
            valid.append(citation)
        else:
            invalid.append(citation)
    return valid, invalid
```

**Step 3: 跑测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_citations.py services/api/tests/test_retrieve.py -v
```

Expected: PASS。

**Step 4: 实现 Ollama 客户端**

Create `services/api/app/llm/__init__.py`:

```python
"""Local LLM clients."""
```

Create `services/api/app/llm/ollama.py`:

```python
from __future__ import annotations

import json

import httpx

OLLAMA_URL = "http://127.0.0.1:11434"


async def list_models() -> list[str]:
    async with httpx.AsyncClient(timeout=2.0) as client:
        response = await client.get(f"{OLLAMA_URL}/api/tags")
        response.raise_for_status()
        data = response.json()
        return [model["name"] for model in data.get("models", [])]


async def stream_chat(model: str, messages: list[dict]):
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
                "options": {"temperature": 0.1, "num_ctx": 8192},
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                data = json.loads(line)
                if data.get("message", {}).get("content"):
                    yield data["message"]["content"]
```

**Step 5: 实现问答编排与 SSE**

Create `services/api/app/qa/prompt.py`:

```python
SYSTEM_PROMPT = """你是一个本地知识库问答助手。

规则：
1. 只能使用下面提供的资料回答，不得使用外部知识。
2. 每个结论后必须标注来源，格式为 [文件名 p.页码]。
3. 如果资料不足以回答，只输出：资料中没有找到。
4. 不要编造文件名、页码或数据。
5. 用中文回答，简洁、直接。
"""


def build_messages(question: str, contexts: list[dict]) -> list[dict]:
    context_text = "\n\n".join(
        f"[{item['file_name']} p.{item['page_no']}]\n{item['text']}"
        for item in contexts
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"资料：\n{context_text}\n\n问题：{question}",
        },
    ]
```

Create `services/api/app/qa/answer.py`:

```python
from __future__ import annotations

from .citations import extract_citations, validate_citations
from .prompt import build_messages
from ..llm.ollama import stream_chat


async def answer_question(
    question: str,
    contexts: list[dict],
    model: str,
):
    messages = build_messages(question, contexts)
    full_text = ""
    async for token in stream_chat(model, messages):
        full_text += token
        yield ("token", token)

    citations = extract_citations(full_text)
    valid, invalid = validate_citations(citations, contexts)
    yield ("citations", {"citations": valid, "invalid": invalid})
    yield ("done", {"text": full_text, "model": model})
```

Create `services/api/app/qa/retriever.py`:

```python
from __future__ import annotations

import sqlite3

from ..index.embeddings import encode
from ..index.fts import search as fts_search
from ..index.vector_store import VectorStore
from .retrieve import rrf_fuse


class HybridRetriever:
    def __init__(self, conn: sqlite3.Connection, vector_store: VectorStore) -> None:
        self.conn = conn
        self.vector_store = vector_store

    def search(self, question: str, limit: int = 8) -> list[dict]:
        keyword = fts_search(self.conn, question, limit=20)

        query_vector = encode([question])[0]
        vector_hits = self.vector_store.search(query_vector, limit=20)

        vector_results: list[dict] = []
        for embedding_id, score in vector_hits:
            row = self.conn.execute(
                "SELECT id AS chunk_id FROM chunks WHERE embedding_id = ?",
                (embedding_id,),
            ).fetchone()
            if row:
                vector_results.append({"chunk_id": row["chunk_id"], "score": score})

        fused = rrf_fuse(keyword, vector_results, k=60)[:limit]
        return [self._hydrate(item) for item in fused]

    def _hydrate(self, item: dict) -> dict:
        row = self.conn.execute(
            """
            SELECT c.id AS chunk_id, c.document_id, c.page_no, c.text,
                   d.file_name
            FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.id = ?
            """,
            (item["chunk_id"],),
        ).fetchone()
        return dict(row) if row else item
```

Create `services/api/app/api/ask.py`:

```python
import json
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..qa.retrieve import rrf_fuse
from ..qa.answer import answer_question

router = APIRouter()


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/ask")
async def ask(payload: dict, request: Request) -> StreamingResponse:
    question = payload.get("question", "").strip()
    if not question:
        return StreamingResponse(iter([sse("error", {"message": "question required"})]), media_type="text/event-stream")

    # app.state.retriever is initialized in the lifespan in main.py.
    contexts = request.app.state.retriever.search(question, limit=8)
    model = request.app.state.settings.get("llm_model", "qwen2.5:7b")

    async def stream() -> AsyncIterator[str]:
        yield sse("retrieval", {"chunks": contexts})
        async for event, data in answer_question(question, contexts, model):
            yield sse(event, data)

    return StreamingResponse(stream(), media_type="text/event-stream")
```

Register in `main.py`. In the `lifespan`, after `app.state.db` is created, wire the retriever and settings:

```python
import json

from .api.settings import DEFAULTS
from .index.vector_store import VectorStore
from .qa.retriever import HybridRetriever

# inside lifespan(), after app.state.db = conn:
rows = conn.execute("SELECT key, value FROM settings").fetchall()
app.state.settings = {
    **DEFAULTS,
    **{row["key"]: json.loads(row["value"]) for row in rows},
}
app.state.retriever = HybridRetriever(
    conn,
    VectorStore(data_dir / "vectors.faiss"),
)
```

**Step 6: 跑测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests -v
```

Expected: PASS（不依赖真实模型）。

**Step 7: Commit**

```powershell
git add services/api
git commit -m "feat: hybrid retrieval and cited question answering"
```

---

## Task 7: React UI 主流程

**Files:**
- Create: `apps/desktop/src/pages/Onboarding.tsx`
- Create: `apps/desktop/src/pages/Library.tsx`
- Create: `apps/desktop/src/pages/Jobs.tsx`
- Create: `apps/desktop/src/pages/Chat.tsx`
- Create: `apps/desktop/src/pages/Settings.tsx`
- Create: `apps/desktop/src/components/Sidebar.tsx`
- Create: `apps/desktop/src/components/StatusBar.tsx`
- Create: `apps/desktop/src/components/CitationPanel.tsx`
- Create: `apps/desktop/src/components/PageViewer.tsx`
- Create: `apps/desktop/src/api/stream.ts`
- Modify: `apps/desktop/src/App.tsx`
- Modify: `apps/desktop/src/main.tsx`
- Create: `apps/desktop/src/styles.css`
- Create: `apps/desktop/vitest.config.ts`
- Create: `apps/desktop/src/test/setup.ts`
- Modify: `apps/desktop/package.json`

**Step 1: 写 UI 冒烟测试**

Install test deps:

```powershell
cd apps\desktop
npm install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

Create `apps/desktop/vitest.config.ts`:

```ts
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
  },
});
```

Create `apps/desktop/src/test/setup.ts`:

```ts
import "@testing-library/jest-dom/vitest";
```

Add a test script to `apps/desktop/package.json`:

```json
{
  "scripts": {
    "test": "vitest"
  }
}
```

Create `apps/desktop/src/App.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import App from "./App";

describe("App", () => {
  it("renders the product name", () => {
    render(<App />);
    expect(screen.getByText("Local KB")).toBeInTheDocument();
  });
});
```

Run:

```powershell
npm test -- --run
```

Expected: FAIL（App 还没用新布局）。

**Step 2: 实现布局与路由**

`apps/desktop/src/App.tsx`:

```tsx
import { HashRouter, Route, Routes } from "react-router-dom";
import Sidebar from "./components/Sidebar";
import StatusBar from "./components/StatusBar";
import Chat from "./pages/Chat";
import Jobs from "./pages/Jobs";
import Library from "./pages/Library";
import Onboarding from "./pages/Onboarding";
import Settings from "./pages/Settings";
import "./styles.css";

export default function App() {
  return (
    <HashRouter>
      <div className="app-shell">
        <Sidebar />
        <main className="app-main">
          <Routes>
            <Route path="/" element={<Library />} />
            <Route path="/onboarding" element={<Onboarding />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/chat" element={<Chat />} />
            <Route path="/settings" element={<Settings />} />
          </Routes>
        </main>
        <StatusBar />
      </div>
    </HashRouter>
  );
}
```

`Sidebar.tsx`:

```tsx
import { BookOpen, MessageSquare, Settings as SettingsIcon } from "lucide-react";
import { NavLink } from "react-router-dom";

const items = [
  { to: "/", label: "资料库", icon: BookOpen },
  { to: "/chat", label: "问答", icon: MessageSquare },
  { to: "/settings", label: "设置", icon: SettingsIcon },
];

export default function Sidebar() {
  return (
    <aside className="sidebar">
      <div className="brand">Local KB</div>
      {items.map(({ to, label, icon: Icon }) => (
        <NavLink key={to} to={to} end={to === "/"} className="nav-item">
          <Icon size={16} />
          <span>{label}</span>
        </NavLink>
      ))}
    </aside>
  );
}
```

`StatusBar.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";

export default function StatusBar() {
  const [text, setText] = useState("connecting...");

  useEffect(() => {
    apiFetch("/health")
      .then((r) => r.json())
      .then((data) => setText(data.ollama ? "本地服务正常 · Ollama 已连接" : "本地服务正常 · Ollama 未启动"))
      .catch(() => setText("本地服务未连接"));
  }, []);

  return <footer className="status-bar">{text}</footer>;
}
```

`Library.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";

type DocumentRow = {
  id: string;
  file_name: string;
  status: string;
  page_count: number;
  ocr_confidence: number | null;
};

export default function Library() {
  const [rows, setRows] = useState<DocumentRow[]>([]);

  useEffect(() => {
    apiFetch("/documents")
      .then((r) => r.json())
      .then(setRows)
      .catch(() => setRows([]));
  }, []);

  return (
    <section>
      <h1>资料库</h1>
      {rows.length === 0 ? (
        <p className="empty">还没有资料。选择一个文件夹开始导入。</p>
      ) : (
        <table className="table">
          <thead>
            <tr><th>文件</th><th>状态</th><th>页数</th><th>OCR 置信度</th></tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>{row.file_name}</td>
                <td>{row.status}</td>
                <td>{row.page_count}</td>
                <td>{row.ocr_confidence?.toFixed(2) ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
```

`Jobs.tsx`, `Settings.tsx`, `Onboarding.tsx` can start as simple forms calling the API.

**Step 3: 实现 SSE 问答**

Create `apps/desktop/src/api/stream.ts`:

```ts
import { apiFetch } from "./client";

export type AskEvent =
  | { event: "retrieval"; data: { chunks: any[] } }
  | { event: "token"; data: { text: string } }
  | { event: "citations"; data: { citations: any[]; invalid: any[] } }
  | { event: "done"; data: { text: string; model: string } }
  | { event: "error"; data: { message: string } };

export async function streamAsk(
  question: string,
  onEvent: (event: AskEvent) => void,
): Promise<void> {
  const response = await apiFetch("/ask", {
    method: "POST",
    body: JSON.stringify({ question }),
  });
  if (!response.body) throw new Error("stream unavailable");

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let index = buffer.indexOf("\n\n");
    while (index !== -1) {
      const raw = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const lines = raw.split("\n");
      let event = "message";
      let data = "";
      for (const line of lines) {
        if (line.startsWith("event: ")) event = line.slice(7);
        if (line.startsWith("data: ")) data += line.slice(6);
      }
      if (data) onEvent({ event, data: JSON.parse(data) } as AskEvent);
      index = buffer.indexOf("\n\n");
    }
  }
}
```

`Chat.tsx`:

```tsx
import { useState } from "react";
import { streamAsk, type AskEvent } from "../api/stream";

export default function Chat() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [citations, setCitations] = useState<any[]>([]);

  async function submit() {
    if (!question.trim()) return;
    setAnswer("");
    setCitations([]);
    await streamAsk(question, (event: AskEvent) => {
      if (event.event === "token") setAnswer((prev) => prev + event.data.text);
      if (event.event === "citations") setCitations(event.data.citations);
    });
  }

  return (
    <section>
      <h1>问答</h1>
      <textarea value={question} onChange={(e) => setQuestion(e.target.value)} />
      <button onClick={submit}>提问</button>
      <article className="answer">{answer}</article>
      <aside className="citations">
        {citations.map((citation, index) => (
          <div key={index}>
            {citation.file_name} p.{citation.page_no}
          </div>
        ))}
      </aside>
    </section>
  );
}
```

`PageViewer.tsx`:

```tsx
import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";

export default function PageViewer(props: { documentId: string; pageNo: number }) {
  const [text, setText] = useState("");

  useEffect(() => {
    apiFetch(`/documents/${props.documentId}/pages/${props.pageNo}`)
      .then((r) => r.json())
      .then((data) => setText(data.text))
      .catch(() => setText("原文不可用"));
  }, [props.documentId, props.pageNo]);

  return (
    <aside className="page-viewer">
      <h3>原文</h3>
      <pre>{text}</pre>
    </aside>
  );
}
```

**Step 4: 样式**

Create `apps/desktop/src/styles.css` with a simple layout:

```css
:root {
  font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
  color: #0f172a;
  background: #eef2f7;
}

body { margin: 0; }

.app-shell {
  display: grid;
  grid-template-columns: 220px 1fr;
  grid-template-rows: 1fr 32px;
  height: 100vh;
}

.sidebar {
  grid-row: 1 / 2;
  background: #111827;
  color: #e5e7eb;
  padding: 16px;
}

.brand { font-weight: 700; font-size: 18px; margin-bottom: 24px; }

.nav-item {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 12px;
  color: #cbd5e1;
  text-decoration: none;
  border-radius: 8px;
}

.nav-item.active { background: #1f2937; color: white; }

.app-main { padding: 24px; overflow: auto; }

.status-bar {
  grid-column: 1 / -1;
  background: #0f172a;
  color: #cbd5e1;
  font-size: 12px;
  padding: 6px 12px;
}

.table { width: 100%; border-collapse: collapse; background: white; }
.table th, .table td { text-align: left; padding: 10px; border-bottom: 1px solid #e2e8f0; }
.empty { padding: 24px; background: white; border-radius: 12px; }
.answer { white-space: pre-wrap; background: white; padding: 16px; border-radius: 12px; }
.citations { margin-top: 12px; }
```

**Step 5: 跑测试**

```powershell
cd apps\desktop
npm test -- --run
```

Expected: PASS。

**Step 6: 手动验证**

- 启动 API：`scripts/dev-api.ps1`
- 启动 Tauri：`npm run tauri dev`
- 资料库页面能加载
- 问答页面能发送问题（Ollama 未启动时显示错误）

**Step 7: Commit**

```powershell
git add apps/desktop
git commit -m "feat: add desktop UI for library, jobs, chat and settings"
```

---

## Task 8: 模型下载、离线模式与打包

**Files:**
- Create: `services/api/app/api/models.py`
- Create: `scripts/download-models.ps1`
- Create: `scripts/build-sidecar.ps1`
- Create: `services/api/kb-server.spec`
- Modify: `apps/desktop/src-tauri/tauri.conf.json`
- Modify: `apps/desktop/src-tauri/src/sidecar.rs`
- Modify: `apps/desktop/src/pages/Onboarding.tsx`
- Modify: `services/api/app/main.py`

**Step 1: 模型状态 API**

Create `services/api/app/api/models.py`:

```python
from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/models/status")
async def models_status(request: Request) -> dict:
    # Task 8 will check local model directories and Ollama tags.
    return {
        "ocr": {"ready": True},
        "embedding": {"ready": False},
        "llm": {"ready": False, "models": []},
    }


@router.post("/models/download")
async def download_models(request: Request, payload: dict) -> dict:
    # MVP: return instructions; the download script does the heavy lifting.
    return {
        "ok": True,
        "steps": [
            "ollama pull qwen2.5:7b",
            "python scripts/download_embedding.py",
        ],
    }
```

Register in `main.py`.

**Step 2: 下载脚本**

Create `scripts/download-models.ps1`:

```powershell
ollama pull qwen2.5:7b
.\.venv\Scripts\python.exe scripts\download_embedding.py
```

Create `scripts/download_embedding.py`:

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3", device="cpu")
model.save("models/bge-m3")
print("embedding model saved to models/bge-m3")
```

**Step 3: PyInstaller sidecar**

Create `services/api/kb-server.spec`:

```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for package in ["rapidocr_onnxruntime", "sentence_transformers", "jieba", "fitz"]:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run_server.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kb-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="kb-server")
```

Create `scripts/build-sidecar.ps1`:

```powershell
$ErrorActionPreference = "Stop"
cd services\api
..\..\.venv\Scripts\pyinstaller.exe --clean --noconfirm kb-server.spec
Copy-Item -Recurse -Force dist\kb-server ..\..\apps\desktop\src-tauri\binaries\kb-server
```

**Step 4: Tauri 配置**

Modify `apps/desktop/src-tauri/tauri.conf.json`:

```json
{
  "bundle": {
    "resources": ["binaries/kb-server/**"]
  }
}
```

`sidecar.rs` 的 release 分支使用 `resource_dir/binaries/kb-server/kb-server.exe`，与上面的 `resources` 路径保持一致。

**Step 5: 离线模式**

- Tauri CSP `connect-src` only `http://127.0.0.1:*` and `http://tauri.localhost`.
- Python code does not call external services except Ollama on localhost.
- Add a test that mocks `httpx` external calls and asserts none occur.

**Step 6: 打包测试**

```powershell
cd apps\desktop
npm run tauri build
```

Expected: 生成安装包；安装后启动，sidecar 正常运行。

**Step 7: Commit**

```powershell
git add services/api scripts apps/desktop/src-tauri apps/desktop/src
git commit -m "feat: model download, offline mode and desktop packaging"
```

---

## Task 9: 黄金集、性能与收尾

**Files:**
- Create: `tests/golden/questions.json`
- Create: `scripts/eval_golden.py`
- Create: `docs/benchmarks/2026-10-27-local-kb.md`
- Modify: `README.md`

**Step 1: 准备黄金集**

Create `tests/golden/questions.json`:

```json
[
  {
    "question": "2024 年度报告里营收增长了多少？",
    "expected_file": "2024年度报告.pdf",
    "expected_page": 12
  },
  {
    "question": "合同中约定的验收标准是什么？",
    "expected_file": "项目合同.pdf",
    "expected_page": 3
  }
]
```

至少准备 15～20 个问题，覆盖不同文件和题型。

**Step 2: 评估脚本**

Create `scripts/eval_golden.py`:

```python
import json
import sys
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8765/api/v1"
TOKEN = "dev-token"


def ask(question: str) -> dict:
    with httpx.Client(timeout=60) as client:
        response = client.post(
            f"{BASE}/ask",
            headers={"X-App-Token": TOKEN},
            json={"question": question},
        )
        return response.json()


def main() -> None:
    questions = json.loads(Path("tests/golden/questions.json").read_text(encoding="utf-8"))
    correct = 0
    for item in questions:
        result = ask(item["question"])
        citations = result.get("citations", [])
        ok = any(
            c["file_name"] == item["expected_file"] and c["page_no"] == item["expected_page"]
            for c in citations
        )
        correct += int(ok)
        print(("PASS" if ok else "FAIL"), item["question"])
    print(f"{correct}/{len(questions)}")
    sys.exit(0 if correct / len(questions) >= 0.8 else 1)


if __name__ == "__main__":
    main()
```

**Step 3: 性能记录**

Create `docs/benchmarks/2026-10-27-local-kb.md`:

```markdown
# Local KB Benchmark

- 资料数：
- 总页数：
- OCR 耗时：
- 索引耗时：
- 索引大小：
- 问答 P50 / P95：
- 内存峰值：
- 模型：qwen2.5:7b / 4b
```

**Step 4: 跑黄金集**

```powershell
.\.venv\Scripts\python.exe scripts\eval_golden.py
```

Expected: 至少 80% PASS。

**Step 5: 收尾**

- 更新 `README.md`：安装、首次启动、模型下载、导入、问答
- 更新 `.trellis/spec/`：把实际踩坑写入规范
- 归档任务

**Step 6: Commit**

```powershell
git add tests scripts docs README.md
git commit -m "test: add golden set and local kb benchmark"
```

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-10-27-local-knowledge-base.md`.

Two execution options:

1. **Subagent-Driven（当前会话）**：每个 Task 派一个全新子 Agent，任务间做 spec 审查 + 代码质量审查，适合快速迭代。
2. **Parallel Session（独立会话）**：在新会话中使用 `executing-plans`，按 Task 批量执行，带检查点。

如果选 1，使用 `superpowers:subagent-driven-development`；如果选 2，新会话使用 `superpowers:executing-plans`。

在开始实现前，请先完成：

```powershell
git init
trellis init -u <your-name>
```
