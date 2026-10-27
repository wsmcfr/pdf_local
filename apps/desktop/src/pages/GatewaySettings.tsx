import { useCallback, useEffect, useState } from "react";
import { getServerInfo } from "../api/client";
import {
  getEmbeddingConfig,
  getGatewayConfig,
  rotateGatewayToken,
  testEmbedding,
  updateEmbeddingConfig,
  updateGatewayConfig,
} from "../api/gateway";
import { listProviders } from "../api/providers";
import type {
  EmbeddingConfig,
  EmbeddingMode,
  GatewayConfig,
  Provider,
} from "../api/types";

const EMBEDDING_MODES: { value: EmbeddingMode; label: string }[] = [
  { value: "local", label: "本地 BGE-M3" },
  { value: "external", label: "外部 API" },
  { value: "auto", label: "自动回退（本地 → 外部）" },
  { value: "fts", label: "仅全文检索（FTS）" },
];

function describeError(cause: unknown): string {
  const raw = cause instanceof Error ? cause.message : "请求失败";
  const messages: Record<string, string> = {
    TOKEN_ENV_MANAGED:
      "当前 token 由 KB_GATEWAY_TOKEN 环境变量管理，无法在界面中轮换。",
    KEYRING_ERROR: "无法访问系统凭据存储。",
    PROVIDER_NOT_FOUND: "找不到所选 Provider。",
    PROVIDER_REQUIRED: "外部 embedding 需要先选择 Provider。",
    MODEL_REQUIRED: "外部 embedding 需要填写模型名称。",
    INVALID_MODE: "不支持的 embedding 模式。",
    INVALID_RAG_MODE: "不支持的 RAG 模式。",
    INVALID_ENABLED: "网关开关的值无效。",
    INVALID_MCP_ENABLED: "MCP 开关的值无效。",
    INVALID_PROVIDER_ID: "Provider 选择无效。",
  };
  return messages[raw] ?? raw;
}

type SnippetProps = {
  label: string;
  text: string;
  copied: boolean;
  onCopy: () => void;
};

function Snippet({ label, text, copied, onCopy }: SnippetProps) {
  return (
    <div className="snippet">
      <div className="snippet-head">
        <strong>{label}</strong>
        <button type="button" className="secondary" onClick={onCopy}>
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre>{text}</pre>
    </div>
  );
}

