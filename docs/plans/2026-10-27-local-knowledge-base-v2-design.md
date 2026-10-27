# 本地知识库 + AI 网关 · 设计文档 v2

- **日期**：2026-10-27
- **状态**：设计变更，待实现
- **取代**：`2026-10-27-local-knowledge-base-design.md`（v1，本地 Ollama/BGE-M3 方案）
- **目标用户**：单人（项目所有者）
- **运行环境**：Windows，Ryzen 7 8845H / 16GB RAM / RTX 4060 Laptop 8GB

---

## 1. v2 变更摘要

v1 是“本地知识库 + 本地 Ollama 问答”。v2 改为：

> **本地知识库 + 本地 AI 网关**：不再依赖 Ollama/本地 LLM；模型来自可配置的外部 API；同时把本软件暴露为 OpenAI 兼容、Claude 兼容和 MCP 服务，供 Trae 等外部客户端连接。

核心变化：

1. **移除 Ollama / 本地 LLM**，改为 Provider 适配器：
   - OpenAI Chat Completions（兼容形式）
   - OpenAI Responses（OpenAI 原生新形式）
   - Anthropic Claude
   - Gemini
   - 通用 HTTP/SSE 适配器（覆盖国产/非标准 API）
2. **模型配置**：地址、密钥、模型；密钥可探测模型列表，用户选择任意可用模型。
3. **入站能力**：
   - OpenAI Chat Completions `/v1/chat/completions`（流式 + 非流式）
   - OpenAI Responses `/v1/responses`（流式 + 非流式）
   - Claude 兼容 `/v1/messages`（流式 + 非流式）
   - MCP 工具（search / get_page / list_documents / ask）
4. **embedding**：默认本地 BGE-M3；可选外部 embedding API；失败回退 FTS 关键词检索。
5. **隐私变化**：文档片段会发送到用户配置的外部 LLM/embedding API；密钥只存本机。

## 2. 目标与边界

### 2.1 目标

- 继续做本地资料库：扫描、OCR、切分、检索、引用。
- 问答生成改由外部 API 完成，用户可自由选择 provider/model。
- Trae 可通过 MCP 使用本软件的检索能力，由 Trae 的模型回答。
- 其他客户端可通过 OpenAI/Claude 兼容接口连接本软件，本软件作为 RAG 网关转发到上游 API。
- 本地 embedding 默认免费、私密、离线；外部 embedding 可选。

### 2.2 非目标

- 不做多用户/团队权限。
- 不做公网暴露；网关默认只监听 `127.0.0.1`。
- 不实现每家国产 API 的原生协议；用 OpenAI-compatible 端点或通用适配器覆盖。
- 不保证所有第三方客户端的所有扩展字段完全兼容；以 OpenAI/Claude 标准字段为准。
- 第一版不做图片多模态问答、工具调用编排（MCP 工具除外）。

## 3. 使用方式

### 3.1 自己的聊天 UI

```text
用户在 Local KB 里提问
 → 本地检索（FTS + 可选向量）
 → 取 top chunks
 → Provider 适配器调用外部 LLM（流式）
 → 回答 + 引用
```

### 3.2 Trae 通过 MCP 使用

```text
Trae（自带模型）
 → MCP 连接 Local KB
 → 调用 kb_search / kb_get_page
 → Trae 的模型基于检索结果回答
```

这条路径**不经过本软件配置的 LLM**，回答由 Trae 的模型生成。

### 3.3 其他客户端通过 OpenAI/Claude 兼容接口
```text
客户端（OpenAI Chat / OpenAI Responses / Claude 格式）
 → Local KB /v1/chat/completions、/v1/responses 或 /v1/messages
 → 本地检索 + RAG 注入（默认）
 → 转发到配置的上游 Provider
 → 按客户端协议返回流式/非流式结果
```

这条路径使用**本软件配置的上游模型**，不是客户端自带模型。

## 4. 总体架构

