import { useCallback, useEffect, useState } from "react";
import {
  FilePlus,
  FolderOpen,
  MessageSquare,
  RefreshCw,
} from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { apiFetch } from "../api/client";
import {
  pickDirectory,
  pickFiles,
  startImport,
  startScan,
} from "../api/scan";
import type { DocumentRow } from "../api/types";

const STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  processing: "处理中",
  ready: "就绪",
  failed: "失败",
};

type MenuState = { x: number; y: number; doc: DocumentRow } | null;

function confidenceText(value: number | null): string {
  if (value === null) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function isLowConfidence(value: number | null): boolean {
  return value !== null && value < 0.75;
}

export default function Library() {
  const [rows, setRows] = useState<DocumentRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [importing, setImporting] = useState(false);
  const [menu, setMenu] = useState<MenuState>(null);
  const navigate = useNavigate();

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch("/documents");
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setRows((await response.json()) as DocumentRow[]);
    } catch {
      setRows([]);
      setError("无法加载资料库，请确认本地服务已启动。");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  async function importFolder() {
    setError("");
    setImporting(true);
    try {
      const selected = await pickDirectory();
      if (!selected) return;
      const job = await startScan(selected);
      navigate("/jobs", { state: { jobId: job.id } });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导入失败");
    } finally {
      setImporting(false);
    }
  }

  async function importFiles() {
    setError("");
    setImporting(true);
    try {
      const selected = await pickFiles();
      if (selected.length === 0) return;
      const job = await startImport(selected);
      navigate("/jobs", { state: { jobId: job.id } });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "导入失败");
    } finally {
      setImporting(false);
    }
  }

  function openChat(document: DocumentRow) {
    setMenu(null);
    navigate(`/chat?document=${document.id}`);
  }

  return (
    <section className="page">
      <header className="page-header">
        <div>
          <h1>资料库</h1>
          <p className="muted">
            共 {rows.length} 份资料。支持 PDF、Word、PPT、Excel、Markdown 与 TXT；
            右键文件可直接和它对话。
          </p>
        </div>
        <div className="header-actions">
          <Link className="link-button" to="/onboarding">
            首次使用引导
          </Link>
          <button type="button" className="secondary" onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />
            刷新
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => void importFolder()}
            disabled={importing}
          >
            <FolderOpen size={15} aria-hidden="true" />
            导入文件夹
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void importFiles()}
            disabled={importing}
          >
            <FilePlus size={15} aria-hidden="true" />
            {importing ? "正在创建导入任务…" : "导入文件"}
          </button>
        </div>
      </header>

      {error && <p className="notice error">{error}</p>}

      {loading ? (
        <p className="empty">正在加载资料…</p>
      ) : rows.length === 0 ? (
        <div className="empty">
          <p>还没有资料。可以选文件，也可以直接导入一个文件夹。</p>
          <div className="header-actions">
            <button
              type="button"
              className="secondary"
              onClick={() => void importFolder()}
            >
              <FolderOpen size={15} aria-hidden="true" />
              选择文件夹
            </button>
            <button
              type="button"
              className="primary"
              onClick={() => void importFiles()}
            >
              <FilePlus size={15} aria-hidden="true" />
              选择文件
            </button>
          </div>
        </div>
      ) : (
        <div className="table-shell">
          <table className="table">
            <thead>
              <tr>
                <th>文件</th>
                <th>状态</th>
                <th className="numeric">页数</th>
                <th className="numeric">OCR 置信度</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className="document-row"
                  onContextMenu={(event) => {
                    event.preventDefault();
                    setMenu({
                      x: event.clientX,
                      y: event.clientY,
                      doc: row,
                    });
                  }}
                  onDoubleClick={() => openChat(row)}
                >
                  <td title={row.path}>{row.file_name}</td>
                  <td>
                    <span className={`tag status-${row.status}`}>
                      {STATUS_LABELS[row.status] ?? row.status}
                    </span>
                    {row.error && <span className="muted small"> {row.error}</span>}
                  </td>
                  <td className="numeric">{row.page_count}</td>
                  <td className="numeric">
                    {isLowConfidence(row.ocr_confidence) ? (
                      <span className="tag warn">{confidenceText(row.ocr_confidence)}</span>
                    ) : (
                      confidenceText(row.ocr_confidence)
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {menu && (
        <div
          className="context-menu"
          style={{
            left: Math.min(menu.x, window.innerWidth - 220),
            top: Math.min(menu.y, window.innerHeight - 64),
          }}
          onClick={(event) => event.stopPropagation()}
        >
          <button type="button" onClick={() => openChat(menu.doc)}>
            <MessageSquare size={14} aria-hidden="true" />
            和这个文件对话
          </button>
        </div>
      )}
    </section>
  );
}
