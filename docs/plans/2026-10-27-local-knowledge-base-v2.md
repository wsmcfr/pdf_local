# 本地知识库 + AI 网关 v2 Implementation Plan

> **For Codex/Trae:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 把已完成的本地知识库改造成“本地知识库 + AI 网关”：不再依赖 Ollama/本地 LLM；支持外部 OpenAI Chat Completions、OpenAI Responses、Claude、Gemini、Custom API；同时暴露 OpenAI Chat Completions、OpenAI Responses、Claude 兼容接口和 MCP 工具，供 Trae 等客户端连接。

**Architecture:** FastAPI sidecar 内部增加 Provider 适配层和入站协议层；检索/OCR/FTS/FAISS 复用现有实现；embedding 默认本地 BGE-M3，可选外部 API，失败回退 FTS；API 密钥存 OS keyring。

**Tech Stack:** Python 3.11、FastAPI、httpx、SQLite、FAISS、sentence-transformers、keyring、mcp、Tauri + React。

**Baseline:** Task 1-8 已完成（commit `140d65a`）。本计划是 v2 delta，不重写扫描/OCR/FTS/FAISS/UI 基础。Ollama 相关代码将被移除。

---

## Prerequisites

```powershell
cd f:\xiangfa
git checkout feat/local-kb
.\.venv\Scripts\Activate.ps1
python -m pip install keyring mcp
```

- 不安装 Ollama。
- 不下载 BGE-M3（本计划测试用 fake embedding；真实模型后续手动验收）。
- 不提交密钥、模型、构建产物。
- 每个 Task 先写失败测试，再实现，再跑测试；不 commit。

---

## Task V2-1: Provider 数据模型、密钥存储与 CRUD

**Files:**
- Modify: `services/api/app/schema.sql`
- Create: `services/api/app/providers/__init__.py`
- Create: `services/api/app/providers/base.py`
- Create: `services/api/app/providers/secret_store.py`
- Create: `services/api/app/api/providers.py`
- Create: `services/api/tests/test_providers.py`
- Modify: `services/api/app/main.py`

**Step 1: 写失败测试**

`services/api/tests/test_providers.py`:

```python
def test_create_provider_hides_key(client, monkeypatch):
    monkeypatch.setattr("app.providers.secret_store.set_secret", lambda pid, key: None)
    response = client.post(
        "/api/v1/providers",
        headers=HEADERS,
        json={
            "name": "DeepSeek",
            "kind": "openai_chat",
            "base_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
            "api_key": "sk-test",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert "api_key" not in body
    assert body["has_key"] is True
```

**Step 2: Schema**

在 `schema.sql` 末尾加：

```sql
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  base_url TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  enabled INTEGER NOT NULL DEFAULT 1,
  key_ref TEXT,
  extra_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_cache (
  provider_id TEXT NOT NULL REFERENCES providers(id) ON DELETE CASCADE,
  models_json TEXT NOT NULL DEFAULT '[]',
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (provider_id)
);

PRAGMA user_version = 2;
```

**Step 3: 密钥存储**

`secret_store.py` 使用 `keyring`：

```python
import keyring

SERVICE = "local-kb"

def set_secret(name: str, value: str) -> None:
    keyring.set_password(SERVICE, name, value)

def get_secret(name: str) -> str | None:
    return keyring.get_password(SERVICE, name)

def delete_secret(name: str) -> None:
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        pass

def provider_key_name(provider_id: str) -> str:
    return f"provider:{provider_id}"
```

**Step 4: Provider 基础类型**

`providers/base.py`:

```python
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Protocol

@dataclass
class ChatMessage:
    role: str
    content: str

@dataclass
class ChatRequest:
    model: str
    messages: list[ChatMessage]
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    extra: dict = field(default_factory=dict)

@dataclass
class ChatChunk:
    type: Literal["text", "usage", "done", "error"]
    text: str = ""
    usage: dict | None = None
    error: str | None = None

@dataclass
class ModelInfo:
    id: str
    name: str | None = None

class Provider(Protocol):
    async def list_models(self) -> list[ModelInfo]: ...
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...
    async def embed(self, texts: list[str]) -> list[list[float]] | None: ...
```

