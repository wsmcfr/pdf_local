# Local KB

本项目是一个本地优先的知识库桌面应用，并在本机提供一个 AI 网关：

- 扫描本地文件夹（PDF / Markdown / TXT），OCR、切块、全文检索与可选的向量检索
- 基于检索到的资料问答，回答带文件/页码或章节引用
- 通过本机网关把知识库能力提供给 Trae MCP、OpenAI Chat/Responses、Claude 等客户端
- 不依赖 Ollama；模型服务由用户在设置页配置的外部 Provider 提供

## 目录结构

```
apps/desktop/          Tauri 2 + React 前端
services/api/          FastAPI 后端（知识库、检索、Provider、网关、MCP）
scripts/               构建、开发与评估脚本
tests/golden/          黄金集语料与问题
docs/                  设计与基准记录
```

## 环境要求

- Python 3.11
- Node 22
- Rust（Tauri 2 开发与打包）
- Windows 开发环境（当前主要在 Windows 上验证）

初始化：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

cd apps\desktop
npm install
```

## 启动

集成开发（推荐）：Tauri 会自动以开发模式启动 `.venv` 中的 Python sidecar。

```powershell
cd apps\desktop
npm run tauri dev
```

分别启动：

```powershell
# 后端 API（脚本内设置 KB_TOKEN=dev-token、PYTHONPATH=services/api）
.\scripts\dev-api.ps1

# 仅前端（没有 Tauri 桥时无法获取 sidecar 地址与 token，只适合纯 UI 调试）
cd apps\desktop
npm run dev
```

打包前如需生成独立 sidecar：

```powershell
.\scripts\build-sidecar.ps1
```

数据目录：开发模式下默认为仓库下的 `data/`（例如 `F:\xiangfa\data`），可用
`KB_DATA_DIR` 覆盖；打包后的 sidecar 未设置该变量时回退到 `~/.local-kb`。数据库、
FAISS 索引、页面缓存、本地模型与 Hugging Face 缓存都放在这个目录，不会默认写入 C 盘。
桌面端每次启动会生成 `KB_TOKEN` 并只在本机进程间使用。

## 配置

在应用左侧进入“设置”：

- **Provider**：`OpenAI Chat Completions`、`OpenAI Responses`、`Anthropic`、`Gemini`、
  `Custom`。填写 Base URL、模型与 API Key；Custom 可额外配置 headers、请求体模板和
  响应路径。保存后可“探测模型”和“测试连接”。
- **API Key**：只写入操作系统凭据存储（keyring），不写入 SQLite，也不会回显。
- **Embedding**：`local`（本地 BGE-M3）、`external`（外部 API）、`auto`（本地失败回退
  外部）、`fts`（仅全文检索）。本地模型可选，首次使用可运行：
  `.\\.venv\\Scripts\\python.exe scripts\\download_embedding.py`。脚本只下载
  `SentenceTransformer` 需要的文件（跳过 ONNX、示例图片和可选权重）；模型与 Hugging
  Face 缓存都写入数据目录，未下载时测试会立即失败并回退 FTS，不会隐式下载。直连
  Hugging Face 超时时可先设置 `KB_HF_ENDPOINT=https://hf-mirror.com` 再运行脚本。
  下载完成后，之前只建了 FTS 的文档需要关闭应用并重建向量索引：
  `.\\.venv\\Scripts\\python.exe scripts\\reindex_embeddings.py`。
- **检索参数**：`context_chunks` 控制每次问答送入模型的资料块数量。

## 网关

在“设置 → 网关”中：

1. 打开“启用本地网关”，选择 LLM Provider 与 RAG 模式
   （`auto` 自动检索、`off` 纯模型回答、`tools` 不自动注入，配合 MCP 工具使用）。
2. 复制 Gateway Token（仅本机可见，可轮换）。
3. 使用页面生成的配置片段：

- **Trae MCP（SSE）**：地址 `http://127.0.0.1:<端口>/mcp/sse`，请求头
  `Authorization: Bearer <gateway-token>`
- **MCP（stdio）**：命令 `.\\.venv\\Scripts\\python.exe -m app.mcp_stdio`，环境变量
  `PYTHONPATH=services/api`、`KB_DATA_DIR=<数据目录>`、`KB_GATEWAY_TOKEN=<token>`
- **OpenAI Chat Completions / Responses**：Base URL `http://127.0.0.1:<端口>/v1`，
  API Key 使用 gateway token
- **Claude 客户端**：`ANTHROPIC_BASE_URL=http://127.0.0.1:<端口>`，
  `ANTHROPIC_AUTH_TOKEN=<gateway-token>`

网关默认只监听本机；若设置 `KB_GATEWAY_TOKEN` 环境变量，则 token 由环境变量管理，
界面内不能轮换。

## 测试与评估

```powershell
# 后端
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe -m pytest services/api/tests -q

# 前端
cd apps\desktop
npm run build
npx vitest run

# 黄金集：临时数据目录 + FTS + MockProvider，不访问网络、不需要真实密钥
$env:PYTHONPATH="services/api"
.\.venv\Scripts\python.exe scripts\eval_golden.py
```

黄金集语料在 `tests/golden/corpus/`，问题与预期引用在 `tests/golden/questions.json`。
评估脚本使用 `PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring`，不会读写真实凭据。

## 安全说明

- API Key 与 gateway token 保存在本机 keyring；日志与错误响应不会回显密钥。
- 问答时，检索到的文档片段会发送到当前配置的**外部 Provider**。如果不希望内容离开
  本机，请使用本地/自建模型服务并自行评估风险。
- Embedding 选择 `external` 或 `auto` 时，待索引或检索的文本片段会发送到所选 Provider；
  选择 `local` 或 `fts` 不会发送。
- 网关与 sidecar 只监听 `127.0.0.1`，请勿直接暴露到公网。