```text
┌─────────────────────────────────────────────────────────┐
│ 外部客户端                                                │
│  ├─ Trae / MCP 客户端 ── MCP tools ───────────────┐      │
│  ├─ OpenAI Chat 客户端 ── /v1/chat/completions ────┤      │
│  ├─ OpenAI Responses 客户端 ── /v1/responses ──────┤      │
│  └─ Claude 客户端 ── /v1/messages ────────────────┤      │
└──────────────────────────┬──────────────────────────┘      │
                           ▼                                 │
┌─────────────────────────────────────────────────────────┐
│ Local KB Gateway（FastAPI，127.0.0.1）                    │
│  ├─ 入站协议层：OpenAI Chat / OpenAI Responses / Claude / MCP │
│  ├─ 鉴权：本地 gateway token                             │
│  ├─ RAG：检索、上下文注入、引用                          │
│  ├─ Provider 路由：选择 provider/model                    │
│  └─ 流式归一化：SSE / JSON                                │
└──────────────────────────┬──────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│ Provider 适配器                                          │
│  ├─ OpenAI Chat Completions                              │
│  ├─ OpenAI Responses                                     │
│  ├─ Anthropic Claude                                    │
│  ├─ Gemini                                              │
│  └─ Custom HTTP/SSE                                     │
└──────────────────────────┬──────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│ 本地数据                                                 │
│  ├─ SQLite：文档/页/chunk/provider/settings              │
│  ├─ FAISS：向量（本地或外部 embedding）                   │
│  ├─ keyring：API keys                                   │
│  └─ 本地 BGE-M3 模型目录（可选下载）                      │
└─────────────────────────────────────────────────────────┘
```

## 5. Provider 兼容矩阵（A + C）

### 5.1 A：通用协议

| Provider 类型 | 模型探测 | 聊天 | 流式 | Embedding |
|---|---|---|---|---|
| OpenAI Chat Completions | `GET /models` | `POST /chat/completions` | SSE `data:` / `[DONE]` | `POST /embeddings`（可选） |
| OpenAI Responses | `GET /models` | `POST /responses` | `response.output_text.delta` / `response.completed` | 同 Chat Completions（可选） |
| Anthropic Claude | `GET /models`（若可用） | `POST /messages` | `content_block_delta` 等 | 不支持（用本地/其他） |
| Gemini | `GET /models` | `:generateContent` | `:streamGenerateContent?alt=sse` | `:embedContent`（可选） |

> OpenAI Chat Completions 是大多数国产/兼容 API 的形式；OpenAI Responses 是 OpenAI 原生新形式，请求/响应/流式事件都不同。两者都作为独立 Provider 类型。

OpenAI 预设（仅填默认 base URL，可改）：

- OpenAI Chat Completions
- OpenAI Responses
- DeepSeek
- Qwen / DashScope compatible mode
- Moonshot / Kimi
- Zhipu / GLM（OpenAI-compatible 端点）
- MiniMax（OpenAI-compatible 端点）
- SiliconFlow
- OpenRouter
- 自定义

> 国产 API 优先用其 Chat Completions 兼容端点；没有兼容端点的，用 Custom 适配器。

### 5.2 C：Custom HTTP/SSE 适配器

配置字段：

- `method`：POST（默认）
- `url`：完整聊天地址
- `auth`：`bearer` / `x-api-key` / `query` / `none`
- `headers`：自定义 headers（可含占位符）
- `body_template`：JSON 模板，占位符：
  - `{{model}}`
  - `{{messages_json}}`
  - `{{system}}`
  - `{{stream}}`
  - `{{max_tokens}}`
  - `{{temperature}}`
- `response_text_path`：非流式响应中文本路径，如 `choices.0.message.content`
- `stream_text_path`：流式事件中增量文本路径，如 `choices.0.delta.content`
- `stream_done`：结束标记，如 `[DONE]`
- `usage_path`：可选 token 用量路径
- `test_url` / `models_url`：可选模型探测地址