**Step 5: CRUD API**

`api/providers.py` 提供：

- `GET /providers`：返回列表，包含 `has_key`，不含 key
- `POST /providers`：创建，`api_key` 写 keyring
- `PATCH /providers/{id}`
- `DELETE /providers/{id}`
- `PUT /providers/{id}/key`
- `POST /providers/{id}/models`：调用适配器探测（V2-2 接入）
- `POST /providers/{id}/test`

`main.py` 挂载 `providers_router`，保留鉴权。

**Step 6: 测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_providers.py -v
```

**Step 7: Commit**

```powershell
git add services/api/app/schema.sql services/api/app/providers services/api/app/api/providers.py services/api/app/main.py services/api/tests/test_providers.py
git commit -m "feat: add provider config and keyring storage"
```

---

## Task V2-2: OpenAI Chat Completions 适配器

**Files:**
- Create: `services/api/app/providers/openai_compat.py`
- Create: `services/api/app/providers/factory.py`
- Create: `services/api/tests/test_provider_openai.py`

**Step 1: 写失败测试**

用 `httpx.MockTransport` 模拟：

- `GET /models` 返回 `data: [{id: "deepseek-chat"}]`
- `POST /chat/completions` 非流式返回标准 choices
- 流式返回 `data: {...}` + `[DONE]`
- 401 抛出 `ProviderAuthError`

**Step 2: 实现**

```python
class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str, model: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model

    async def list_models(self) -> list[ModelInfo]:
        # GET {base_url}/models, Bearer
        ...

    async def stream_chat(self, request: ChatRequest):
        # POST {base_url}/chat/completions, stream=request.stream
        # 非流式：yield text + done
        # 流式：解析 data: JSON / [DONE]
        # 提取 choices[0].delta.content 或 choices[0].message.content
        ...

    async def embed(self, texts):
        # POST {base_url}/embeddings
        # 返回 data[].embedding；失败返回 None
        ...
```

错误映射：

- 401/403 → `ProviderAuthError`
- 404 → `ProviderNotFoundError`
- 429 → `ProviderRateLimitError`
- timeout → `ProviderTimeoutError`

**Step 3: 工厂**

`factory.py` 按 `kind` 返回 provider 实例；V2-2 先支持 `openai_chat`，后续 Task 补 `openai_responses`/`anthropic`/`gemini`/`custom`。

**Step 4: 测试**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests/test_provider_openai.py -v
```

**Step 5: Commit**

```powershell
git add services/api/app/providers services/api/tests/test_provider_openai.py
git commit -m "feat: add openai chat completions provider adapter"
```

---

## Task V2-2b: OpenAI Responses 适配器

**Files:**
- Create: `services/api/app/providers/openai_responses.py`
- Create: `services/api/tests/test_provider_openai_responses.py`
- Modify: `services/api/app/providers/factory.py`

**要点：**

- `POST {base_url}/responses`
- 请求：
  - `model`
  - `input`：字符串或 messages 数组
  - `instructions`：system 内容
  - `stream`
  - `max_output_tokens`
  - `temperature`
- 非流式：读取 `output[].content[].text`，兼容 `output_text`
- 流式事件：
  - `response.output_text.delta` → 文本增量
  - `response.completed` → done + usage
  - `response.failed` → error
  - 其他事件忽略
- 模型探测：复用 OpenAI `GET /models`
- Embedding：可复用 `POST /embeddings`，失败返回 `None`
- 错误映射与 V2-2 一致

**测试：**

- mock 非流式 `output`
- mock 流式 `response.output_text.delta` + `response.completed`
- mock `response.failed`
- 401 错误

