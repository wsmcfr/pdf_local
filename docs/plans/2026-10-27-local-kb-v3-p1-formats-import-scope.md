# Local KB v3 P1：Office 格式、单文件导入与单文件对话

> **For the implementing agent:** 按任务顺序执行；每个任务先写测试、再实现、再提交。

**Goal:** 让 `.docx / .pptx / .xlsx` 可以导入并进入问答，支持单文件导入、资料库右键“和这个文件对话”，以及单文件全文/文件内检索两种上下文模式。

**Architecture:** 在现有 `IngestPipeline` 中按后缀分派解析器，新增 `blocks` 表保存文字块与文件位置；新增 `import` 任务类型；`/ask` 增加 `document_id`，短文走全文、长文走文件内 FTS+向量检索。

**Tech Stack:** FastAPI、SQLite、python-docx、python-pptx、openpyxl、React + Tauri dialog。

---

## Task 1：依赖与格式白名单

**Files:**
- Modify: `requirements.txt`
- Modify: `services/api/app/utils/paths.py`
- Test: `services/api/tests/test_paths.py`

**Step 1: 写失败测试**

```python
from pathlib import Path
from app.utils.paths import is_supported

def test_office_formats_are_supported():
    for name in ["a.docx", "b.pptx", "c.xlsx", "d.md"]:
        assert is_supported(Path(name))
    assert not is_supported(Path("old.doc"))
```

**Step 2: 运行并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_paths.py -q`
Expected: FAIL（docx 不在白名单）

**Step 3: 实现**

`requirements.txt` 追加：

```
python-docx==1.1.*
python-pptx==1.0.*
openpyxl==3.1.*
```

`SUPPORTED_SUFFIXES = {".pdf", ".md", ".markdown", ".txt", ".docx", ".pptx", ".xlsx"}`。

**Step 4: 安装依赖并跑测试**

Run: `.\.venv\Scripts\python.exe -m pip install -r requirements.txt`
Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_paths.py -q`
Expected: PASS

**Step 5: Commit**

```
git add requirements.txt services/api/app/utils/paths.py services/api/tests/test_paths.py
git commit -m "feat(ingest): support docx/pptx/xlsx file types"
```

---

## Task 2：blocks 表与迁移

**Files:**
- Modify: `services/api/app/schema.sql`
- Modify: `services/api/app/db.py`
- Test: `services/api/tests/test_db_migration.py`

**Step 1: 写失败测试**

```python
from app.db import connect, init_db

def test_blocks_table_and_document_columns(tmp_path):
    conn = connect(tmp_path / "app.db")
    init_db(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
    assert {"editable", "source_format", "converted_from"} <= cols
    conn.execute("SELECT id, document_id, page_no, order_index, kind, text, locator_json, bbox_json, editable, approximate FROM blocks LIMIT 0")
```

**Step 2: 运行并确认失败**

Expected: FAIL（no such table: blocks）

**Step 3: 实现**

`schema.sql` 追加 `blocks` 表（字段与设计文档一致），`PRAGMA user_version = 3`。
`db.init_db` 在 `executescript` 后执行：

```python
def _ensure_columns(conn, table, columns):
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _ensure_columns(conn, "documents", {
        "editable": "INTEGER NOT NULL DEFAULT 0",
        "source_format": "TEXT NOT NULL DEFAULT ''",
        "converted_from": "TEXT",
    })
    conn.commit()
```

**Step 4: 跑测试**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_db_migration.py -q`
Expected: PASS

**Step 5: Commit**

```
git commit -am "feat(db): add blocks table and editable document columns"
```

---

## Task 3：Office 解析器

**Files:**
- Create: `services/api/app/ingest/formats.py`
- Test: `services/api/tests/test_formats.py`

**Step 1: 写失败测试**（用 python-docx/pptx/openpyxl 在 tmp_path 生成真实文件）

```python
from docx import Document
from pptx import Presentation
from openpyxl import Workbook
from app.ingest.formats import parse_office_file

def test_docx_blocks(tmp_path):
    path = tmp_path / "a.docx"
    doc = Document(); doc.add_paragraph("第一段"); doc.add_paragraph("第二段"); doc.save(path)
    parsed = parse_office_file(path)
    assert [b.text for b in parsed.blocks] == ["第一段", "第二段"]
    assert parsed.blocks[0].locator == {"paragraph": 0}
    assert parsed.blocks[0].editable is True

def test_pptx_blocks(tmp_path):
    path = tmp_path / "a.pptx"
    prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "标题"; slide.placeholders[1].text = "正文"
    prs.save(path)
    parsed = parse_office_file(path)
    assert any(b.text == "标题" for b in parsed.blocks)
    assert any(b.locator["slide"] == 1 for b in parsed.blocks)

