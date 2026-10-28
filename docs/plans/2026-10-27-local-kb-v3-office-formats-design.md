# Local KB v3：Office 格式、单文件对话、可编辑原文与 AI 演示文稿

- 日期：2026-10-27
- 状态：已确认设计，待实现
- 关系：在 v2（本地知识库问答）基础上扩展，不推翻 v2 的检索、Provider、网关与 MCP 能力

## 1. 已确认的产品决策

| 议题 | 决策 |
|---|---|
| 修改方式 | 直接回写原文件；每次保存前自动备份，支持恢复/撤销 |
| 第一批格式 | `.docx` / `.pptx` / `.txt` / `.md` 可读可改；`.xlsx` 只读；`.pdf` 只读 |
| 旧格式 | 导入时检测 `.doc` / `.ppt`，用本机 Office 后台转换为 `.docx` / `.pptx` 副本 |
| AI 文字修改 | 选中段落 / PPT 文本框后给指令；只改文字，保留字体、版式、图片、动画 |
| 单文件对话 | 文字 ≤ 3 万字符时全文入上下文；超过则退化为文件内检索，并提示 |
| 预览联动 | Word 近似逐段联动，PPT/PDF 精确联动；需显示每个文字块的预览高亮 |
| 素材 | 全局素材库；图片/视频导入后复制到数据目录；AI 按素材名称/描述选用 |
| 媒体插入 | 位置支持“预览上拖框”与“选中已有元素作锚点”；应用前用虚线框确认 |
| 新增幻灯片 | 支持；可从空壳模板生成整份 PPT（大纲 → 生成 → 预览 → 另存/覆盖） |
| 生成保存 | 默认另存为 `原名-AI-时间戳.pptx`；覆盖必须备份 |

### 非目标（第一批不做）

- 动画、切换、SmartArt、图表、复杂自定义形状
- 像素级自由设计；AI 只在模板版式与占位符内排版
- PDF 内容编辑；Excel 单元格编辑
- 独立图片文件的 OCR 导入（仍以 PDF 内扫描页为主）
- 非 Windows 平台的 Office 预览/转换（将优雅降级）

## 2. 总体架构

```
Tauri (React)
  ├── 资料库：文件/文件夹导入、右键菜单、单文件对话
  ├── 文档视图：预览图 + 文字块 + 编辑/素材/AI 设计
  └── 聊天：全库 / 单文件作用域
        │ HTTP + SSE（X-App-Token）
Python sidecar (FastAPI)
  ├── ingest：扫描、单文件导入、旧格式转换
  ├── parsers：pdf / docx / pptx / xlsx / text
  ├── blocks：文字块与文件位置映射
  ├── preview：Office COM 导出 PDF/PNG（缓存）
  ├── editor：备份、保存、恢复、保存后重建
  ├── assets：素材库（图片/视频）
  ├── ai_edit：结构化编辑动作（文本 / 媒体 / 新增页）
  └── qa：全文模式 / 文件作用域检索
```

### 2.1 解析与文字块

每种格式解析为统一的 `blocks`：

- `docx`：段落（含表格单元格段落）；`locator = {"paragraph": n}`，样式来自段落/首个 run。
- `pptx`：每页每个含文字的形状/段落；`locator = {"slide": n, "shape": shape_id, "paragraph": p}`；`bbox` 取形状坐标归一化。
- `xlsx`：每个非空单元格或行；`locator = {"sheet": name, "row": r, "col": c}`；只读。
- `pdf`：PyMuPDF 的文本块/行；`locator = {"block": n, "line": l}`；`bbox` 来自页面坐标。
- `txt/md`：按段落/行切块；`locator = {"offset": n}`。

`blocks` 是“查看、定位、人工编辑、AI 编辑”的唯一数据源；`pages`/`chunks` 继续服务检索。

### 2.2 预览

- PPT：PowerPoint COM 将每页导出 PNG（`data/previews/<doc>/slide-<n>.png`），可直接使用形状 bbox 叠加高亮。
- Word：Word COM 导出 PDF（`data/previews/<doc>/preview.pdf`），PyMuPDF 渲染页面；段落与 PDF 文本做顺序对齐，得到页码 + 近似矩形。
- PDF：直接用现有页图 + PyMuPDF 坐标。
- XLSX：前端表格视图，按单元格高亮，无需位图。
- 预览在导入后异步生成；编辑保存后重新生成。生成失败不阻塞问答，只降级为“无预览”。

