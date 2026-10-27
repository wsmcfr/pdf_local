import { useCallback, useEffect, useState } from "react";
import { FolderOpen } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import { pickDirectory, startScan } from "../api/scan";
import type { ModelsStatus, Settings as SettingsPayload } from "../api/types";

export default function Onboarding() {
  const [settings, setSettings] = useState<SettingsPayload | null>(null);
  const [models, setModels] = useState<ModelsStatus | null>(null);
  const [modelsError, setModelsError] = useState("");
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    apiFetch("/settings")
      .then(async (response) => {
        if (!response.ok) throw new Error("local service unavailable");
        const data = (await response.json()) as SettingsPayload;
        if (cancelled) return;
        setSettings(data);
      })
      .catch(() => {
        if (!cancelled) setError("无法连接本地服务，请先启动应用。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadModels = useCallback(() => {
    let cancelled = false;
    apiFetch("/models/status")
      .then(async (response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return (await response.json()) as ModelsStatus;
      })
      .then((data) => {
        if (cancelled) return;
        setModels(data);
        setModelsError("");
      })
      .catch(() => {
        if (!cancelled) setModelsError("无法读取本地模型状态，请确认本地服务已启动。");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => loadModels(), [loadModels]);

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

  const modelsReady = Boolean(
    models?.ocr.ready && models?.embedding.ready && models?.llm.ready,
  );

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
              问答由本机 Ollama 提供，资料与模型都不发送到外部服务。
            </p>

            {modelsError && <p className="notice error small">{modelsError}</p>}
            {models === null && !modelsError && (
              <p className="muted small">正在检查本地模型…</p>
            )}

            {models && (
              <>
                <p className="muted small">
                  <span
                    className={`status-dot ${models.ocr.ready ? "ok" : "bad"}`}
                  />
                  OCR（RapidOCR）：{models.ocr.ready ? "就绪（wheel 自带模型）" : "不可用"}
                </p>
                <p className="muted small">
                  <span
                    className={`status-dot ${models.embedding.ready ? "ok" : "bad"}`}
                  />
                  Embedding（BGE-M3）：{models.embedding.ready ? "就绪" : "未下载"}
                </p>
                <p className="muted small">
                  <span
                    className={`status-dot ${models.llm.ready ? "ok" : "bad"}`}
                  />
                  LLM（{models.llm.target}）：
                  {models.llm.ready
                    ? "就绪"
                    : models.llm.installed
                      ? "Ollama 已连接，未找到该模型"
                      : "Ollama 未启动"}
                </p>

                {modelsReady ? (
                  <p className="notice ok small">本地模型已就绪，可以离线问答。</p>
                ) : (
                  <div className="notice warn small">
                    <p>应用不会自动下载模型，请手动准备未就绪的部分：</p>
                    {!models.ocr.ready && (
                      <p>OCR 不可用：请重新安装 rapidocr_onnxruntime 依赖。</p>
                    )}
                    {!models.embedding.ready && (
                      <>
                        <p>Embedding（约 2.2GB）：</p>
                        <pre>.\\.venv\\Scripts\\python.exe scripts\\download_embedding.py</pre>
                        <p className="muted small">保存位置：{models.embedding.path}</p>
                      </>
                    )}
                    {!models.llm.installed && (
                      <p>
                        Ollama 未安装或未启动：请从 https://ollama.com/download
                        安装并启动后刷新状态。
                      </p>
                    )}
                    {models.llm.installed && !models.llm.ready && (
                      <>
                        <p>LLM：</p>
                        <pre>ollama pull {models.llm.target}</pre>
                      </>
                    )}
                    <p>也可以一次执行（会实际下载模型）：</p>
                    <pre>.\\scripts\\download-models.ps1</pre>
                  </div>
                )}
              </>
            )}

            <div className="header-actions">
              <button type="button" className="link-button" onClick={() => loadModels()}>
                刷新模型状态
              </button>
            </div>
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