def test_xlsx_blocks_read_only(tmp_path):
    path = tmp_path / "a.xlsx"
    wb = Workbook(); ws = wb.active; ws["A1"] = "姓名"; ws["B1"] = "分数"; ws["A2"] = "张三"; ws["B2"] = 90; wb.save(path)
    parsed = parse_office_file(path)
    assert parsed.blocks[0].kind == "cell"
    assert parsed.blocks[0].locator == {"sheet": "Sheet", "row": 1, "col": 1}
    assert parsed.blocks[0].editable is False
```

**Step 2: 运行并确认失败**（module not found）

**Step 3: 实现**

`ParsedBlock` / `ParsedPage` / `ParsedDocument` dataclass，`parse_office_file(path)` 按后缀分派：

- docx：段落 + 表格单元格；段落按 2000 字符聚合成伪页；`kind="paragraph"`，`locator={"paragraph": idx}`；表格 `kind="cell"`，`locator={"table": ti, "row": ri, "col": ci}`。
- pptx：每页每个含文字的 shape 的每个非空段落；`locator={"slide": n, "shape": shape_id, "paragraph": p}`；`bbox` 用 shape 位置 / 页面尺寸归一化。
- xlsx：`read_only=True, data_only=True`，每个非空单元格 `kind="cell"`；页按工作表编号；最多 20000 个单元格。
- txt/md：按行/段落切块，`kind="text"`，`locator={"offset": n}`。

**Step 4: 跑测试**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_formats.py -q`
Expected: PASS

**Step 5: Commit**

```
git add services/api/app/ingest/formats.py services/api/tests/test_formats.py
git commit -m "feat(ingest): parse docx, pptx and xlsx into blocks"
```

---

## Task 4：Pipeline 接入 blocks

**Files:**
- Modify: `services/api/app/ingest/pipeline.py`
- Test: `services/api/tests/test_pipeline_formats.py`

**Step 1: 写失败测试**

用 `ScannedFile` 构造 docx，调用 `_ingest_file`，断言 `documents.status == "ready"`、`pages` 非空、`blocks` 数量与段落一致。

**Step 2: 运行并确认失败**

**Step 3: 实现**

- `mime_for(suffix)`：docx/pptx/xlsx 对应 Office MIME，md 为 `text/markdown`，其余 `text/plain`。
- `documents` upsert 写入 `source_format` 与 `editable`（docx/pptx/md/txt 为 1，xlsx/pdf 为 0）。
- 重新导入时 `DELETE FROM blocks WHERE document_id=?`。
- `_write_pages`：PDF 保持原逻辑；其他后缀调用 `parse_office_file`，批量插入 `pages` 与 `blocks`。
- 返回 `len(pages)`。

**Step 4: 跑测试**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_pipeline_formats.py services/api/tests/test_indexer.py -q`
Expected: PASS

**Step 5: Commit**

```
git commit -am "feat(ingest): persist blocks for office documents"
```

---

## Task 5：单文件导入任务

**Files:**
- Modify: `services/api/app/ingest/pipeline.py`
- Modify: `services/api/app/jobs/runner.py`
- Modify: `services/api/app/api/jobs.py`
- Test: `services/api/tests/test_import_files.py`

**Step 1: 写失败测试**

```python
def test_import_files_creates_documents(tmp_path):
    # 用 TestClient 打开 app，POST /api/v1/import/files
    # 传入 tmp 下真实 docx 路径，轮询 /jobs/{id} 直到 done
    # 断言 /documents 里出现该文件
```

**Step 2: 运行并确认失败**（404）

**Step 3: 实现**

- `IngestPipeline.run_files(job_id, paths, checkpoint)`：把每个路径包成 `ScannedFile(root=parent)`，复用 `_ingest_file`；进度写入 jobs。
- `JobRunner._run_job` 增加 `import` 分支。
- `POST /api/v1/import/files`：校验 `paths` 是非空字符串数组、文件存在、后缀受支持；返回 `enqueue("import", {"paths": [...]})`。

**Step 4: 跑测试**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_import_files.py services/api/tests/test_jobs.py -q`
Expected: PASS

**Step 5: Commit**

```
git commit -am "feat(api): import individual files"
```

---

## Task 6：检索按文件过滤

**Files:**
- Modify: `services/api/app/index/fts.py`
- Modify: `services/api/app/qa/retriever.py`
- Test: `services/api/tests/test_retriever_scope.py`

**Step 1: 写失败测试**

构造两个文档的 chunks，`search(..., document_id="d1")` 只返回 d1。

**Step 2: 运行并确认失败**（unexpected keyword）

**Step 3: 实现**

- `fts.search(conn, query, limit=20, document_id=None)`：有过滤时 SQL 连接 `chunks` 并 `WHERE chunks.document_id = ?`。
- `HybridRetriever.search(question, limit=8, document_id=None)`；向量结果按 embedding_id 查 `chunks.document_id` 过滤。

