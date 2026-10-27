import { apiFetch } from "./client";
import type { Preset, Provider, ProviderKind, ProviderModel } from "./types";

export type ProviderInput = {
  name: string;
  kind: ProviderKind;
  base_url: string;
  model?: string;
  enabled?: boolean;
  api_key?: string;
  extra?: Record<string, unknown>;
};

export type ProviderTestResult = {
  ok: boolean;
  model?: string;
  latency_ms?: number;
  error?: string;
};

async function read<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as T;
}

async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown; error?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (typeof body.error === "string") return body.error;
  } catch {
    // Fall through to the HTTP status.
  }
  return `HTTP ${response.status}`;
}

export async function listProviders(): Promise<Provider[]> {
  return read<Provider[]>(await apiFetch("/providers"));
}

export async function createProvider(input: ProviderInput): Promise<Provider> {
  return read<Provider>(
    await apiFetch("/providers", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  );
}

export async function updateProvider(
  id: string,
  input: Partial<ProviderInput>,
): Promise<Provider> {
  return read<Provider>(
    await apiFetch(`/providers/${id}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),
  );
}

export async function deleteProvider(id: string): Promise<void> {
  await read<{ ok: boolean }>(
    await apiFetch(`/providers/${id}`, { method: "DELETE" }),
  );
}

export async function putProviderKey(id: string, apiKey: string): Promise<void> {
  await read<{ ok: boolean }>(
    await apiFetch(`/providers/${id}/key`, {
      method: "PUT",
      body: JSON.stringify({ api_key: apiKey }),
    }),
  );
}

export async function probeProviderModels(id: string): Promise<ProviderModel[]> {
  const body = await read<{ models: ProviderModel[] }>(
    await apiFetch(`/providers/${id}/models`, { method: "POST" }),
  );
  return body.models;
}

export async function testProvider(id: string): Promise<ProviderTestResult> {
  const response = await apiFetch(`/providers/${id}/test`, { method: "POST" });
  let body: Partial<ProviderTestResult> = {};
  try {
    body = (await response.json()) as Partial<ProviderTestResult>;
  } catch {
    // Non-JSON error body: report the status only.
  }
  if (!response.ok) {
    return {
      ok: false,
      model: body.model,
      latency_ms: body.latency_ms,
      error: body.error ?? (await errorDetailFromBody(body, response.status)),
    };
  }
  return {
    ok: body.ok !== false,
    model: body.model,
    latency_ms: body.latency_ms,
    error: body.error,
  };
}

function errorDetailFromBody(
  body: Partial<ProviderTestResult> & { detail?: unknown },
  status: number,
): string {
  if (typeof body.detail === "string") return body.detail;
  return `HTTP ${status}`;
}

export async function listPresets(): Promise<Preset[]> {
  return read<Preset[]>(await apiFetch("/providers/presets"));
}
