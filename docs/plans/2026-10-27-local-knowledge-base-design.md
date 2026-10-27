# 本地知识库问答桌面应用 · 设计文档

> **已被 v2 取代**：v2 改为外部 Provider + 本地 AI 网关，见 `2026-10-27-local-knowledge-base-v2-design.md`。本文保留为 v1 历史记录。

- **日期**：2026-10-27
- **状态**：设计已确认，待实现
- **目标用户**：单人（项目所有者）
- **运行环境**：Windows，Ryzen 7 8845H / 16GB RAM / RTX 4060 Laptop 8GB
- **技术约束**：完全本地运行；安装时可联网下载模型；Tauri + React；Python sidecar

---

## 1. 背景与目标

目标是把手头散落的本地资料（PDF 为主，含扫描件，中英混合）变成一个**可搜索、可提问、回答带原文页码引用**的本地知识库。

核心价值不是“聊天”，而是：

1. 问一个问题，得到基于自己资料的回答
2. 每个结论都能点回原文页核对
3. 资料里没有答案时，明确说“没有找到”，不编造
4. 全程本地，资料不上传

## 2. 用户与使用场景

### 2.1 主要场景

- 查找某份报告、合同、课程资料中的定义、结论、数据
- 跨多份资料做对比和归纳
- 看完回答后，立刻查看引用页的原文
- 新增资料后增量导入，不重复处理旧文件

### 2.2 非目标

- 不做多用户、权限、团队协作
- 不做云端同步、在线抓取、浏览器插件
- 不做通用聊天机器人
- 不做笔记编辑、知识图谱、移动端

## 3. 约束与假设

| 约束 | 说明 |
|---|---|
| 完全本地 | 运行时不能调用云端 OCR/LLM/embedding；资料不出本机 |
| 安装可联网 | 首次安装允许下载模型；下载完成后可离线运行 |
| 资料规模 | 100～1,000 份，PDF 为主，一半以上为扫描件 |
| 语言 | 中文为主，中英混合 |
| 硬件 | RTX 4060 8GB VRAM，16GB RAM，F 盘约 811GB 空闲 |
| 技术栈 | Tauri + React + TypeScript；Python FastAPI sidecar |
| 工具链 | Node 22、npm 11、Rust 1.93 已安装；Ollama 尚未安装；系统 Python 3.13 |

## 4. 方案选择

### 4.1 采用方案：轻量全本地 RAG（方案 A）

```text
RapidOCR + PyMuPDF + SQLite FTS5 + FAISS + BGE-M3
+ Ollama（Qwen2.5-7B/4B）
+ Tauri + React + Python FastAPI sidecar
```

选择理由：

- 100～1,000 份资料规模下，SQLite + FAISS 足够，不需要 LanceDB/向量数据库服务
- RapidOCR 对中英混排和扫描件够用，依赖比 PaddleOCR 轻
- Tauri 只做桌面壳，核心逻辑在 Python，后续替换 OCR/模型不影响 UI
- 可以分阶段：先本地 Web 跑通，再套 Tauri；但本设计直接按 Tauri 结构组织

### 4.2 暂不采用

| 方案 | 暂不采用原因 |
|---|---|
| B. PaddleOCR PP-Structure + LanceDB + llama.cpp | 表格/公式更好，但安装包大、显存/内存紧张、打包复杂，对 MVP 过重 |
| C. 纯关键词搜索 | 无法自然语言问答，不满足目标 |
| 云端 OCR/LLM | 违反“资料不出本机”的硬约束 |

### 4.3 升级路径

如果后续发现复杂表格/公式/多栏版面效果不足，只替换解析层：

- 普通页继续 RapidOCR
- 复杂页调用 PP-Structure 或 MinerU
- 保留 SQLite/FAISS/问答/UI 不变

## 5. 产品范围（MVP）

### 5.1 第一版要做

| 能力 | 说明 |
|---|---|
| 文件夹导入 | 递归扫描，支持 PDF、Markdown、TXT |
| PDF 文字提取 | 文字版 PDF 直接抽取 |
| 扫描 PDF OCR | 页级 OCR，保存页文本与置信度 |
| 增量导入 | 文件哈希，跳过未变化文件；支持中断续跑 |
| 混合索引 | SQLite FTS5 关键词 + FAISS 向量 |
| 带引用问答 | 回答标注 `[文件名 p.页码]`；Markdown/TXT 标注章节 |
| 原文对照 | 点击引用查看 PDF 页图或 Markdown/TXT 文本 |
| 本地模型 | 首次下载，运行时离线；可选 4B/7B |
| 基础设置 | 数据目录、OCR DPI、模型、离线模式 |

