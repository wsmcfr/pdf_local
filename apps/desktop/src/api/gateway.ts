import { apiFetch } from "./client";
import type { EmbeddingConfig, EmbeddingMode, GatewayConfig } from "./types";

export type GatewayPatch = Partial<
  Pick<GatewayConfig, "enabled" | "rag_mode" | "provider_id" | "mcp_enabled">
>;

export type EmbeddingPatch = {
  mode: EmbeddingMode;
  provider_id?: string;
  model?: string;
};

export type EmbeddingTestResult = {
  ok: boolean;
  mode?: string;
  dimension?: number;
  fallback?: string;
  reason?: string;
};

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Fall through to the HTTP status.
  }
  return `HTTP ${response.status}`;
}

export async function getGatewayConfig(): Promise<GatewayConfig> {
  return read<GatewayConfig>(await apiFetch("/gateway/config"));
}

export async function updateGatewayConfig(
  patch: GatewayPatch,
): Promise<GatewayConfig> {
  return read<GatewayConfig>(
    await apiFetch("/gateway/config", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  );
}

export async function rotateGatewayToken(): Promise<string> {
  const body = await read<{ token: string }>(
    await apiFetch("/gateway/token/rotate", { method: "POST" }),
  );
  return body.token;
}

export async function getEmbeddingConfig(): Promise<EmbeddingConfig> {
  return read<EmbeddingConfig>(await apiFetch("/embedding/config"));
}

export async function updateEmbeddingConfig(
  patch: EmbeddingPatch,
): Promise<EmbeddingConfig> {
  return read<EmbeddingConfig>(
    await apiFetch("/embedding/config", {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),
  );
}

export async function testEmbedding(): Promise<EmbeddingTestResult> {
  return read<EmbeddingTestResult>(
    await apiFetch("/embedding/test", { method: "POST" }),
  );
}