**Step 4: 跑测试**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_retriever_scope.py services/api/tests/test_retrieve.py -q`
Expected: PASS

**Step 5: Commit**

```
git commit -am "feat(qa): scope retrieval to one document"
```

---

## Task 7：/ask 单文件上下文

**Files:**
- Modify: `services/api/app/api/ask.py`
- Test: `services/api/tests/test_ask_scope.py`

**Step 1: 写失败测试**

- 短文（≤30000 字符）：`document_id` 存在时 `retrieval` 事件带 `context_mode: "full_text"`，且不调用 retriever。
- 长文：`context_mode: "retrieval"`，retriever 收到 `document_id`。
- 文件不存在：SSE `DOCUMENT_NOT_FOUND`。

**Step 2: 运行并确认失败**

**Step 3: 实现**

```python
document_id = str(payload.get("document_id") or "").strip() or None
...
if document_id:
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if not doc:
        yield sse("error", {"code": "DOCUMENT_NOT_FOUND", "message": "文件不存在"})
        return
    pages = db.execute(...).fetchall()
    total = sum(len(p["text"]) for p in pages)
    if total <= 30000:
        contexts = [{"chunk_id": f"page-{p['page_no']}", "document_id": document_id,
                     "page_no": p["page_no"], "text": p["text"],
                     "file_name": doc["file_name"], "mime": doc["mime"]} for p in pages if p["text"]]
        context_mode = "full_text"
    else:
        contexts = await asyncio.to_thread(retriever.search, question, limit, document_id)
        context_mode = "retrieval"
else:
    ... 原逻辑 ...
yield sse("retrieval", {"chunks": contexts, "context_mode": context_mode})
```

**Step 4: 跑测试**

Run: `.\.venv\Scripts\python.exe -m pytest services/api/tests/test_ask.py services/api/tests/test_ask_scope.py -q`
Expected: PASS（原测试需兼容 `context_mode` 字段）

**Step 5: Commit**

```
git commit -am "feat(api): document-scoped ask with full-text fallback"
```

---

## Task 8：前端 API 与类型

**Files:**
- Modify: `apps/desktop/src/api/scan.ts`
- Modify: `apps/desktop/src/api/stream.ts`
- Modify: `apps/desktop/src/api/types.ts`

**Step 1: 实现**

- `pickFiles()`：`open({ multiple: true, directory: false, filters: [...] })`，返回 `string[]`。
- `startImport(paths)`：`POST /import/files`。
- `streamAsk(question, onEvent, signal, documentId?)`，body 带 `document_id`。
- `AskEvent` 的 `retrieval` 增加 `context_mode?: "full_text" | "retrieval"`。
- `DocumentRow` 增加 `editable?: boolean`、`source_format?: string`。

**Step 2: 构建**

Run: `npm run build`
Expected: PASS

---

## Task 9：资料库导入与右键菜单

**Files:**
- Modify: `apps/desktop/src/pages/Library.tsx`
- Modify: `apps/desktop/src/styles.css`

**Step 1: 实现**

- 页头：`导入文件`（FilePlus 图标）+ `导入文件夹`。
- 表格行 `onContextMenu` 打开绝对定位菜单：`和这个文件对话` → `navigate('/chat?document=' + row.id)`。
- 点击其他区域关闭菜单；双击行同样进入对话。
- 标题文案更新为支持格式列表。

**Step 2: 构建 + 测试**

Run: `npm run build; npx vitest run`
Expected: PASS

---

## Task 10：聊天单文件作用域

**Files:**
- Modify: `apps/desktop/src/pages/Chat.tsx`
- Modify: `apps/desktop/src/styles.css`

**Step 1: 实现**

- 用 `useSearchParams` 读取 `document`；
- 有作用域时请求 `/documents/{id}` 显示胶囊标签：`与《文件名》对话` + `退出`（`setSearchParams({})` 并清空回答状态）；
- 提交时把 `documentId` 传给 `streamAsk`；
- `retrieval` 事件设置 `contextMode`，在消息区上方显示：
  - `full_text` → “已载入该文件全文”
  - `retrieval` → “文件较长，已切换为文件内检索”
- 空态示例问题在单文件模式下改为“这份文件的要点是什么？ / 总结这份文件 / 有哪些风险？”

**Step 2: 构建 + 测试**

Run: `npm run build; npx vitest run`
Expected: PASS

---

## Task 11：整体验证

- 后端：`.\.venv\Scripts\python.exe -m pytest services/api/tests -q` → 全部通过
- 前端：`npm run build` + `npx vitest run` → 通过
- 手工：
  1. 导入一个 `.docx`、`.pptx`、`.xlsx`；
  2. 资料库右键 → 和这个文件对话；
  3. 短文件显示“已载入全文”，长文件显示“文件内检索”；
  4. 回答引用可点击并打开对应页。