### 5.2 第一版不做

- Word / PPT / Excel 解析
- 表格结构还原、公式识别
- OCR 人工纠错界面
- 多用户、云同步、移动端
- 浏览器插件、自动网页抓取
- 笔记编辑、知识图谱
- 多资料库/集合管理

## 6. 验收标准

1. 导入 50～100 份真实资料不崩溃，中断后可继续
2. 15～20 个黄金问题：**80% 以上回答正确，且引用到正确文件和页码**
3. 资料中没有答案时，明确回答“资料中没有找到”
4. 模型预热后，单次问答 ≤ 10 秒
5. OCR 低置信度页在 UI 有标记
6. 断网后，除模型下载外所有功能可用
7. 安装包在干净的 Windows 环境可启动、可停止 sidecar

## 7. 总体架构

```text
┌──────────────────────────────────────────────┐
│ Tauri 桌面壳（Rust）                          │
│  ├─ 启动/停止 Python sidecar                  │
│  ├─ 窗口、菜单、文件选择                       │
│  └─ 只允许访问 127.0.0.1 本地端口              │
└──────────────────────┬───────────────────────┘
                       │ HTTP + SSE
┌──────────────────────▼───────────────────────┐
│ React + TypeScript UI                        │
│  ├─ 资料库 / 导入进度                         │
│  ├─ 问答                                     │
│  ├─ 原文页对照                               │
│  └─ 设置                                     │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│ Python FastAPI sidecar                       │
│  ├─ api/          路由、鉴权、SSE             │
│  ├─ ingest/       扫描、解析、OCR、切分        │
│  ├─ index/        SQLite FTS5 + FAISS        │
│  ├─ qa/           检索、提示词、引用校验       │
│  ├─ llm/          Ollama 客户端               │
│  ├─ jobs/         后台任务队列、断点续跑       │
│  └─ config/       数据目录、模型、参数         │
└──────────────────────┬───────────────────────┘
                       │
┌──────────────────────▼───────────────────────┐
│ 本地数据目录                                  │
│  ├─ app.db        SQLite（元数据 + FTS5）     │
│  ├─ vectors.faiss 向量索引                    │
│  ├─ models/       OCR / embedding 模型        │
│  ├─ cache/        可选页图缓存                 │
│  └─ logs/         运行日志                     │
└──────────────────────────────────────────────┘
```

## 8. 模块设计

| 模块 | 职责 | 输入 | 输出 |
|---|---|---|---|
| `apps/desktop` | Tauri 壳、窗口、sidecar 生命周期 | 用户操作 | 启动/停止本地服务 |
| `apps/desktop/src` | React UI、进度、问答、原文对照 | 本地 API | 用户交互 |
| `services/api/app/api` | REST/SSE、token 鉴权 | UI 请求 | JSON/事件流 |
| `services/api/app/ingest` | 扫描、哈希、PDF 解析、OCR、切分 | 文件夹/文件 | 页文本、chunk |
| `services/api/app/index` | 写 SQLite、FTS5、FAISS | chunk/embedding | 可检索索引 |
| `services/api/app/qa` | 混合检索、组装上下文、引用 | 问题 | 回答 + 引用 |
| `services/api/app/llm` | 调用 Ollama、流式输出 | 提示词 | token 流 |
| `services/api/app/jobs` | 队列、状态、重试、断点续跑 | 导入任务 | 进度事件 |
| `services/api/app/config` | 数据目录、模型、参数 | 设置 | 配置对象 |

建议目录：

```text
local-kb/
├─ apps/
│  └─ desktop/                 # Tauri + React
│     ├─ src/                  # React UI
│     └─ src-tauri/            # Rust 壳
├─ services/
│  └─ api/                     # Python FastAPI sidecar
│     ├─ app/
│     │  ├─ api/
│     │  ├─ ingest/
│     │  ├─ index/
│     │  ├─ qa/
│     │  ├─ llm/
│     │  ├─ jobs/
│     │  ├─ db/
│     │  └─ config/
│     └─ tests/
├─ docs/
│  └─ plans/
├─ scripts/
└─ .trellis/                   # 之后由 trellis init 生成
```

## 9. 数据模型

SQLite 为主，FAISS 单独存向量。所有时间使用 ISO 8601 字符串。