Custom 适配器只做“配置化 HTTP + SSE 解析”，不为每家写死代码。

## 6. 入站协议

### 6.1 OpenAI 兼容

- `GET /v1/models`
- `POST /v1/chat/completions`
  - 非流式：标准 `choices[0].message.content`
  - 流式：`data: {...}` + `[DONE]`
  - 支持字段：`messages`、`model`、`stream`、`temperature`、`max_tokens`
  - 未知字段忽略，不报错
- 鉴权：`Authorization: Bearer <gateway_token>`；也接受 `x-api-key`
- RAG 默认开启：把检索上下文注入 system prompt；响应附加非标准 `kb_citations`（客户端可忽略）

### 6.2 OpenAI Responses

- `POST /v1/responses`
  - 非流式：标准 `object: "response"`、`output[]`、`output_text`
  - 流式：SSE 事件 `response.created` / `response.output_item.added` / `response.content_part.added` / `response.output_text.delta` / `response.output_text.done` / `response.content_part.done` / `response.output_item.done` / `response.completed` / `response.failed`
  - 支持字段：`model`、`input`、`instructions`、`stream`、`temperature`、`max_output_tokens`
  - 未知字段忽略，不报错
- 鉴权：`Authorization: Bearer <gateway_token>`；也接受 `x-api-key`
- RAG 默认开启：把检索上下文注入 `instructions` 或 input 的首个 system 消息；响应附加非标准 `kb_citations`

### 6.3 Claude 兼容

- `POST /v1/messages`
  - 非流式：标准 `content[]`
  - 流式：`message_start` / `content_block_start` / `content_block_delta` / `content_block_stop` / `message_delta` / `message_stop`
  - 支持字段：`messages`、`system`、`model`、`max_tokens`、`stream`
- 鉴权：`x-api-key` 或 `Authorization: Bearer <gateway_token>`
- RAG 默认开启：把检索上下文追加到 `system`；引用放入 `metadata.kb_citations`

### 6.4 MCP

- 传输：
  - HTTP/SSE：`http://127.0.0.1:<gateway_port>/mcp`
  - 可选 stdio：`python -m app.mcp`
- 工具：
  - `kb_search(query, top_k=8)`：返回 chunk 列表、文件名、页码、引用
  - `kb_get_page(document_id, page_no)`：返回页文本 + 可选页图 base64
  - `kb_list_documents(query?, limit=20)`：返回资料列表
  - `kb_ask(question)`：可选，使用本软件配置的上游模型生成回答
- Trae 使用 `kb_search` / `kb_get_page`，由 Trae 的模型完成回答。

### 6.5 RAG 策略

- `auto`（默认）：入站 OpenAI Chat Completions / OpenAI Responses / Claude 请求自动检索并注入上下文
- `off`：纯代理，不注入知识库
- `tools`：入站聊天不注入，客户端通过 MCP 工具自行检索
- 上下文上限：默认 8 个 chunk，可配置；超出截断
- 引用：默认只引用召回 chunk 对应的文件/页码；不做句子级事实校验

## 7. 数据模型

### 7.1 `providers`

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | UUID |
| `name` | TEXT | 显示名 |
| `kind` | TEXT | `openai_chat` / `openai_responses` / `anthropic` / `gemini` / `custom` |
| `base_url` | TEXT | API 根地址 |
| `model` | TEXT | 默认模型 |
| `enabled` | INTEGER | 0/1 |
| `key_ref` | TEXT | keyring 引用，不存明文 |
| `extra_json` | TEXT | custom 适配器/自定义 headers 等 |
| `created_at` | TEXT | |
| `updated_at` | TEXT | |

### 7.2 设置键