**Commit:**

```powershell
git add services/api/app/providers services/api/tests/test_provider_openai_responses.py
git commit -m "feat: add openai responses provider adapter"
```

---

## Task V2-3: Anthropic Claude 适配器

**Files:**
- Create: `services/api/app/providers/anthropic.py`
- Create: `services/api/tests/test_provider_anthropic.py`
- Modify: `services/api/app/providers/factory.py`

**要点：**

- Headers：`x-api-key`、`anthropic-version: 2023-06-01`
- `POST {base_url}/messages`
- 非流式：`content[0].text`
- 流式：解析 `content_block_delta` 的 `delta.text`，`message_stop` 结束
- `max_tokens` 必填，默认 1024
- 不支持 embedding：`embed()` 返回 `None`
- 模型探测：`GET /models`，失败时返回空列表，由 UI 手动填模型

**测试：** mock 非流式 + 流式事件序列 + 401。

**Commit:**

```powershell
git add services/api/app/providers services/api/tests/test_provider_anthropic.py
git commit -m "feat: add anthropic provider adapter"
```

---

## Task V2-4: Gemini 适配器

**Files:**
- Create: `services/api/app/providers/gemini.py`
- Create: `services/api/tests/test_provider_gemini.py`
- Modify: `services/api/app/providers/factory.py`

**要点：**

- `GET {base_url}/models?key=...` 或 `x-goog-api-key`
- 非流式：`POST {base_url}/models/{model}:generateContent`
- 流式：`POST {base_url}/models/{model}:streamGenerateContent?alt=sse`
- 文本路径：`candidates[0].content.parts[].text`
- `embed()`：`POST {base_url}/models/{model}:embedContent`
- 错误映射同 OpenAI

**测试：** mock 模型列表、非流式、SSE、embedding。

**Commit:**

```powershell
git add services/api/app/providers services/api/tests/test_provider_gemini.py
git commit -m "feat: add gemini provider adapter"
```

---

## Task V2-5: Custom HTTP/SSE 适配器与国产 API 预设

**Files:**
- Create: `services/api/app/providers/custom.py`
- Create: `services/api/app/providers/presets.py`
- Create: `services/api/tests/test_provider_custom.py`
- Modify: `services/api/app/providers/factory.py`

**Custom 配置（存 `extra_json`）：**

```json
{
  "auth": "bearer",
  "headers": {"X-Custom": "{{api_key}}"},
  "body_template": {
    "model": "{{model}}",
    "messages": "{{messages_json}}",
    "stream": "{{stream}}"
  },
  "response_text_path": "data.answer",
  "stream_text_path": "delta.content",
  "stream_done": "[DONE]",
  "models_url": "https://example.com/models"
}
```

实现：

- `_render(value, context)` 递归替换 `{{model}}` / `{{messages_json}}` / `{{api_key}}` / `{{stream}}`
- `_get_path(data, "a.b.0.c")` 支持点号路径和数组下标
- SSE：按 `data:` 行解析，结束标记可配置
- 模型探测：有 `models_url` 才调用；失败返回空列表
- `presets.py` 只放常用 base URL/kind，不写死协议：
  - OpenAI Chat Completions、OpenAI Responses、DeepSeek、Qwen、Kimi、GLM、MiniMax、SiliconFlow、OpenRouter

**测试：** mock 自定义 JSON 响应 + SSE + 鉴权方式 `bearer`/`x-api-key`/`query`。

**Commit:**

```powershell
git add services/api/app/providers services/api/tests/test_provider_custom.py
git commit -m "feat: add custom http/sse provider adapter and presets"
```

---

## Task V2-6: Embedding 抽象、外部 embedding 与 FTS 回退

