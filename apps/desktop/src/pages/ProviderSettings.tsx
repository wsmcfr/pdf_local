import { useCallback, useEffect, useState } from "react";
import {
  createProvider,
  deleteProvider,
  listPresets,
  listProviders,
  probeProviderModels,
  testProvider,
  updateProvider,
  type ProviderInput,
} from "../api/providers";
import type { Preset, Provider, ProviderKind, ProviderModel } from "../api/types";

const KIND_OPTIONS: { value: ProviderKind; label: string }[] = [
  { value: "openai_chat", label: "OpenAI Chat Completions" },
  { value: "openai_responses", label: "OpenAI Responses" },
  { value: "anthropic", label: "Anthropic" },
  { value: "gemini", label: "Gemini" },
  { value: "custom", label: "Custom" },
];

const KIND_LABEL: Record<ProviderKind, string> = {
  openai_chat: "OpenAI Chat",
  openai_responses: "OpenAI Responses",
  anthropic: "Anthropic",
  gemini: "Gemini",
  custom: "Custom",
};

type Draft = {
  id: string | null;
  name: string;
  kind: ProviderKind;
  base_url: string;
  model: string;
  enabled: boolean;
  api_key: string;
  extraText: string;
  hasKey: boolean;
};

const EMPTY_DRAFT: Draft = {
  id: null,
  name: "",
  kind: "openai_chat",
  base_url: "",
  model: "",
  enabled: true,
  api_key: "",
  extraText: "",
  hasKey: false,
};

function draftFromProvider(provider: Provider): Draft {
  return {
    id: provider.id,
    name: provider.name,
    kind: provider.kind,
    base_url: provider.base_url,
    model: provider.model,
    enabled: provider.enabled,
    api_key: "",
    extraText:
      Object.keys(provider.extra).length > 0
        ? JSON.stringify(provider.extra, null, 2)
        : "",
    hasKey: provider.has_key,
  };
}

function parseExtra(text: string): Record<string, unknown> | undefined {
  const trimmed = text.trim();
  if (!trimmed) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    throw new Error("INVALID_EXTRA");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("INVALID_EXTRA");
  }
  return parsed as Record<string, unknown>;
}

function describeError(cause: unknown): string {
  const raw = cause instanceof Error ? cause.message : "请求失败";
  const messages: Record<string, string> = {
    INVALID_EXTRA: "Custom 高级配置必须是合法的 JSON 对象。",
    INVALID_KIND: "不支持的 Provider 类型。",
    PROVIDER_KEY_REQUIRED: "请先填写并保存 API Key。",
    PROVIDER_NOT_CONFIGURED: "请填写 Base URL 和 API Key。",
    INVALID_PROVIDER_CONFIG: "Provider 配置不完整，请检查类型与地址。",
    PROVIDER_AUTH_ERROR: "密钥无效或没有访问权限。",
    PROVIDER_TIMEOUT: "上游响应超时，请稍后重试。",
    PROVIDER_RATE_LIMIT: "上游限流，请稍后重试。",
    PROVIDER_ENDPOINT_NOT_FOUND: "接口地址不存在，请检查 Base URL。",
    MODEL_REQUIRED: "请先选择模型。",
    KEYRING_ERROR: "无法访问系统凭据存储，密钥未保存。",
    PROVIDER_NOT_FOUND: "找不到该 Provider。",
  };
  return messages[raw] ?? raw;
}