### 9.1 `documents`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | UUID |
| `root_path` | TEXT | 用户选择的资料根目录 |
| `path` | TEXT UNIQUE | 文件绝对路径 |
| `file_name` | TEXT | 显示名 |
| `mime` | TEXT | `application/pdf` / `text/markdown` / `text/plain` |
| `size_bytes` | INTEGER | 文件大小 |
| `mtime` | REAL | 文件修改时间 |
| `content_hash` | TEXT | 文件内容哈希，用于去重和增量 |
| `page_count` | INTEGER | 页数/章节数 |
| `status` | TEXT | `pending / processing / ready / failed / skipped / missing` |
| `ocr_confidence` | REAL | 平均 OCR 置信度，可空 |
| `error` | TEXT | 失败原因 |
| `created_at` | TEXT | 创建时间 |
| `updated_at` | TEXT | 更新时间 |
| `indexed_at` | TEXT | 最近索引时间 |

### 9.2 `pages`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `document_id` | TEXT FK | 文档 ID |
| `page_no` | INTEGER | PDF 页码；Markdown/TXT 为章节序号 |
| `text` | TEXT | 提取或 OCR 文本 |
| `is_scanned` | INTEGER | 0/1 |
| `ocr_confidence` | REAL | 页级平均置信度 |
| `low_confidence` | INTEGER | 0/1，低于阈值 |
| `width` | INTEGER | 渲染宽度，可空 |
| `height` | INTEGER | 渲染高度，可空 |
| `created_at` | TEXT | 创建时间 |

唯一约束：`UNIQUE(document_id, page_no)`。

### 9.3 `chunks`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | UUID |
| `document_id` | TEXT FK | 文档 ID |
| `page_no` | INTEGER | 页码/章节 |
| `chunk_index` | INTEGER | 页内序号 |
| `text` | TEXT | 原文片段 |
| `token_count` | INTEGER | 估算 token 数 |
| `start_offset` | INTEGER | 页内起始偏移 |
| `end_offset` | INTEGER | 页内结束偏移 |
| `embedding_id` | INTEGER | FAISS 行号 |
| `created_at` | TEXT | 创建时间 |

唯一约束：`UNIQUE(document_id, page_no, chunk_index)`。

### 9.4 `chunks_fts`

FTS5 虚表，用于关键词检索。

- 中文采用 **jieba 预分词 + `unicode61`**，避免 trigram 对 2 字中文词不命中的问题
- 英文由 `unicode61` 直接处理
- 存储分词后的 `text_tokens`，原文仍保留在 `chunks.text`

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  chunk_id UNINDEXED,
  text_tokens,
  tokenize = 'unicode61'
);
```

### 9.5 任务与会话

| 表 | 关键字段 | 说明 |
|---|---|---|
| `jobs` | `id`、`type`、`status`、`progress`、`total`、`processed`、`current_file`、`error`、`payload_json` | 导入/重建任务 |
| `chats` | `id`、`title`、`created_at`、`updated_at` | 会话 |
| `messages` | `id`、`chat_id`、`role`、`content`、`citations_json`、`model`、`latency_ms` | 问答消息 |
| `settings` | `key`、`value` | 配置 |
| `index_meta` | `key`、`value` | embedding 模型、维度、索引版本、最近重建时间 |

### 9.6 引用格式

```json
[
  {
    "document_id": "doc_xxx",
    "file_name": "2024年度报告.pdf",
    "page_no": 12,
    "chunk_id": "chunk_xxx",
    "quote": "原文中的证据片段"
  }
]
```

展示规则：

- PDF：`[2024年度报告.pdf p.12]`
- Markdown/TXT：`[课程笔记.md §3]`

## 10. API 设计

统一前缀 `/api/v1`，只监听 `127.0.0.1`。Tauri 启动 sidecar 时生成一次性 token，UI 通过 Tauri command 获取；所有请求带 `X-App-Token`。

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | sidecar、Ollama、索引状态 |
| `POST` | `/scan` | 扫描文件夹，创建导入任务 |
| `GET` | `/jobs` | 任务列表 |
| `POST` | `/jobs/{id}/pause` | 暂停 |
| `POST` | `/jobs/{id}/resume` | 继续 |
| `POST` | `/jobs/{id}/cancel` | 取消 |
| `GET` | `/events` | SSE：导入进度、索引状态、模型状态 |
| `GET` | `/documents` | 资料列表，支持状态/目录/关键词过滤 |
| `GET` | `/documents/{id}` | 文档详情 |
| `GET` | `/documents/{id}/pages/{page}` | 页文本 |
| `GET` | `/documents/{id}/pages/{page}/image` | 页图，按需渲染 |
| `POST` | `/documents/{id}/reindex` | 单文档重建索引（不重新 OCR） |
| `POST` | `/documents/{id}/reimport` | 重新解析/OCR |
| `DELETE` | `/documents/{id}` | 删除文档和索引 |
| `POST` | `/ask` | 提问，SSE 流式返回 |
| `GET` | `/chats` | 会话列表 |
| `GET` | `/chats/{id}` | 会话消息 |
| `DELETE` | `/chats/{id}` | 删除会话 |
| `GET/PATCH` | `/settings` | 读取/修改设置 |
| `POST` | `/index/rebuild` | 全量重建索引 |
| `GET` | `/models/status` | OCR/embedding/LLM 模型状态 |
| `POST` | `/models/download` | 首次下载模型 |
| `GET` | `/stats` | 文档数、页数、chunk 数、索引大小 |

### 10.1 `/ask` 事件流

```text
event: retrieval
data: {"chunks":[{"chunk_id":"...","file_name":"...","page_no":12,"score":0.82}]}