**Files:**
- Modify: `services/api/app/index/embeddings.py`
- Create: `services/api/app/index/embedding_service.py`
- Modify: `services/api/app/index/indexer.py`
- Modify: `services/api/app/qa/retriever.py`
- Modify: `services/api/app/jobs/runner.py`
- Modify: `services/api/app/main.py`
- Create: `services/api/app/api/embeddings.py`
- Create: `services/api/tests/test_embedding_service.py`

**要点：**

- `EmbeddingService` 模式：
  - `local`：BGE-M3（现有 `encode`）
  - `external`：调用 provider.embed
  - `auto`：本地可用则本地，否则外部，仍失败则 `None`
  - `fts`：不生成向量
- `index_document(..., embed_fn=None)`：
  - `embed_fn=None` 时只写 FTS，不写向量
  - `embed_fn` 抛错时记录警告，文档仍 `ready`，检索退化为 FTS
- `HybridRetriever`：
  - 无向量索引或 embed 失败时只用 FTS
  - 不再抛 500
- `GET/PATCH /embedding/config`、`POST /embedding/test`
- settings：`embedding_mode`、`embedding_provider_id`、`embedding_model`

**测试：**

- fake local embedder
- mock external embedder
- external 失败 → FTS 仍能召回
- `fts` 模式 chunks 无 embedding_id，但 FTS 可查

**Commit:**

```powershell
git add services/api/app/index services/api/app/qa/retriever.py services/api/app/jobs/runner.py services/api/app/main.py services/api/app/api/embeddings.py services/api/tests/test_embedding_service.py
git commit -m "feat: support local, external and fts embedding modes"
```

---

## Task V2-7: 入站 OpenAI Chat Completions API + RAG

**Files:**
- Create: `services/api/app/api/inbound_openai.py`
- Create: `services/api/app/qa/rag.py`
- Create: `services/api/tests/test_inbound_openai.py`
- Modify: `services/api/app/main.py`

**要点：**

- `GET /v1/models`：返回当前 provider 的模型缓存；没有则探测
- `POST /v1/chat/completions`
  - 鉴权：`Authorization: Bearer <gateway_token>` 或 `x-api-key`
  - 非流式：标准响应 + 可选 `kb_citations`
  - 流式：标准 `data: {...}` + `[DONE]`
  - `model` 字段：优先用请求值；不在可用列表时用 provider 默认模型
  - RAG：`gateway_rag_mode=auto` 时检索最后一个 user message，注入 system prompt
  - `gateway_rag_mode=off` 时纯代理
- `qa/rag.py`：`retrieve_context(question, top_k)` + `inject_context(messages, chunks)`
- 错误：
  - 无 token 401
  - 网关关闭 403
  - provider 未配置 400
  - 上游错误按 OpenAI error 格式返回

**测试：**

- 非流式 mock provider
- 流式 mock provider，事件顺序
- RAG 注入后 system 包含引用
- RAG off 不注入
- 无 token 401

**Commit:**

```powershell
git add services/api/app/api/inbound_openai.py services/api/app/qa/rag.py services/api/app/main.py services/api/tests/test_inbound_openai.py
git commit -m "feat: add openai chat completions inbound api with rag"
```

---

## Task V2-7b: 入站 OpenAI Responses API + RAG

**Files:**
- Create: `services/api/app/api/inbound_responses.py`
- Create: `services/api/tests/test_inbound_responses.py`
- Modify: `services/api/app/main.py`

**要点：**

- `POST /v1/responses`
- 鉴权：`Authorization: Bearer <gateway_token>` 或 `x-api-key`
- 请求字段：`model`、`input`（字符串或 messages）、`instructions`、`stream`、`temperature`、`max_output_tokens`
- 非流式响应：
  - `id`、`object: "response"`、`created_at`、`status: "completed"`、`model`
  - `output: [{type:"message", role:"assistant", content:[{type:"output_text", text:"..."}]}]`
  - `output_text`（便利字段）
  - `usage`
  - 非标准 `kb_citations`
