import { useEffect, useState } from "react";
import { PanelRightClose } from "lucide-react";
import { apiFetch } from "../api/client";
import type { PageContent } from "../api/types";

type Props = {
  documentId: string | null;
  pageNo: number | null;
  title?: string;
  options?: { key: string; label: string }[];
  selectedKey?: string;
  onSelectSource?: (key: string) => void;
  onClose?: () => void;
};

export default function PageViewer({
  documentId,
  pageNo,
  title,
  options,
  selectedKey,
  onSelectSource,
  onClose,
}: Props) {
  const [page, setPage] = useState<PageContent | null>(null);
  const [textError, setTextError] = useState("");
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [imageError, setImageError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    setPage(null);
    setTextError("");
    setImageUrl(null);
    setImageError("");

    if (!documentId || pageNo === null) {
      setLoading(false);
      return;
    }

    setLoading(true);
    apiFetch(`/documents/${documentId}/pages/${pageNo}`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json() as Promise<PageContent>;
      })
      .then((data) => {
        if (!cancelled) setPage(data);
      })
      .catch(() => {
        if (!cancelled) setTextError("原文文本不可用");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    apiFetch(`/documents/${documentId}/pages/${pageNo}/image`)
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        objectUrl = url;
        setImageUrl(url);
      })
      .catch(() => {
        if (!cancelled) setImageError("该来源没有页图预览（页图仅支持 PDF）。");
      });

    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [documentId, pageNo]);

  const hasSelection = Boolean(documentId) && pageNo !== null;

  return (
    <aside className="page-viewer">
      <header className="panel-header viewer-head">
        <div className="viewer-head-text">
          <h3>原文</h3>
          {title && <p className="muted small viewer-title">{title}</p>}
        </div>
        {onClose && (
          <button
            type="button"
            className="icon-button"
            onClick={onClose}
            aria-label="收起原文"
            title="收起原文"
          >
            <PanelRightClose size={15} aria-hidden="true" />
          </button>
        )}
      </header>

      {options && options.length > 1 && (
        <label className="field viewer-select">
          <span>引用文件</span>
          <select
            value={selectedKey ?? ""}
            onChange={(event) => onSelectSource?.(event.target.value)}
          >
            <option value="">选择要查看的文件…</option>
            {options.map((option) => (
              <option key={option.key} value={option.key}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      )}

      {!hasSelection ? (
        <p className="empty small">
          点击回答中的文件名或引用列表查看原文页；回答完成后会自动定位到第一条引用。
        </p>
      ) : (
        <>
          {imageUrl ? (
            <img className="page-image" src={imageUrl} alt={`第 ${pageNo} 页`} />
          ) : (
            <div className="page-image-placeholder">
              {loading ? "页图加载中…" : imageError || "页图不可用"}
            </div>
          )}

          {page && (
            <div className="page-meta">
              <span className="tag">{page.is_scanned ? "OCR 识别" : "PDF 文本层"}</span>
              {page.ocr_confidence !== null && (
                <span className={`tag ${page.low_confidence ? "warn" : ""}`}>
                  OCR {(page.ocr_confidence * 100).toFixed(1)}%
                </span>
              )}
              {page.low_confidence ? <span className="tag warn">低置信度</span> : null}
            </div>
          )}

          <div className="page-text">
            <h4>OCR / 提取文本</h4>
            {textError ? (
              <p className="muted">{textError}</p>
            ) : (
              <pre>{page?.text?.trim() ? page.text : loading ? "加载中…" : "（本页没有文本）"}</pre>
            )}
          </div>
        </>
      )}
    </aside>
  );
}
