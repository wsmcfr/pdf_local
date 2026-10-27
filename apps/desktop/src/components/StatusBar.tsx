import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import { getGatewayConfig } from "../api/gateway";
import { listProviders } from "../api/providers";
import type { GatewayConfig, Health, Provider } from "../api/types";

type Status = "loading" | "ok" | "error";

export default function StatusBar() {
  const [status, setStatus] = useState<Status>("loading");
  const [providers, setProviders] = useState<Provider[] | null>(null);
  const [gateway, setGateway] = useState<GatewayConfig | null>(null);

  const refresh = useCallback(async () => {
    setStatus("loading");
    try {
      const response = await apiFetch("/health");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await (response.json() as Promise<Health>);
      setStatus("ok");
    } catch {
      setStatus("error");
      setProviders(null);
      setGateway(null);
      return;
    }

    try {
      setProviders(await listProviders());
    } catch {
      setProviders(null);
    }
    try {
      setGateway(await getGatewayConfig());
    } catch {
      setGateway(null);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const providerConfigured =
    providers === null
      ? null
      : providers.some((provider) => provider.enabled && provider.has_key);
  const gatewayEnabled = gateway === null ? null : gateway.enabled;

  return (
    <footer className="status-bar">
      <span
        className={`status-dot ${
          status === "ok" ? "ok" : status === "error" ? "bad" : ""
        }`}
      />
      <span>
        {status === "ok"
          ? "本地服务已连接"
          : status === "error"
            ? "本地服务未连接"
            : "正在连接本地服务…"}
      </span>
      <span className="status-sep">·</span>
      <span
        className={`status-dot ${
          status !== "ok"
            ? ""
            : providerConfigured === true
              ? "ok"
              : providerConfigured === false
                ? "bad"
                : ""
        }`}
      />
      <span>
        {providerConfigured === true
          ? "Provider 已配置"
          : providerConfigured === false
            ? "Provider 未配置"
            : "Provider 状态未知"}
      </span>
      <span className="status-sep">·</span>
      <span
        className={`status-dot ${
          status !== "ok"
            ? ""
            : gatewayEnabled === true
              ? "ok"
              : gatewayEnabled === false
                ? "bad"
                : ""
        }`}
      />
      <span>
        {gatewayEnabled === true
          ? "网关已开启"
          : gatewayEnabled === false
            ? "网关已关闭"
            : "网关状态未知"}
      </span>
      <button type="button" className="link-button" onClick={() => void refresh()}>
        刷新
      </button>
    </footer>
  );
}
