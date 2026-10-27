import { useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { pickDirectory, startScan } from "../api/scan";
import type { Health, Settings as SettingsPayload } from "../api/types";

export default function Onboarding() {
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    Promise.all([apiFetch("/settings"), apiFetch("/health")])
      .then(async ([settingsResponse, healthResponse]) => {
        if (!settingsResponse.ok || !healthResponse.ok) {
          throw new Error("local service unavailable");
        }
        const [settingsData, healthData] = await Promise.all([
          settingsResponse.json() as Promise<SettingsPayload>,
          healthResponse.json() as Promise<Health>,
        ]);
        if (cancelled) return;
        setSettings(settingsData);
        setHealth(healthData);
      })
      .catch(() => {
        if (!cancelled) setError("无法连接本地服务，请先启动应用。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

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

  return (
    <section className="page onboarding">
      <header className="page-header">
        <div>
          <h1>开始使用 Local KB</h1>
          <p className="muted">三步完成本地知识库的准备：确认目录、启动模型、导入资料。</p>
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
            <h2>本地模型</h2>
            <p className="muted small">
              问答由本机 Ollama 提供，不把资料发送到外部服务。
            </p>
            {health === null ? (
              <p className="muted small">正在检查 Ollama 状态…</p>
            ) : health.ollama ? (
              <p className="notice ok small">Ollama 已连接，可以开始提问。</p>
            ) : (
              <div className="notice warn small">
                <p>未检测到 Ollama。请安装并启动 Ollama，然后准备模型：</p>
                <pre>ollama pull qwen2.5:7b</pre>
                <p>模型下载与离线包管理将在后续版本中提供。</p>
              </div>
            )}
          </div>
        </article>

        <article className="card step">
          <span className="step-index">3</span>
          <div>
            <h2>导入资料</h2>
            <p className="muted small">选择一个文件夹，PDF、Markdown 与文本文件会被递归导入。</p>
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
              <Link className="link-button" to="/settings">
                查看设置
              </Link>
            </div>
          </div>
        </article>
      </div>
    </section>
  );
}
