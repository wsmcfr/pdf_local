import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { Settings as SettingsPayload } from "../api/types";

const MODELS = ["qwen2.5:7b", "qwen2.5:4b"];

type Draft = {
  ocr_dpi: string;
  llm_model: string;
  context_chunks: string;
  offline_mode: boolean;
};

export default function Settings() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const [dataDir, setDataDir] = useState("");
  const [draft, setDraft] = useState<Draft>({
    ocr_dpi: "300",
    llm_model: MODELS[0],
    context_chunks: "8",
    offline_mode: true,
  });

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/settings");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const settings = (await response.json()) as SettingsPayload;
      setDataDir(settings.data_dir);
      setDraft({
        ocr_dpi: String(settings.ocr_dpi),
        llm_model: settings.llm_model,
        context_chunks: String(settings.context_chunks),
        offline_mode: settings.offline_mode,
      });
    } catch {
      setError("无法读取设置，请确认本地服务已启动。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setSaved(false);
  }

  async function save() {
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const payload = {
        ocr_dpi: Math.min(Math.max(Number(draft.ocr_dpi) || 300, 72), 600),
        llm_model: draft.llm_model,
        context_chunks: Math.min(
          Math.max(Number(draft.context_chunks) || 8, 1),
          20,
        ),
        offline_mode: draft.offline_mode,
      };
      const response = await apiFetch("/settings", {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const settings = (await response.json()) as SettingsPayload;
      setDataDir(settings.data_dir);
      setDraft({
        ocr_dpi: String(settings.ocr_dpi),
        llm_model: settings.llm_model,
        context_chunks: String(settings.context_chunks),
        offline_mode: settings.offline_mode,
      });
      setSaved(true);
    } catch {
      setError("保存设置失败，请稍后重试。");
    } finally {
      setSaving(false);
    }
  }

  const models = MODELS.includes(draft.llm_model)
    ? MODELS
    : [...MODELS, draft.llm_model];

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>设置</h1>
          <p className="muted">调整导入与问答参数，保存后对后续任务生效。</p>
        </div>
        <button
          type="button"
          className="primary"
          onClick={() => void save()}
          disabled={loading || saving}
        >
          {saving ? "保存中…" : "保存设置"}
        </button>
      </header>

      {error && <p className="notice error">{error}</p>}
      {saved && <p className="notice ok">设置已保存。</p>}

      {loading ? (
        <p className="empty">正在读取设置…</p>
      ) : (
        <div className="settings-grid">
          <section className="card">
            <h2>文档处理</h2>
            <label className="field">
              <span>OCR DPI</span>
              <input
                type="number"
                min={72}
                max={600}
                step={1}
                value={draft.ocr_dpi}
                onChange={(event) => update("ocr_dpi", event.target.value)}
              />
              <small className="muted">扫描页面的渲染分辨率，默认 300。</small>
            </label>
          </section>

          <section className="card">
            <h2>问答模型</h2>
            <label className="field">
              <span>Ollama 模型</span>
              <select
                value={draft.llm_model}
                onChange={(event) => update("llm_model", event.target.value)}
              >
                {models.map((model) => (
                  <option key={model} value={model}>
                    {model}
                  </option>
                ))}
              </select>
              <small className="muted">7B 效果更好，4B 速度更快。</small>
            </label>

            <label className="field">
              <span>检索片段数</span>
              <input
                type="number"
                min={1}
                max={20}
                step={1}
                value={draft.context_chunks}
                onChange={(event) => update("context_chunks", event.target.value)}
              />
              <small className="muted">每次提问带入模型的资料片段数量。</small>
            </label>
          </section>

          <section className="card">
            <h2>运行模式</h2>
            <label className="checkbox">
              <input
                type="checkbox"
                checked={draft.offline_mode}
                onChange={(event) => update("offline_mode", event.target.checked)}
              />
              <span>离线模式</span>
            </label>
            <small className="muted">
              离线模式下不访问外部网络，模型与索引均保存在本机。
            </small>
          </section>

          <section className="card">
            <h2>数据目录</h2>
            <label className="field">
              <span>当前数据目录</span>
              <input type="text" readOnly value={dataDir} placeholder="由 sidecar 管理（默认 ~/.local-kb）" />
              <small className="muted">只读。切换数据目录需要重启应用。</small>
            </label>
          </section>
        </div>
      )}
    </section>
  );
}