export default function ProviderSettings() {
  const [providers, setProviders] = useState<Provider[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [presetId, setPresetId] = useState("");
  const [models, setModels] = useState<ProviderModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState(false);
  const [testing, setTesting] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [items, presetItems] = await Promise.all([
        listProviders(),
        listPresets(),
      ]);
      setProviders(items);
      setPresets(presetItems);
    } catch (cause) {
      setError(`无法读取 Provider 列表：${describeError(cause)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function update<K extends keyof Draft>(key: K, value: Draft[K]) {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setMessage("");
  }

  function startNew() {
    setDraft(EMPTY_DRAFT);
    setPresetId("");
    setModels([]);
    setError("");
    setMessage("");
  }

  function edit(provider: Provider) {
    setDraft(draftFromProvider(provider));
    setPresetId("");
    setModels([]);
    setError("");
    setMessage("");
  }

  function applyPreset(value: string) {
    setPresetId(value);
    const preset = presets.find((item) => item.id === value);
    if (!preset) return;
    setDraft((prev) => ({
      ...prev,
      name: prev.name.trim() ? prev.name : preset.name,
      kind: preset.kind,
      base_url: preset.base_url,
      model: preset.default_model,
    }));
    setModels([]);
    setMessage("");
  }

  async function save() {
    if (!draft.name.trim() || !draft.base_url.trim()) {
      setError("请填写名称和 Base URL。");
      return;
    }

    setSaving(true);
    setError("");
    setMessage("");
    try {
      const payload: ProviderInput = {
        name: draft.name.trim(),
        kind: draft.kind,
        base_url: draft.base_url.trim(),
        model: draft.model.trim(),
        enabled: draft.enabled,
      };
      const extra = parseExtra(draft.extraText);
      if (extra) payload.extra = extra;
      if (draft.api_key.trim()) payload.api_key = draft.api_key.trim();

      const saved = draft.id
        ? await updateProvider(draft.id, payload)
        : await createProvider(payload);

      setDraft(draftFromProvider(saved));
      setMessage(draft.id ? "Provider 已更新。" : "Provider 已创建。");
      await load();
    } catch (cause) {
      setError(`保存失败：${describeError(cause)}`);
    } finally {
      setSaving(false);
    }
  }

  async function detectModels() {
    if (!draft.id) return;
    setProbing(true);
    setError("");
    setMessage("");
    try {
      const detected = await probeProviderModels(draft.id);
      setModels(detected);
      setMessage(
        detected.length > 0
          ? `探测到 ${detected.length} 个模型。`
          : "探测成功，但返回的模型列表为空。",
      );
      if (!draft.model.trim() && detected.length > 0) {
        setDraft((prev) => ({ ...prev, model: detected[0].id }));
      }
    } catch (cause) {
      setError(`探测模型失败：${describeError(cause)}`);
    } finally {
      setProbing(false);
    }
  }

  async function checkConnection() {
    if (!draft.id) return;
    setTesting(true);
    setError("");
    setMessage("");
    try {
      const result = await testProvider(draft.id);
      if (result.ok) {
        const latency =
          typeof result.latency_ms === "number"
            ? `（${Math.round(result.latency_ms)} ms）`
            : "";
        setMessage(`连接成功${latency}。`);
      } else {
        setError(`连接失败：${describeError(result.error ?? "PROVIDER_ERROR")}`);
      }
    } catch (cause) {
      setError(`连接失败：${describeError(cause)}`);
    } finally {
      setTesting(false);
    }
  }

  async function remove(provider: Provider) {
    if (!window.confirm(`确定删除 Provider「${provider.name}」？`)) return;
    setDeletingId(provider.id);
    setError("");
    setMessage("");
    try {
      await deleteProvider(provider.id);
      if (draft.id === provider.id) startNew();
      setMessage("Provider 已删除。");
      await load();
    } catch (cause) {
      setError(`删除失败：${describeError(cause)}`);
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <section className="settings-section">
      <div className="section-head">
        <div>
          <h2>Provider 管理</h2>
          <p className="muted small">
            问答与外部 embedding 使用这里配置的模型服务，密钥只保存在系统凭据存储中。
          </p>
        </div>
        <button type="button" className="secondary" onClick={startNew}>
          新增 Provider
        </button>
      </div>

      {error && <p className="notice error small">{error}</p>}
      {message && <p className="notice ok small">{message}</p>}

      <div className="provider-layout">
        <div className="provider-list">
          {loading && <p className="empty small">正在读取 Provider…</p>}
          {!loading && providers.length === 0 && (
            <p className="empty small">还没有 Provider。添加一个即可开始问答。</p>
          )}
          {providers.map((provider) => (
            <article
              key={provider.id}
              className={`provider-card${
                draft.id === provider.id ? " selected" : ""
              }`}
            >
              <header>
                <strong>{provider.name}</strong>
                <span className="tag">{KIND_LABEL[provider.kind]}</span>
              </header>
              <p className="muted small">{provider.base_url}</p>
              <p className="muted small">
                模型：{provider.model || "未设置"} ·{" "}
                {provider.enabled ? "已启用" : "已停用"} ·{" "}
                {provider.has_key ? "已保存密钥" : "未保存密钥"}
              </p>
              <div className="header-actions">
                <button
                  type="button"
                  className="link-button"
                  onClick={() => edit(provider)}
                >
                  编辑
                </button>
                <button
                  type="button"
                  className="link-button danger-text"
                  onClick={() => void remove(provider)}
                  disabled={deletingId === provider.id}
                >
                  {deletingId === provider.id ? "删除中…" : "删除"}
                </button>
              </div>
            </article>
          ))}
        </div>

        <form
          className="card provider-form"
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <h3>{draft.id ? "编辑 Provider" : "新增 Provider"}</h3>

          <label className="field">
            <span>预设</span>
            <select
              value={presetId}
              onChange={(event) => applyPreset(event.target.value)}
            >
              <option value="">自定义 / 不套用预设</option>
              {presets.map((preset) => (
                <option key={preset.id} value={preset.id}>
                  {preset.name}
                </option>
              ))}
            </select>
            <small className="muted">选择预设会填充类型、地址与默认模型。</small>
          </label>

          <label className="field">
            <span>名称</span>
            <input
              value={draft.name}
              onChange={(event) => update("name", event.target.value)}
              placeholder="例如 DeepSeek"
            />
          </label>

          <label className="field">
            <span>类型</span>
            <select
              value={draft.kind}
              onChange={(event) =>
                update("kind", event.target.value as ProviderKind)
              }
            >
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>

          <label className="field">
            <span>Base URL</span>
            <input
              value={draft.base_url}
              onChange={(event) => update("base_url", event.target.value)}
              placeholder="https://api.example.com/v1"
            />
          </label>

          <label className="field">
            <span>API Key</span>
            <input
              type="password"
              autoComplete="new-password"
              value={draft.api_key}
              onChange={(event) => update("api_key", event.target.value)}
              placeholder={
                draft.hasKey ? "已保存，留空则不修改" : "填写 API Key"
              }
            />
          </label>

          <label className="field">
            <span>默认模型</span>
            <input
              list="provider-model-options"
              value={draft.model}
              onChange={(event) => update("model", event.target.value)}
              placeholder="例如 deepseek-chat"
            />
            <datalist id="provider-model-options">
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.name}
                </option>
              ))}
            </datalist>
          </label>

          <div className="header-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => void detectModels()}
              disabled={!draft.id || probing}
            >
              {probing ? "探测中…" : "探测模型"}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={() => void checkConnection()}
              disabled={!draft.id || testing}
            >
              {testing ? "测试中…" : "测试连接"}
            </button>
          </div>
          {!draft.id && (
            <small className="muted">保存后可探测模型和测试连接。</small>
          )}

          {draft.kind === "custom" && (
            <label className="field">
              <span>Custom 高级配置（JSON）</span>
              <textarea
                rows={6}
                value={draft.extraText}
                onChange={(event) => update("extraText", event.target.value)}
                placeholder={
                  '{\n  "headers": {},\n  "body": {},\n  "response_path": "choices.0.message.content"\n}'
                }
              />
              <small className="muted">
                可配置 headers、请求体模板、响应与流式路径，仅限当前 Provider。
              </small>
            </label>
          )}

          <label className="checkbox">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => update("enabled", event.target.checked)}
            />
            <span>启用该 Provider</span>
          </label>

          <div className="header-actions">
            <button type="submit" className="primary" disabled={saving}>
              {saving ? "保存中…" : "保存 Provider"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}