| key | 说明 |
|---|---|
| `embedding_mode` | `local` / `external` / `auto` / `fts` |
| `embedding_provider_id` | 外部 embedding provider |
| `embedding_model` | 外部 embedding 模型 |
| `gateway_enabled` | 入站网关开关 |
| `gateway_port` | 入站端口 |
| `gateway_rag_mode` | `auto` / `off` / `tools` |
| `gateway_token_ref` | keyring 中的网关 token |
| `mcp_enabled` | MCP 开关 |
| `context_chunks` | RAG top-k |
| `request_timeout` | 上游超时秒数 |

### 7.3 `model_cache`

| 字段 | 说明 |
|---|---|
| `provider_id` | provider |
| `models_json` | 探测到的模型列表 |
| `fetched_at` | 最近探测时间 |

密钥：`keyring`（Windows Credential Manager），service `local-kb`，username `provider:<id>` / `gateway-token`。API 永不返回明文密钥，只返回 `has_key`。

## 8. 后端 API

### 8.1 管理 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/v1/providers` | provider 列表（不含密钥） |
| `POST` | `/api/v1/providers` | 新建 provider |
| `PATCH` | `/api/v1/providers/{id}` | 修改 provider |
| `DELETE` | `/api/v1/providers/{id}` | 删除 provider 和 keyring 条目 |
| `PUT` | `/api/v1/providers/{id}/key` | 写入/更新密钥 |
| `POST` | `/api/v1/providers/{id}/models` | 用密钥探测模型列表 |
| `POST` | `/api/v1/providers/{id}/test` | 测试连接/发一条短消息 |
| `GET` | `/api/v1/embedding/config` | embedding 配置 |
| `PATCH` | `/api/v1/embedding/config` | 修改 embedding 模式/provider |
| `POST` | `/api/v1/embedding/test` | 测试 embedding |
| `GET` | `/api/v1/gateway/config` | 入站网关配置 |
| `PATCH` | `/api/v1/gateway/config` | 修改端口/RAG/MCP |
| `POST` | `/api/v1/gateway/token/rotate` | 轮换本地网关 token |

### 8.2 入站 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/v1/models` | OpenAI 格式模型列表 |
| `POST` | `/v1/chat/completions` | OpenAI Chat Completions（流式/非流式） |
| `POST` | `/v1/responses` | OpenAI Responses（流式/非流式） |
| `POST` | `/v1/messages` | Claude 兼容聊天（流式/非流式） |
| `GET` | `/mcp` / `/mcp/sse` | MCP 传输端点 |

### 8.3 内部 Provider 接口

```text
Provider
  ├─ list_models() -> ModelInfo[]
  ├─ chat(request) -> ChatChunk stream | ChatResponse
  ├─ embed(texts) -> vectors[]（可选）
  └─ test() -> ok/error
```

统一内部事件：

```text
ChatChunk = { type: "text" | "usage" | "done" | "error", ... }
```

所有外部 provider 的流式格式都归一到这个事件，再由入站协议层转换为 OpenAI/Claude SSE。

## 9. UI

### 9.1 设置页

1. **Provider 管理**
   - 列表：名称、类型、地址、默认模型、是否启用、`has_key`
   - 类型：OpenAI Chat Completions / OpenAI Responses / Anthropic / Gemini / Custom
   - 新建/编辑：类型、base URL、API key、模型探测、模型下拉、测试连接
   - Custom 适配器：headers、body 模板、响应路径、SSE 路径
2. **Embedding**
   - 模式：本地 BGE-M3 / 外部 API / 自动回退 / 仅 FTS
   - 外部 embedding provider/model
   - 本地模型下载状态、路径
3. **网关**
   - 开关、端口、RAG 模式、MCP 开关
   - 本地 gateway token 显示/轮换/复制
   - 一键复制 Trae MCP 配置、OpenAI Chat Completions、OpenAI Responses、Claude 客户端配置片段

### 9.2 引导页

- 选择/添加 provider
- 填密钥 → 探测模型 → 选模型 → 测试
- 选择 embedding 模式（默认本地 BGE-M3）
- 选择资料文件夹开始导入

