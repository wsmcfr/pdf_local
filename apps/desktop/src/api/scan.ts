import { open } from "@tauri-apps/plugin-dialog";
import { apiFetch } from "./client";
import type { JobRow } from "./types";

export async function pickDirectory(): Promise<string | null> {
  const selected = await open({
    directory: true,
    multiple: false,
    title: "选择资料文件夹",
  });
  return typeof selected === "string" ? selected : null;
}

export async function startScan(rootPath: string): Promise<JobRow> {
  const response = await apiFetch("/scan", {
    method: "POST",
    body: JSON.stringify({ root_path: rootPath }),
  });
  if (!response.ok) {
    throw new Error(`导入请求失败（HTTP ${response.status}）`);
  }
  return (await response.json()) as JobRow;
}