- 流式事件（SSE `event:` + `data:`）：
  - `response.created`
  - `response.output_item.added`
  - `response.content_part.added`
  - `response.output_text.delta`
  - `response.output_text.done`
  - `response.content_part.done`
  - `response.output_item.done`
  - `response.completed`
  - `response.failed`
- RAG：`gateway_rag_mode=auto` 时把检索上下文注入 `instructions`；`off` 时纯代理
- 错误：按 OpenAI Responses `{error:{type,message}}` 格式返回

**测试：**

- 非流式 mock provider，检查 `output` / `output_text`
- 流式 mock provider，检查事件名和顺序
- RAG 注入后 `instructions` 包含引用
- 无 token 401

**Commit:**

```powershell
git add services/api/app/api/inbound_responses.py services/api/app/main.py services/api/tests/test_inbound_responses.py
git commit -m "feat: add openai responses inbound api with rag"
```

---

## Task V2-8: 入站 Claude 兼容 API + RAG

**Files:**
- Create: `services/api/app/api/inbound_claude.py`
- Create: `services/api/tests/test_inbound_claude.py`
- Modify: `services/api/app/main.py`

**要点：**

- `POST /v1/messages`
- 鉴权：`x-api-key` 或 Bearer gateway token
- 非流式：`content: [{type:"text", text:"..."}]` + `metadata.kb_citations`
- 流式：
  - `message_start`
  - `content_block_start`
  - `content_block_delta`
  - `content_block_stop`
  - `message_delta`
  - `message_stop`
- RAG 同 OpenAI；`system` 字符串或 content block 都要兼容
- 错误按 Claude `{type:"error", error:{type,message}}` 格式

**测试：** 非流式、流式事件顺序、RAG 注入、401。

**Commit:**

```powershell
git add services/api/app/api/inbound_claude.py services/api/app/main.py services/api/tests/test_inbound_claude.py
git commit -m "feat: add claude-compatible inbound messages api"
```

---

## Task V2-9: MCP 服务与工具

**Files:**
- Create: `services/api/app/mcp_server.py`
- Create: `services/api/app/mcp_stdio.py`
- Create: `services/api/tests/test_mcp_tools.py`
- Modify: `services/api/app/main.py`
- Modify: `requirements.txt`

**工具：**

- `kb_search(query, top_k=8)` → `{results:[{document_id,file_name,page_no,text,score}]}`
- `kb_get_page(document_id, page_no)` → `{text, is_scanned, ocr_confidence, image_base64?}`
- `kb_list_documents(query?, limit=20)` → `{documents:[...]}`
- `kb_ask(question)` → 使用当前 provider 生成回答 + citations

**要点：**

- 使用 `mcp.server.fastmcp.FastMCP`
- HTTP/SSE 挂载 `/mcp`；stdio 入口 `python -m app.mcp_stdio`
- 只监听本地；HTTP 模式需要 gateway token header/query
- 工具参数用 Pydantic/JSON Schema 描述
- 无结果返回空数组，不报错

**测试：** 直接调用工具函数 + 参数校验 + 无结果。

**Commit:**

```powershell
git add services/api/app/mcp_server.py services/api/app/mcp_stdio.py services/api/app/main.py services/api/tests/test_mcp_tools.py requirements.txt
git commit -m "feat: expose knowledge base tools over mcp"
```

---

## Task V2-10: Provider/网关设置 UI

**Files:**
- Create: `apps/desktop/src/pages/ProviderSettings.tsx`
- Create: `apps/desktop/src/pages/GatewaySettings.tsx`
- Create: `apps/desktop/src/api/providers.ts`
- Modify: `apps/desktop/src/pages/Settings.tsx`
- Modify: `apps/desktop/src/pages/Onboarding.tsx`
- Modify: `apps/desktop/src/api/types.ts`
- Modify: `apps/desktop/src/styles.css`
- Create/Modify tests under `apps/desktop/src/`

**要点：**