### 9.3 问答页

- 显示当前 provider/model
- 流式回答
- 引用面板 + 原文页图
- 错误：未配置 provider、密钥无效、模型不可用、上游超时、限流
- RAG 关闭时明确提示“当前是纯模型回答，未使用知识库”

## 10. 安全与隐私

- API keys 存 OS keyring，不写 SQLite 明文、不进日志、不进 git。
- 入站网关只监听 `127.0.0.1`，默认关闭；开启后需要 gateway token。
- Tauri CSP 只允许本地连接和必要协议。
- 文档片段会发送到外部 Provider；UI 明确提示。
- 日志脱敏：不记录 Authorization、API key、完整文档内容。
- 本地 embedding 默认不把文档向量发出去；只有检索到的片段发给 LLM。
- 通用 Custom 适配器允许自定义 headers，但禁止自动加载任意外部脚本。

## 11. 错误处理

| 错误 | 处理 |
|---|---|
| 未配置 provider | 问答页引导去设置 |
| 密钥无效 401/403 | 提示重新填写密钥 |
| 模型不存在 | 重新探测模型/选择 |
| 上游限流 429 | 退避重试 1 次，之后提示 |
| 上游超时 | 可配置超时；SSE 发送 error |
| 流式中断 | 返回已生成部分 + 错误标记 |
| embedding 失败 | 回退 FTS，记录一次警告 |
| MCP 工具参数错误 | 返回结构化错误，不崩溃 |
| 入站客户端字段不兼容 | 忽略未知字段，保留标准字段 |
| keyring 不可用 | 提示安装/启用系统凭据存储；不回退明文存储 |

## 12. 测试

- Provider 适配器：用本地 mock HTTP/SSE 服务，覆盖 OpenAI Chat Completions、OpenAI Responses、Claude、Gemini、Custom 的流式与非流式。
- 模型探测：mock `/models`，覆盖成功、401、超时、空列表。
- 入站协议：OpenAI Chat Completions、OpenAI Responses、Claude 标准请求、流式事件顺序、错误格式、鉴权。
- MCP：工具 schema、参数校验、返回结构、无结果。
- Embedding：本地 fake embedder、外部 mock、失败回退 FTS。
- 安全：密钥不出现在 API/日志；无 token 入站请求 401。
- 端到端：mock provider + 临时资料，导入 → 提问 → 引用。
- 回归：现有 41 个后端测试 + 前端构建/冒烟测试。

## 13. 任务拆分（v2 delta）

| 编号 | 任务 | 验收 |
|---|---|---|
| V2-1 | Provider 数据模型、keyring、CRUD API | 新建/编辑/删除 provider；密钥不出明文 |
| V2-2 | OpenAI Chat Completions 适配器 + 模型探测 + 流式 | mock provider 流式/非流式通过 |
| V2-2b | OpenAI Responses 适配器 + 流式事件 | `/v1/responses` mock 流式/非流式通过 |
| V2-3 | Anthropic Claude 适配器 | `/v1/messages` + 流式事件 mock 通过 |
| V2-4 | Gemini 适配器 | generateContent/stream + 模型列表 mock 通过 |
| V2-5 | Custom HTTP/SSE 适配器 + 中国 API 预设 | 配置化请求/SSE 解析通过 |
| V2-6 | Embedding 抽象：本地 BGE-M3 / 外部 / FTS 回退 | 三种模式切换与失败回退通过 |
| V2-7 | 入站 OpenAI Chat Completions `/v1/chat/completions` + RAG | 流式/非流式/鉴权通过 |
| V2-7b | 入站 OpenAI Responses `/v1/responses` + RAG | Responses 流式事件/非流式/鉴权通过 |
| V2-8 | 入站 Claude `/v1/messages` + RAG | 流式/非流式/鉴权通过 |
| V2-9 | MCP 服务与工具 | `kb_search`/`get_page` 可调用 |
| V2-10 | Provider/网关设置 UI + 配置片段 | 可在 UI 完成配置并复制 Trae 配置 |
| V2-11 | 移除 Ollama/本地 LLM 路径；文档和打包更新 | 无 Ollama 依赖；构建通过 |
| V2-12 | 黄金集 + mock provider 端到端 + 性能 | 问答/引用正确，无真实密钥依赖 |