event: token
data: {"text":"根据资料，..."}

event: citations
data: {"citations":[{"document_id":"...","file_name":"...","page_no":12,"quote":"..."}]}

event: done
data: {"message_id":"...","latency_ms":3200,"model":"qwen2.5:7b"}

event: error
data: {"code":"OLLAMA_UNAVAILABLE","message":"..."}
```

### 10.2 错误码

| 错误码 | 含义 |
|---|---|
| `UNAUTHORIZED` | token 缺失/错误 |
| `FILE_NOT_FOUND` | 原文件已移动或删除 |
| `PDF_ENCRYPTED` | PDF 加密/无法解析 |
| `OCR_FAILED` | OCR 失败 |
| `OLLAMA_UNAVAILABLE` | Ollama 未启动或模型缺失 |
| `INDEX_NOT_READY` | 索引尚未建立 |
| `NO_EVIDENCE` | 检索不到足够证据 |
| `INTERNAL_ERROR` | 未分类错误 |

## 11. UI 设计

### 11.1 主布局

```text
┌──────────────┬────────────────────────────────────┐
│ 侧边栏        │ 主内容区                             │
│ 资料库        │                                    │
│ 问答          │  资料库 / 导入进度 / 问答 / 设置      │
│ 设置          │                                    │
├──────────────┴────────────────────────────────────┤
│ 状态栏：sidecar 状态 · Ollama 状态 · 索引规模        │
└───────────────────────────────────────────────────┘
```

### 11.2 页面

1. **首次引导**
   - 选择数据目录
   - 检测/下载模型（OCR、embedding、LLM）
   - 选择资料文件夹
2. **资料库**
   - 列表：文件名、目录、页数、状态、OCR 置信度
   - 筛选：状态、目录、关键词
   - 操作：重新导入、重建索引、删除
   - 详情：页列表、OCR 文本、低置信度标记
3. **导入进度**
   - 总进度、当前文件、成功/失败数量
   - 暂停/继续/取消
   - 失败文件列表与重试
4. **问答**
   - 左侧会话列表，右侧消息流
   - 回答内联引用，右侧引用面板
   - 点击引用 → 原文页图或 Markdown/TXT 文本
   - **“资料中没有找到”**独立状态
5. **设置**
   - 数据目录、模型（4B/7B）、OCR DPI、上下文 chunk 数
   - 离线模式、重建索引、清理缓存
   - Ollama 安装/启动引导

### 11.3 关键状态

- 空资料库
- 导入中 / 索引中
- Ollama 未安装 / 未启动 / 模型缺失
- 低置信度页
- 检索无结果
- 模型下载失败 / 下载中断
- sidecar 崩溃并重启

### 11.4 交互约定

- 回答流式输出，先显示“正在检索”，再显示 token
- 引用默认显示文件名+页码，悬停显示原文片段
- 低置信度引用加黄色标记
- `Ctrl+K` 打开提问/搜索
- `Esc` 关闭右侧原文面板

## 12. 处理流程

### 12.1 导入流程

```text
扫描文件夹
 → 计算文件哈希
 → 跳过未变化文件
 → 判断 PDF 是否文字版
    ├─ 文字版：PyMuPDF 抽取页文本
    └─ 扫描页：渲染 300 DPI → RapidOCR → 页文本 + 置信度
 → 按页/段落切 chunk
 → jieba 分词写 FTS5
 → BGE-M3 批量编码
 → 写入 FAISS
 → 更新文档状态