export default function GatewaySettings() {
  const [gateway, setGateway] = useState<GatewayConfig | null>(null);
  const [embedding, setEmbedding] = useState<EmbeddingConfig | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [baseUrl, setBaseUrl] = useState("http://127.0.0.1:8000");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [savingGateway, setSavingGateway] = useState(false);
  const [savingEmbedding, setSavingEmbedding] = useState(false);
  const [testingEmbedding, setTestingEmbedding] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [copied, setCopied] = useState("");
  const [gatewayError, setGatewayError] = useState("");
  const [gatewayMessage, setGatewayMessage] = useState("");
  const [embeddingError, setEmbeddingError] = useState("");
  const [embeddingMessage, setEmbeddingMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [config, embeddingConfig, providerList] = await Promise.all([
        getGatewayConfig(),
        getEmbeddingConfig(),
        listProviders(),
      ]);
      setGateway(config);
      setEmbedding(embeddingConfig);
      setProviders(providerList);
    } catch (cause) {
      setLoadError(`无法读取网关配置：${describeError(cause)}`);
    } finally {
      setLoading(false);
    }

    try {
      const info = await getServerInfo();
      setBaseUrl(info.base_url);
    } catch {
      // Keep the localhost fallback when the Tauri bridge is unavailable.
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveGateway() {
    if (!gateway) return;
    setSavingGateway(true);
    setGatewayError("");
    setGatewayMessage("");
    try {
      const saved = await updateGatewayConfig({
        enabled: gateway.enabled,
        rag_mode: gateway.rag_mode,
        provider_id: gateway.provider_id,
        mcp_enabled: gateway.mcp_enabled,
      });
      setGateway(saved);
      setGatewayMessage("网关设置已保存。");
    } catch (cause) {
      setGatewayError(`保存失败：${describeError(cause)}`);
    } finally {
      setSavingGateway(false);
    }
  }

  async function rotateToken() {
    if (!window.confirm("轮换后旧 token 会立即失效，确定继续？")) return;
    setRotating(true);
    setGatewayError("");
    setGatewayMessage("");
    try {
      const token = await rotateGatewayToken();
      setGateway((prev) => (prev ? { ...prev, token } : prev));
      setShowToken(true);
      setGatewayMessage("已生成新的 gateway token，旧 token 立即失效。");
    } catch (cause) {
      setGatewayError(`轮换失败：${describeError(cause)}`);
    } finally {
      setRotating(false);
    }
  }

  async function copyText(label: string, text: string) {
    setGatewayError("");
    try {
      await navigator.clipboard.writeText(text);
      setCopied(label);
      window.setTimeout(() => setCopied(""), 1500);
    } catch {
      setGatewayError("复制失败，请手动选择文本复制。");
    }
  }

  function updateEmbedding(patch: Partial<EmbeddingConfig>) {
    setEmbedding((prev) => (prev ? { ...prev, ...patch } : prev));
    setEmbeddingMessage("");
    setEmbeddingError("");
  }

  async function saveEmbedding() {
    if (!embedding) return;
    setSavingEmbedding(true);
    setEmbeddingError("");
    setEmbeddingMessage("");
    try {
      const saved = await updateEmbeddingConfig({
        mode: embedding.mode,
        provider_id: embedding.provider_id,
        model: embedding.model,
      });
      setEmbedding(saved);
      setEmbeddingMessage("Embedding 配置已保存。");
    } catch (cause) {
      setEmbeddingError(`保存失败：${describeError(cause)}`);
    } finally {
      setSavingEmbedding(false);
    }
  }

  async function runEmbeddingTest() {
    setTestingEmbedding(true);
    setEmbeddingError("");
    setEmbeddingMessage("");
    try {
      const result = await testEmbedding();
      if (result.ok) {
        const dimension =
          typeof result.dimension === "number"
            ? `（${result.dimension} 维）`
            : "";
        setEmbeddingMessage(`Embedding 测试成功${dimension}。`);
      } else {
        setEmbeddingError("Embedding 测试失败，检索会回退到 FTS。");
      }
    } catch (cause) {
      setEmbeddingError(`测试失败：${describeError(cause)}`);
    } finally {
      setTestingEmbedding(false);
    }
  }

  const token = gateway?.token ?? "";
  const base = baseUrl.replace(/\/+$/, "");
  const model =
    providers.find((provider) => provider.id === gateway?.provider_id)?.model ||
    providers.find((provider) => provider.enabled)?.model ||
    "local-kb";

  const mcpSse = JSON.stringify(
    {
      mcpServers: {
        "local-kb": {
          type: "sse",
          url: `${base}/mcp/sse`,
          headers: {
            Authorization: `Bearer ${token}`,
          },
        },
      },
    },
    null,
    2,
  );

  const mcpStdio = JSON.stringify(
    {
      mcpServers: {
        "local-kb": {
          command: ".\\.venv\\Scripts\\python.exe",
          args: ["-m", "app.mcp_stdio"],
          env: {
            PYTHONPATH: "services/api",
            KB_DATA_DIR: "<你的数据目录>",
            KB_GATEWAY_TOKEN: token,
          },
        },
      },
    },
    null,
    2,
  );

  const openaiChat = [
    `Base URL: ${base}/v1`,
    `API Key: ${token}`,
    `Model: ${model}`,
  ].join("\n");

  const openaiResponses = [
    `Base URL: ${base}/v1`,
    `API Key: ${token}`,
    `Model: ${model}`,
  ].join("\n");

  const claudeClient = [
    `ANTHROPIC_BASE_URL=${base}`,
    `ANTHROPIC_AUTH_TOKEN=${token}`,
  ].join("\n");

  return (
    <>
      <section className="settings-section">
        <div className="section-head">
          <div>
            <h2>网关</h2>
            <p className="muted small">
              在本机提供 OpenAI / Claude 兼容接口与 MCP，默认只监听本机。
            </p>
          </div>
          <button
            type="button"
            className="secondary"
            onClick={() => void load()}
            disabled={loading}
          >
            刷新
          </button>
        </div>

        {loadError && <p className="notice error small">{loadError}</p>}
        {loading ? (
          <p className="empty small">正在读取网关配置…</p>
        ) : (
          gateway && (
            <div className="gateway-grid">
              <form
                className="card"
                onSubmit={(event) => {
                  event.preventDefault();
                  void saveGateway();
                }}
              >
                <h3>入站配置</h3>

                {gatewayError && (
                  <p className="notice error small">{gatewayError}</p>
                )}
                {gatewayMessage && (
                  <p className="notice ok small">{gatewayMessage}</p>
                )}

                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={gateway.enabled}
                    onChange={(event) => {
                      const checked = event.target.checked;
                      setGateway((prev) =>
                        prev ? { ...prev, enabled: checked } : prev,
                      );
                    }}
                  />
                  <span>启用本地网关</span>
                </label>

                <label className="field">
                  <span>RAG 模式</span>
                  <select
                    value={gateway.rag_mode}
                    onChange={(event) => {
                      const ragMode = event.target
                        .value as GatewayConfig["rag_mode"];
                      setGateway((prev) =>
                        prev ? { ...prev, rag_mode: ragMode } : prev,
                      );
                    }}
                  >
                    <option value="auto">auto：自动检索资料</option>
                    <option value="off">off：纯模型回答</option>
                    <option value="tools">tools：由模型决定是否调用工具</option>
                  </select>
                </label>

                <label className="field">
                  <span>LLM Provider</span>
                  <select
                    value={gateway.provider_id}
                    onChange={(event) => {
                      const providerId = event.target.value;
                      setGateway((prev) =>
                        prev ? { ...prev, provider_id: providerId } : prev,
                      );
                    }}
                  >
                    <option value="">自动选择第一个启用的 Provider</option>
                    {providers.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name}（{provider.model || "未设置模型"}）
                      </option>
                    ))}
                  </select>
                </label>

                <label className="checkbox">
                  <input
                    type="checkbox"
                    checked={gateway.mcp_enabled}
                    onChange={(event) => {
                      const checked = event.target.checked;
                      setGateway((prev) =>
                        prev ? { ...prev, mcp_enabled: checked } : prev,
                      );
                    }}
                  />
                  <span>启用 MCP</span>
                </label>

                <div className="header-actions">
                  <button
                    type="submit"
                    className="primary"
                    disabled={savingGateway}
                  >
                    {savingGateway ? "保存中…" : "保存网关设置"}
                  </button>
                </div>
              </form>

              <div className="card">
                <h3>Gateway Token</h3>
                <p className="muted small">
                  外部客户端使用此 token 访问网关。token 仅在本机界面可见。
                </p>
                <div className="token-row">
                  <input
                    type={showToken ? "text" : "password"}
                    readOnly
                    value={token}
                    placeholder="尚未生成"
                  />
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => setShowToken((value) => !value)}
                  >
                    {showToken ? "隐藏" : "显示"}
                  </button>
                </div>
                <div className="header-actions">
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void copyText("token", token)}
                  >
                    {copied === "token" ? "已复制" : "复制 token"}
                  </button>
                  <button
                    type="button"
                    className="secondary"
                    onClick={() => void rotateToken()}
                    disabled={rotating}
                  >
                    {rotating ? "轮换中…" : "轮换 token"}
                  </button>
                </div>
              </div>

              <div className="card gateway-snippets">
                <h3>客户端配置片段</h3>
                <p className="muted small">
                  地址基于本地服务 {base}；如端口变化，请以实际地址为准。
                </p>
                <Snippet
                  label="Trae MCP（SSE）"
                  text={mcpSse}
                  copied={copied === "mcp-sse"}
                  onCopy={() => void copyText("mcp-sse", mcpSse)}
                />
                <Snippet
                  label="MCP（stdio）"
                  text={mcpStdio}
                  copied={copied === "mcp-stdio"}
                  onCopy={() => void copyText("mcp-stdio", mcpStdio)}
                />
                <Snippet
                  label="OpenAI Chat Completions"
                  text={openaiChat}
                  copied={copied === "openai-chat"}
                  onCopy={() => void copyText("openai-chat", openaiChat)}
                />
                <Snippet
                  label="OpenAI Responses"
                  text={openaiResponses}
                  copied={copied === "openai-responses"}
                  onCopy={() => void copyText("openai-responses", openaiResponses)}
                />
                <Snippet
                  label="Claude 客户端"
                  text={claudeClient}
                  copied={copied === "claude"}
                  onCopy={() => void copyText("claude", claudeClient)}
                />
              </div>
            </div>
          )
        )}
      </section>

      <section className="settings-section">
        <div className="section-head">
          <div>
            <h2>Embedding</h2>
            <p className="muted small">
              本地 BGE-M3 不发送文档；外部 API 只发送待索引或检索的文本片段。
            </p>
          </div>
        </div>

        {embedding && (
          <form
            className="card"
            onSubmit={(event) => {
              event.preventDefault();
              void saveEmbedding();
            }}
          >
            <h3>向量化配置</h3>

            {embeddingError && (
              <p className="notice error small">{embeddingError}</p>
            )}
            {embeddingMessage && (
              <p className="notice ok small">{embeddingMessage}</p>
            )}

            <label className="field">
              <span>模式</span>
              <select
                value={embedding.mode}
                onChange={(event) =>
                  updateEmbedding({
                    mode: event.target.value as EmbeddingMode,
                  })
                }
              >
                {EMBEDDING_MODES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>外部 Provider</span>
              <select
                value={embedding.provider_id}
                onChange={(event) =>
                  updateEmbedding({ provider_id: event.target.value })
                }
                disabled={embedding.mode !== "external" && embedding.mode !== "auto"}
              >
                <option value="">未选择</option>
                {providers.map((provider) => (
                  <option key={provider.id} value={provider.id}>
                    {provider.name}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span>外部模型</span>
              <input
                value={embedding.model}
                onChange={(event) =>
                  updateEmbedding({ model: event.target.value })
                }
                placeholder="例如 text-embedding-3-small"
                disabled={embedding.mode !== "external" && embedding.mode !== "auto"}
              />
            </label>

            <p className="muted small">
              状态：{embedding.status} · 本地模型：{embedding.local_model}
            </p>

            <div className="header-actions">
              <button
                type="submit"
                className="primary"
                disabled={savingEmbedding}
              >
                {savingEmbedding ? "保存中…" : "保存 Embedding 设置"}
              </button>
              <button
                type="button"
                className="secondary"
                onClick={() => void runEmbeddingTest()}
                disabled={testingEmbedding}
              >
                {testingEmbedding ? "测试中…" : "测试 Embedding"}
              </button>
            </div>
          </form>
        )}
      </section>
    </>
  );
}
