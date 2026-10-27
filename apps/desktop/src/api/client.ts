import { invoke } from "@tauri-apps/api/core";

export type ServerInfo = {
  base_url: string;
  token: string;
};

export async function getServerInfo(): Promise<ServerInfo> {
  const info = await invoke<ServerInfo | null>("get_server_info");
  if (!info) throw new Error("sidecar is not ready");
  return info;
}

export async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const info = await getServerInfo();
  return fetch(`${info.base_url}/api/v1${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-App-Token": info.token,
      ...(init?.headers ?? {}),
    },
  });
}