```

### 12.2 问答流程

```text
用户问题
 → jieba 分词，FTS5 关键词召回 top 20
 → BGE-M3 编码问题，FAISS 向量召回 top 20
 → RRF 融合，取 top 8
 → 组装上下文（页码 + 文件名 + 原文）
 → Ollama 流式回答
 → 解析引用，校验引用页是否在召回结果中
 → 返回回答 + 引用
```

### 12.3 无答案策略

满足以下任一条件，回答“资料中没有找到”：

- 关键词和向量都没有达到最低分阈值
- LLM 判断上下文不足以回答
- 引用校验失败，且没有可用证据

## 13. 错误处理与降级

| 场景 | 处理 |
|---|---|
| 单文件失败 | 记录 `failed` + 错误原因，继续处理其他文件 |
| 加密/损坏 PDF | 标记 `PDF_ENCRYPTED` / `PDF_INVALID`，不阻塞批次 |
| OCR 低置信度 | 标记页，问答引用时提示“OCR 可能不准确” |
| Ollama 未启动 | UI 状态栏提示，问答按钮禁用，给启动指引 |
| 模型缺失 | 引导下载；下载失败可重试/断点续传 |
| 内存不足 | 降低 embedding batch、切换 4B 模型、暂停索引 |
| 原文件被移动 | 文档状态 `missing`，引用页提示文件不可用 |
| sidecar 崩溃 | Tauri 自动重启，最多 3 次，之后显示错误 |
| 索引未完成 | 允许提问但提示“结果可能不完整” |

## 14. 性能与资源

### 14.1 资源策略

- **OCR/索引单 worker 串行**，避免 16GB 内存爆掉
- 问答与索引可并发，但索引时限制问答并发为 1
- embedding 批量编码，batch size 可配置（默认 8）
- LLM 默认 Qwen2.5-7B Q4；内存紧张时降级 4B
- 页图按需渲染，不默认常驻缓存

### 14.2 目标

| 指标 | 目标 |
|---|---|
| 单次问答（模型预热后） | ≤ 10 秒 |
| 首 token | ≤ 3 秒（7B，4060） |
| 导入 | 可断点续跑；不承诺固定吞吐 |
| 索引规模 | 目标 ≤ 200k chunks；超过后切换 IVF/HNSW |
| 内存峰值 | 尽量 ≤ 14GB |

## 15. 安全与隐私

- sidecar 只监听 `127.0.0.1`，随机端口 + 一次性 token
- Tauri CSP 只允许本地连接
- 运行时禁止外部网络请求；无遥测
- 原文件只读，不复制、不改写
- 数据目录默认在用户目录，允许用户自定义
- 删除文档时同时删除页、chunk、FTS、向量
- 日志不记录完整原文，只记录路径、页码、错误码

## 16. 测试策略

| 类型 | 内容 |
|---|---|
| 单元测试 | 切分、jieba 分词、引用解析/校验、RRF、无答案判定、路径安全 |
| 集成测试 | 文字版 PDF、扫描 PDF、Markdown/TXT 导入 → 索引 → 提问 → 引用 |
| 黄金集 | 15～20 个真实问题，记录期望文件/页码，人工评分 |
| 性能测试 | 50～100 份导入、问答延迟、内存峰值 |
| 离线测试 | 断网后除模型下载外全部可用 |
| 打包测试 | Tauri 启动/停止 sidecar、PyInstaller 包、干净 Windows 安装 |
| 安全测试 | 无 token 请求被拒绝、路径穿越、外部网络请求 |

## 17. Trellis 使用方式

Trellis 由用户在项目初始化时执行 `trellis init`，本设计不代替初始化。

### 17.1 `.trellis/spec/`

建议结构：

```text
.trellis/spec/
├─ backend/index.md        # API、DB、任务、错误处理、日志
├─ ingest/index.md         # PDF/OCR/切分、置信度阈值
├─ retrieval/index.md      # 混合检索、引用、无答案策略
├─ frontend/index.md       # UI 状态、组件、流式渲染
└─ guides/index.md         # 跨层、复用、跨平台检查
```

### 17.2 `.trellis/tasks/`

按第 18 节拆任务，每个任务一份 PRD，复杂任务补 `design.md` / `implement.md`。

### 17.3 `.trellis/workspace/`

记录：

- 模型选型与基准（4B vs 7B、OCR 速度）
- OCR 效果样本与失败案例
- 检索阈值调整记录
- 打包踩坑

### 17.4 `.trellis/agents/`

可选角色：

- `ingest-engineer`：扫描/解析/OCR/切分
- `retrieval-engineer`：索引/检索/引用
- `ui-engineer`：Tauri/React
- `qa-reviewer`：黄金集与回归

### 17.5 `trellis channel`

仅在文件集不重叠时并行，例如 UI 与 OCR。共享的 schema/API 文件必须单写者，串行修改。

## 18. 任务拆分

| 编号 | 任务 | 验收 |
|---|---|---|
| T1 | 项目骨架：Tauri + React + FastAPI + sidecar 生命周期 | 应用能启动、健康检查通过、能正常退出 |
| T2 | SQLite schema + 设置 + 任务队列 | 任务可创建/暂停/继续；重启后可恢复 |
| T3 | 文件夹扫描 + 文字版 PDF 提取 | 导入文字版 PDF，页文本可见 |
| T4 | 扫描 PDF OCR + 置信度 | 扫描件可搜索；低置信度页有标记 |
| T5 | 切分 + jieba FTS5 + BGE-M3 + FAISS | 关键词/向量检索能召回正确页 |
| T6 | 混合检索 + 带引用问答 + 无答案 | 黄金问题回答正确，引用可核对 |
| T7 | UI：引导、资料库、进度、问答、原文、设置 | 全流程可在 UI 完成 |
| T8 | 模型下载 + 离线模式 + PyInstaller/Tauri 打包 | 干净 Windows 可安装运行 |
| T9 | 黄金集回归 + 性能调优 + 收尾 | 80%+ 正确引用，问答 ≤10 秒 |

## 19. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Python 3.13 与 OCR 依赖不兼容 | 无法安装 RapidOCR/onnxruntime | 优先 Python 3.12 venv；锁定 wheel 版本 |
| 16GB 内存不足 | OCR/embedding/LLM 互相抢内存 | 单 worker、批量控制、4B 降级、按需加载 |
| 扫描件 OCR 质量差 | 检索和回答不准 | 低置信度标记；后续 A+ 升级解析层 |
| Tauri sidecar 打包复杂 | 安装包启动失败 | T1 先做 sidecar spike；PyInstaller onedir；干净环境测试 |
| Ollama 未安装/未启动 | 无法问答 | 引导安装；状态栏检测；后续可换 llama.cpp |
| FAISS 内存过大 | 索引加载慢/内存爆 | 控制 chunk 数；超过阈值切 IVF/HNSW |
| 中文 2 字词检索不到 | 关键词召回差 | jieba + unicode61，而非 trigram |
| 引用幻觉 | 回答不可信 | 引用必须来自召回 chunk；校验失败则降级 |
| 原文件移动/删除 | 原文打不开 | `missing` 状态；重新扫描 |
| 模型下载失败/中断 | 首次体验差 | 断点续传、校验和、重试 |

## 20. 后续扩展

- PDF 表格/公式结构化（PP-Structure / MinerU）
- Word / PPT / Excel 解析
- reranker（BGE reranker）提升排序
- OCR 纠错界面
- 资料集合/标签/多资料库
- 本地 llama.cpp 自包含 LLM（去掉 Ollama 依赖）
- 导出问答为报告/笔记

## 21. 决策记录

| 编号 | 决策 | 理由 |
|---|---|---|
| ADR-001 | Tauri + React + Python sidecar | 桌面体验 + Python AI 生态 + 后续可替换模型 |
| ADR-002 | SQLite + FAISS，不用 LanceDB | 1000 份规模足够，依赖少，易打包 |
| ADR-003 | RapidOCR，不用 PaddleOCR | Python 3.13 兼容性更好，依赖轻 |
| ADR-004 | jieba + unicode61，不用 trigram | 解决中文 2 字词不命中 |
| ADR-005 | MVP 用 Ollama | 本地模型管理简单；后续可换 llama.cpp |
| ADR-006 | 页级引用，不做表格结构 | 控制 MVP 复杂度，先保证可信 |
| ADR-007 | OCR/索引单 worker | 16GB 内存约束，优先稳定 |

## 22. 待定问题

- Ollama 具体模型标签与量化版本（7B/4B）
- 默认数据目录位置（`%LOCALAPPDATA%` 还是用户自选）
- Markdown/TXT 的章节切分规则
- 低置信度阈值（初定 0.75，需用样本校准）
- 检索最低分阈值（需黄金集校准）
- UI 设计系统（Tailwind/shadcn 或其他）
- 是否在 v1 就支持 Markdown/TXT，还是先 PDF