## 14. 迁移与废弃

- 删除 `services/api/app/llm/ollama.py` 及 Ollama 状态/下载逻辑。
- `models/status` 改为 provider/embedding 状态，不再探测 Ollama。
- 保留本地 BGE-M3 embedding；`sentence-transformers`/torch 仍需要。
- 新增依赖：`keyring`、`mcp`（MCP Python SDK）。
- 数据库迁移：新增 `providers`、`model_cache`；settings 增加网关/embedding 键。
- 前端：Onboarding/Settings 移除 Ollama，加入 provider/网关配置。
- 现有 T1-T8 的扫描/OCR/FTS/FAISS/UI 代码继续复用。

## 15. 风险

| 风险 | 缓解 |
|---|---|
| 各家 API 字段/流式格式差异 | 统一内部事件 + mock 测试 + Custom 适配器 |
| 入站协议兼容性不足 | 只保证标准字段；未知字段忽略；提供配置片段 |
| RAG 注入增加 token 成本 | 限制 chunk 数/字符数；可关闭 RAG |
| 密钥泄露 | OS keyring；日志脱敏；API 不返回明文 |
| 入站网关被本机其他程序滥用 | 默认关闭；token 鉴权；只监听 127.0.0.1 |
| 本地 embedding 包体大 | 模型首次下载；可选外部 embedding/FTS |
| Trae MCP 配置差异 | 提供 HTTP/SSE 与 stdio 两种配置，并给出示例 |
| 上游限流/超时 | 退避、超时、错误提示；流式部分结果保留 |

## 16. 验收标准

1. 能配置 OpenAI Chat Completions 和 OpenAI Responses 两种 provider，探测模型、选择模型、测试连接。
2. 能配置 Claude 和 Gemini provider，流式/非流式可用。
3. 能用 Custom 适配器接入一个非标准 HTTP/SSE API。
4. 本软件聊天 UI 能用配置的 provider 回答，并带引用。
5. OpenAI Chat Completions、OpenAI Responses、Claude 兼容入站接口可被标准客户端调用，流式/非流式正常。
6. Trae 通过 MCP 调用 `kb_search`，由 Trae 的模型完成回答。
7. 本地 BGE-M3 默认可用；外部 embedding 可切换；失败自动回退 FTS。
8. 密钥不出现在 API 响应/日志/git；入站无 token 返回 401。
9. 不再依赖 Ollama；现有扫描/OCR/索引测试保持通过。
10. 用 mock provider 跑通 10 个黄金问题，引用正确。

## 17. 决策记录

| 编号 | 决策 | 理由 |
|---|---|---|
| V2-ADR-001 | 入站同时支持 OpenAI/Claude/MCP | 覆盖 Trae 和其他客户端 |
| V2-ADR-002 | 出站 A + C | 常见协议开箱即用，非标准 API 用 Custom 兜底 |
| V2-ADR-003 | embedding C：本地默认 + 外部可选 + FTS 回退 | 零费用/隐私/离线，同时保留灵活性 |
| V2-ADR-004 | 入站 RAG 默认 auto | 让“把本软件当模型提供方”的客户端也能用知识库 |
| V2-ADR-005 | 密钥用 keyring | 不存明文，符合本地安全预期 |
| V2-ADR-006 | 移除 Ollama/本地 LLM | 用户选择外部 API，降低显存/安装/维护成本 |
| V2-ADR-007 | OpenAI Responses 出站 + 入站都支持 | 覆盖 OpenAI 原生新协议，同时保留 Chat Completions 兼容性 |
