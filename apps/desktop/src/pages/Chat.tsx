import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../api/client";
import { streamAsk, type AskEvent } from "../api/stream";
import type { Citation, DocumentRow, SourceChunk } from "../api/types";
import CitationPanel from "../components/CitationPanel";
import PageViewer from "../components/PageViewer";

function describeError(data: { code?: string; message: string }): string {
  const messages: Record<string, string> = {
    PROVIDER_NOT_CONFIGURED:
      "尚未配置问答 Provider。请到设置页添加 Provider、填写密钥并选择模型。",
    INVALID_PROVIDER_CONFIG: "Provider 配置不完整，请在设置页检查类型与地址。",
    UPSTREAM_AUTH_ERROR: "Provider 密钥无效或没有访问权限。",
    UPSTREAM_RATE_LIMIT: "Provider 请求过于频繁，请稍后重试。",
    UPSTREAM_TIMEOUT: "Provider 响应超时，请稍后重试。",
    UPSTREAM_NOT_FOUND: "Provider 接口地址不存在，请检查 Base URL。",
    UPSTREAM_ERROR: "Provider 请求失败，请稍后重试。",
    RETRIEVAL_FAILED: "检索失败，请稍后重试。",
    LLM_ERROR: "生成回答失败，请稍后重试。",
    QUESTION_REQUIRED: "请输入问题。",
  };
  return messages[data.code ?? ""] ?? data.message;
}

function isNoAnswer(text: string, citations: Citation[]): boolean {
  return citations.length === 0 && (text.trim() === "" || text.includes("资料中没有找到"));
}

export default function Chat() {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<SourceChunk[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [invalid, setInvalid] = useState<Citation[]>([]);
  const [confidence, setConfidence] = useState<Record<string, number>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [answered, setAnswered] = useState(false);
  const [selected, setSelected] = useState<SourceChunk | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiFetch("/documents")
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<DocumentRow[]>;
      })
      .then((rows) => {
        if (cancelled) return;
        const map: Record<string, number> = {};
        for (const row of rows) {
          if (row.ocr_confidence !== null) map[row.file_name] = row.ocr_confidence;
        }
        setConfidence(map);
      })
      .catch(() => {
        if (!cancelled) setConfidence({});
      });

    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, []);

  async function submit() {
    const text = question.trim();
    if (!text || busy) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setBusy(true);
    setAnswer("");
    setSources([]);
    setCitations([]);
    setInvalid([]);
    setError("");
    setAnswered(false);
    setSelected(null);

    try {
      await streamAsk(
        text,
        (event: AskEvent) => {
          if (event.event === "retrieval") setSources(event.data.chunks);
          if (event.event === "token") setAnswer((prev) => prev + event.data.text);
          if (event.event === "citations") {
            setCitations(event.data.citations);
            setInvalid(event.data.invalid);
          }
          if (event.event === "done") setAnswered(true);
          if (event.event === "error") setError(describeError(event.data));
        },
        controller.signal,
      );
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof Error
            ? `无法连接本地问答服务：${cause.message}`
            : "问答请求失败",
        );
      }
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        setBusy(false);
      }
    }
  }

  const noAnswer = answered && !error && isNoAnswer(answer, citations);

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>问答</h1>
          <p className="muted">回答只依据已导入的资料，并标注原文页码。</p>
        </div>
      </header>

      <div className="chat-layout">
        <div className="chat-main">
          <form
            className="ask-form"
            onSubmit={(event) => {
              event.preventDefault();
              void submit();
            }}
          >
            <textarea
              rows={3}
              value={question}
              placeholder="输入问题，例如：这份报告的主要结论是什么？"
              onChange={(event) => setQuestion(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  void submit();
                }
              }}
            />
            <div className="ask-actions">
              <span className="muted small">Ctrl+Enter 发送</span>
              <button
                type="submit"
                className="primary"
                disabled={busy || question.trim() === ""}
              >
                {busy ? "生成中…" : "提问"}
              </button>
            </div>
          </form>

          {error && <p className="notice error">{error}</p>}
          {noAnswer && (
            <p className="notice warn">
              资料中没有找到答案。可以换一种问法，或先把相关文档导入资料库。
            </p>
          )}

          <article className={`answer${busy && !answer ? " answer-pending" : ""}`}>
            {answer ? (
              answer
            ) : busy ? (
              <span className="muted">正在检索资料并生成回答…</span>
            ) : (
              <span className="muted">回答会显示在这里。</span>
            )}
          </article>

          {(answered || citations.length > 0 || invalid.length > 0) && (
            <CitationPanel
              citations={citations}
              invalid={invalid}
              sources={sources}
              confidence={confidence}
              selected={
                selected
                  ? { documentId: selected.document_id, pageNo: selected.page_no }
                  : null
              }
              onSelect={setSelected}
            />
          )}
        </div>

        <PageViewer
          documentId={selected?.document_id ?? null}
          pageNo={selected?.page_no ?? null}
          title={
            selected
              ? `${selected.file_name} · ${
                  selected.mime === "application/pdf"
                    ? `第 ${selected.page_no} 页`
                    : `§${selected.page_no}`
                }`
              : undefined
          }
        />
      </div>
    </section>
  );
}