### 2.3 编辑与保存

保存流水线（所有可写格式）：

1. 校验路径属于已导入路径，且不是只读文件；
2. 检测 Office 锁文件/独占打开，若被占用则提示先关闭；
3. 写入同目录临时文件，重新打开校验；
4. 将原文件复制到 `data/backups/<document_id>/<timestamp>-<name>`（保留最近 10 份）；
5. 原子替换原文件；
6. 重新解析 blocks/pages/chunks，重新生成预览，重建向量；单文件失败不影响其他文档。

文本写回：

- `docx`：按段落 locator 替换；保留段落样式与首个 run 的字符格式；段落内混合格式会简化为首个 run 的格式（界面提示）。
- `pptx`：按 slide/shape/paragraph 替换；保留段落与占位符样式；run 级混排同样简化。
- `txt/md`：直接按偏移替换或整文件保存。

### 2.4 AI 编辑动作

AI 不直接改文件，只返回结构化动作，由后端校验后执行：

```json
{"actions": [
  {"type": "replace_text", "block_id": "b12", "text": "新的段落文字"},
  {"type": "insert_media", "slide": 3, "asset_id": "a7",
   "rect": {"x": 0.55, "y": 0.2, "w": 0.35, "h": 0.4}},
  {"type": "replace_media", "shape_id": "s4", "asset_id": "a9"}
]}
```

- 文字动作：先进入编辑器草稿，用户看到 diff 后点“应用”，仍需手动保存。
- 媒体动作：缺少 rect 时使用锚点/占位符推断；应用前在预览上画虚线框。
- 所有动作都在工作副本上执行，保存时才落盘并备份。

### 2.5 AI 生成整份 PPT（P5）

1. 输入：目标/受众/页数/风格、可选参考文档、可选素材；
2. AI 输出大纲 JSON（封面、目录、分节、内容页；每页版式、标题、要点、素材建议）；
3. 大纲编辑器允许增删页、改标题、换版式；
4. 后端以所选 `.pptx` 为模板（空壳只含母版/版式也可），新增幻灯片并填充占位符；
5. 图片用 python-pptx 插入，视频用 PowerPoint COM 嵌入；
6. 导出预览供确认；
7. 默认另存为 `原名-AI-时间戳.pptx`，或覆盖原文件（强制备份）。

版式来源：模板已有 layout（封面/标题+内容/分节/双栏/空白）；无模板时使用内置默认模板。

### 2.6 单文件对话

- 从资料库右键“和这个文件对话”，或聊天页选择文件；
- `POST /ask` 增加 `document_id`；
- 文字 ≤ 30,000 字符：全文注入上下文（带页/幻灯片标记），引用仍按文件页校验；
- 超过：`document_id` 限定 FTS + 向量检索，返回 `context_mode: "retrieval"`；
- 前端显示作用域标签与模式提示。

## 3. 数据模型新增

```sql
CREATE TABLE blocks (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_no INTEGER NOT NULL,
  order_index INTEGER NOT NULL,
  kind TEXT NOT NULL,             -- paragraph | text_frame | cell | text | line
  text TEXT NOT NULL,
  locator_json TEXT NOT NULL,
  bbox_json TEXT,
  editable INTEGER NOT NULL DEFAULT 0,
  approximate INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE assets (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,             -- image | video | audio
  path TEXT NOT NULL UNIQUE,
  width INTEGER, height INTEGER,
  duration_ms INTEGER,
  size_bytes INTEGER NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  sha256 TEXT NOT NULL,
  thumbnail_path TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE backups (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE preview_files (
  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,             -- pdf | image
  page_no INTEGER NOT NULL,
  path TEXT NOT NULL,
  width INTEGER, height INTEGER,
  PRIMARY KEY (document_id, kind, page_no)
);
```

`documents` 追加：

```sql
ALTER TABLE documents ADD COLUMN editable INTEGER NOT NULL DEFAULT 0;
ALTER TABLE documents ADD COLUMN source_format TEXT NOT NULL DEFAULT '';
ALTER TABLE documents ADD COLUMN converted_from TEXT;
```

数据库迁移按 `PRAGMA user_version` 升到 3，使用 `ALTER TABLE` 兼容旧库。