- Provider 列表：新增/编辑/删除/启用
- 表单：名称、类型（OpenAI Chat Completions / OpenAI Responses / Anthropic / Gemini / Custom）、base URL、API key、模型探测、模型下拉、测试连接
- Custom 适配器高级字段：headers、body 模板、response/stream 路径
- Embedding：模式切换、外部 provider/model、本地模型状态
- 网关：开关、端口、RAG 模式、MCP 开关、token 显示/轮换
- 配置片段：
  - Trae MCP JSON
  - OpenAI Chat Completions base URL/token
  - OpenAI Responses base URL/token
  - Claude 客户端 base URL/token
- Onboarding 移除 Ollama；改为“添加 Provider → 探测模型 → 测试 → 导入资料”
- 错误：密钥无效、模型不可用、网关关闭

**验证：**

```powershell
cd apps\desktop
npm run build
npx vitest run
```

**Commit:**

```powershell
git add apps/desktop/src
git commit -m "feat: add provider and gateway settings ui"
```

---

## Task V2-11: 移除 Ollama/本地 LLM 路径与打包更新

**Files:**
- Delete: `services/api/app/llm/ollama.py`
- Modify: `services/api/app/qa/answer.py`
- Modify: `services/api/app/api/ask.py`
- Modify: `services/api/app/api/models.py`（改为 provider/embedding 状态或删除）
- Modify: `services/api/app/main.py`
- Modify: `scripts/download-models.ps1`
- Modify: `services/api/kb-server.spec`
- Modify: `services/api/app/pages/Onboarding.tsx`（若 V2-10 未完全覆盖）
- Modify: `README.md`
- Modify: `requirements.txt`

**要点：**

- `qa/answer.py` 改用 provider 流式接口
- `api/ask.py` 不再捕获 `OllamaUnavailable`，改为 provider 错误
- `models/status` 改为 provider/embedding 状态，不再探测 Ollama
- `download-models.ps1` 只下载 BGE-M3，不安装 Ollama
- PyInstaller spec 移除 Ollama 相关 hidden imports；保留 BGE-M3/torch
- 文档明确：不再需要 Ollama；外部 API 需要网络；本地 embedding 首次需下载

**验证：**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests -q
cd apps\desktop
npm run build
cd src-tauri
cargo check
```

**Commit:**

```powershell
git add -A
git commit -m "refactor: remove ollama and wire external provider"
```

---

## Task V2-12: 黄金集、Mock Provider 端到端与性能

**Files:**
- Create: `services/api/tests/mock_provider.py`
- Create: `tests/golden/questions.json`
- Create: `scripts/eval_golden.py`
- Create: `docs/benchmarks/2026-10-27-local-kb-v2.md`
- Modify: `README.md`

**要点：**

- `mock_provider.py`：本地 FastAPI 模拟 OpenAI Chat Completions、OpenAI Responses 的流式/非流式
- 黄金集 10-15 个问题，期望文件/页码
- `eval_golden.py` 通过本软件 `/api/v1/ask` 或入站 `/v1/chat/completions`、`/v1/responses` 调用，检查引用
- 无真实密钥依赖；真实 provider 可选手动验证
- 性能记录：导入、检索、首 token、总耗时

**验证：**

```powershell
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests -q
.\.venv\Scripts\python.exe scripts\eval_golden.py
```

**Commit:**

```powershell
git add services/api/tests/mock_provider.py tests/golden scripts/eval_golden.py docs/benchmarks README.md
git commit -m "test: add mock provider golden set and v2 benchmark"
```

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-10-27-local-knowledge-base-v2.md`.

Two execution options:

1. **Subagent-Driven（当前会话）**：每个 V2 Task 派一个全新子 Agent，任务间审查。
2. **Parallel Session（独立会话）**：新会话用 `executing-plans` 按 Task 批量执行。

建议先用 **mock provider** 跑通 V2-1～V2-9，再填真实 API 密钥做手动验收；不要一开始就依赖真实密钥。
