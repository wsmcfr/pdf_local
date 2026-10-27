import { apiFetch } from "./client";
import type { Citation, SourceChunk } from "./types";

export type AskEvent =
  | { event: "retrieval"; data: { chunks: SourceChunk[] } }
  | { event: "token"; data: { text: string } }
  | { event: "citations"; data: { citations: Citation[]; invalid: Citation[] } }
  | { event: "done"; data: { text: string; model: string | null } }
  | { event: "error"; data: { code?: string; message: string } };

type AskEventName = AskEvent["event"];

/**
 * POST the question and forward every server-sent event to ``onEvent``.
 *
 * The backend emits ``retrieval``, ``token``, ``citations``, ``done`` and
 * ``error`` frames; token payloads are ``{"text": "..."}``.
 */
export async function streamAsk(
  question: string,
  onEvent: (event: AskEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch("/ask", {
    method: "POST",
    body: JSON.stringify({ question }),
    signal,
  });
  if (!response.ok) {
    throw new Error(`问答请求失败（HTTP ${response.status}）`);
  }
  if (!response.body) {
    throw new Error("当前环境不支持流式回答");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");

    let index = buffer.indexOf("\n\n");
    while (index !== -1) {
      const raw = buffer.slice(0, index);
      buffer = buffer.slice(index + 2);
      const frame = parseFrame(raw);
      if (frame) onEvent(frame);
      index = buffer.indexOf("\n\n");
    }
  }

  const tail = parseFrame(buffer.trim());
  if (tail) onEvent(tail);
}

function parseFrame(raw: string): AskEvent | null {
  let event: AskEventName = "message" as AskEventName;
  const dataLines: string[] = [];

  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim() as AskEventName;
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }

  if (dataLines.length === 0) return null;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  if (!isKnownEvent(event)) return null;
  return { event, data: payload } as AskEvent;
}

function isKnownEvent(event: string): event is AskEventName {
  return (
    event === "retrieval" ||
    event === "token" ||
    event === "citations" ||
    event === "done" ||
    event === "error"
  );
}
