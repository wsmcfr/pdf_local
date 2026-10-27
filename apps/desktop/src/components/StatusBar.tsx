import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { Health } from "../api/types";

type Status = "loading" | "ok" | "error";

export default function StatusBar() {
  const [status, setStatus] = useState<Status>("loading");
  const [ollama, setOllama] = useState<boolean | null>(null);

  const refresh = useCallback(() => {
    let cancelled = false;
    setStatus("loading");
    apiFetch("/health")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<Health>;
      })
      .then((data) => {
        if (cancelled) return;
        setStatus("ok");
        setOllama(Boolean(data.ollama));
      })
      .catch(() => {
        if (cancelled) return;
        setStatus("error");
        setOllama(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => refresh(), [refresh]);

  return (
    <footer className="status-bar">
      <span className={`status-dot ${status === "ok" ? "ok" : status === "error" ? "bad" : ""}`} />
      <span>{status === "ok" ? "本地服务已连接" : status === "error" ? "本地服务未连接" : "正在连接本地服务…"}</span>
      <span className="status-sep">·</span>
      <span className={`status-dot ${ollama === true ? "ok" : ollama === false ? "bad" : ""}`} />
      <span>
        {ollama === true ? "Ollama 已连接" : ollama === false ? "Ollama 未启动" : "Ollama 状态未知"}
      </span>
      <button type="button" className="link-button" onClick={() => refresh()}>
        刷新
      </button>
    </footer>
  );
}
