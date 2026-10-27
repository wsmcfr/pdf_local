import { useCallback, useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { getEmbeddingConfig, getGatewayConfig } from "../api/gateway";
import { listProviders } from "../api/providers";
import { pickDirectory, startScan } from "../api/scan";
import type {
  EmbeddingConfig,
  GatewayConfig,
  Provider,
  Settings as SettingsPayload,
} from "../api/types";

async function readSettings(): Promise<SettingsPayload> {
  const response = await apiFetch("/settings");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return (await response.json()) as SettingsPayload;
}

export default function Onboarding() {
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [embedding, setEmbedding] = useState<EmbeddingConfig | null>(null);
  const [gateway, setGateway] = useState<GatewayConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [settingsData, providerList, embeddingData, gatewayData] =
        await Promise.all([
          readSettings(),
          listProviders(),
          getEmbeddingConfig(),
          getGatewayConfig(),
        ]);
      setSettings(settingsData);
      setProviders(providerList);
      setEmbedding(embeddingData);
      setGateway(gatewayData);
    } catch {
      setError("无法连接本地服务，请先启动应用。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function beginImport() {
    setError("");
    setImporting(true);
    try {
      const selected = await pickDirectory();
      if (!selected) return;
      const job = await startScan(selected);
      navigate("/jobs", { state: { jobId: job.id } });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导入失败");
    } finally {
      setImporting(false);
    }
  }

  const providersReady = providers.some(
    (provider) => provider.enabled && provider.has_key,
  );
  const embeddingReady = Boolean(embedding?.ok);
  const gatewayReady = Boolean(gateway?.enabled);

  return (
    <section className="page onboarding">
      <header className="page-header">
        <div>
          <h1>开始使用 Local KB</h1>
          <p className="muted">
            三步完成准备：确认数据目录、配置 Provider 与网关、导入资料。
          </p>
        </div>
      </header>

      {error && <p className="notice error">{error}</p>}

      <div className="steps">
        <article className="card step">
          <span className="step-index">1</span>
          <div>
            <h2>数据目录</h2>
            <p className="muted small">文档索引、向量与设置都保存在本机。</p>
            <p className="path-box">
              {settings?.data_dir || "由 sidecar 管理（默认 ~/.local-kb）"}
            </p>
          </div>
        </article>

        <article className="card step">
          <span className="step-index">2</span>
          <div>
            <h2>模型与 Provider</h2>
            <p className="muted small">
              问答由你添加的 Provider 提供；文档片段会发送到所选 Provider。
            </p>

            {loading && <p className="muted small">正在检查配置…</p>}

            {!loading && (
              <>
                <p className="muted small">
                  <span
                    className={`status-dot ${providersReady ? "ok" : "bad"}`}
                  />
                  Provider：
                  {providers.length === 0
                    ? "尚未配置"
                    : providersReady
                      ? `已配置（${providers
                          .filter((provider) => provider.enabled)
                          .map((provider) => provider.name)
                          .join("、")}）`
                      : "已添加，但未启用或缺少密钥"}
                </p>
                <p className="muted small">
                  <span
                    className={`status-dot ${embeddingReady ? "ok" : "bad"}`}
                  />
                  Embedding：
                  {embedding
                    ? embedding.ok
                      ? `就绪（${embedding.mode}）`
                      : "未配置，检索会使用 FTS"
                    : "状态未知"}
                </p>
                <p className="muted small">
                  <span
                    className={`status-dot ${gatewayReady ? "ok" : "bad"}`}
                  />
                  网关：
                  {gateway ? (gateway.enabled ? "已开启" : "已关闭") : "状态未知"}
                </p>

                {!providersReady && (
                  <div className="notice warn small">
                    <p>
                      {providers.length === 0
                        ? "还没有配置 Provider。"
                        : "已添加的 Provider 尚未启用或缺少密钥。"}
                      请到设置页添加 Provider、填写 API Key 并选择模型。
                    </p>
                    <Link className="link-button" to="/settings">
                      前往设置添加 Provider
                    </Link>
                  </div>
                )}
              </>
            )}

            <div className="header-actions">
              <button
                type="button"
                className="link-button"
                onClick={() => void load()}
                disabled={loading}
              >
                刷新状态
              </button>
              <Link className="link-button" to="/settings">
                查看设置
              </Link>
            </div>
          </div>
        </article>

        <article className="card step">
          <span className="step-index">3</span>
          <div>
            <h2>导入资料</h2>
            <p className="muted small">
              选择一个文件夹，PDF、Markdown 与文本文件会被递归导入。
            </p>
            <div className="header-actions">
              <button
                type="button"
                className="primary"
                onClick={() => void beginImport()}
                disabled={importing}
              >
                <FolderOpen size={15} aria-hidden="true" />
                {importing ? "正在创建导入任务…" : "选择文件夹开始导入"}
              </button>
            </div>
          </div>
        </article>
      </div>
    </section>
  );
}