## 4. API 变更

- `POST /api/v1/import/files`：单文件/多文件导入（绝对路径列表）。
- `POST /api/v1/scan`：保持文件夹导入；新增 `converted` 与 blocks 生成。
- `GET /api/v1/documents/{id}/blocks`
- `PATCH /api/v1/documents/{id}/blocks`：批量更新文字（仅草稿，不落盘）。
- `POST /api/v1/documents/{id}/save`：备份 + 写回 + 重建。
- `POST /api/v1/documents/{id}/restore/{backup_id}`
- `GET /api/v1/documents/{id}/backups`
- `GET /api/v1/documents/{id}/preview/{page}`：页图/PDF 渲染图。
- `POST /api/v1/documents/{id}/ai-edit`：块级指令，返回结构化动作。
- `POST /api/v1/documents/{id}/ai-design`：生成大纲。
- `POST /api/v1/documents/{id}/ai-design/apply`：按大纲生成工作副本。
- `GET/POST /api/v1/assets`、`DELETE /api/v1/assets/{id}`。
- `/api/v1/ask` 增加 `document_id`，返回 `context_mode`。

## 5. UI

- 资料库：
  - “导入文件 / 导入文件夹”两个入口；
  - 右键菜单：和这个文件对话、查看原文、编辑、在文件夹中显示、重新索引。
- 聊天：
  - 顶部作用域标签（全库 / 文件名），可退出；
  - 全文模式提示“已载入全文”，超长时提示“已切换为文件内检索”。
- 文档视图：
  - 左：预览页/幻灯片；右：文字块列表；
  - 文字块 ↔ 预览高亮联动；
  - 可编辑块显示编辑按钮；底部“保存”“撤销上次保存”。
- 素材面板：导入、缩略图、名称、描述、删除；PPT 编辑时“插入素材”。
- AI 设计：大纲编辑 → 生成预览 → 应用/另存。

## 6. 安全与失败处理

- 只有用户通过文件选择器/文件夹导入的路径可写；后端再次校验路径与后缀。
- 保存前强制备份；写失败保留原文件；恢复操作也走备份。
- 旧格式转换只写 `data/converted/`，不覆盖原文件。
- 素材复制进 `data/assets/`，不引用外部路径，避免链接失效。
- Office COM 仅导出预览/插入视频；文字修改用 python-docx/pptx，避免长期占用 Office。
- 文件被 Office 占用、无写权限、磁盘不足时返回可读错误码，UI 给出具体提示。
- 预览生成失败、Office 缺失时自动降级，不阻塞问答与文字编辑。

## 7. 测试

- 单元：docx/pptx/xlsx 解析、blocks 定位、文本写回后重新解析一致、备份/恢复、路径校验、AI 动作校验。
- 集成：单文件导入 → blocks/pages/chunks；单文件对话全文/检索两条路径；保存 → 备份 → 重建索引。
- 前端：右键菜单、作用域标签、块选中 ↔ 高亮、diff 应用、素材面板。
- 手工验收：真实 Word/PPT 修改后 Office 打开正常；PPT 视频插入可播放；空壳模板生成整份 PPT。

## 8. 阶段与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 | 新格式解析、blocks、单文件导入、右键对话、单文件上下文 | docx/pptx/xlsx 可导入；单文件提问命中文件内容；超长时切换检索 |
| P2 | 人工编辑、保存/备份/撤销、重建 | 改 Word/PPT/TXT/MD 后原文件生效；备份可恢复；索引更新 |
| P3 | AI 块级文字修改、素材库、媒体插入 | 选中块改写 diff 可应用；图片/视频插入指定位置 |
| P4 | 预览联动、旧格式转换、Excel 只读视图 | PPT 点击块高亮；Word 近似定位；.doc/.ppt 自动转换 |
| P5 | AI 整份 PPT 生成 | 空壳模板生成多页预览；另存新文件；覆盖有备份 |

## 9. 风险

- Word 分页/坐标只能近似，重复文本可能定位偏差；UI 明确标注。
- 视频嵌入依赖 Office COM，生成慢且文件体积大；失败时保留已生成内容并提示。
- 模板版式质量决定生成效果；不支持动画/图表导致设计感有限。
- 编辑会简化段落内混合格式；需要清晰提示，避免用户误解为“完全无损”。
