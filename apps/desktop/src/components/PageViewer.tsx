import { useEffect, useState } from "react";
import { apiFetch } from "../api/client";
import type { PageContent } from "../api/types";

type Props = {
  documentId: string | null;
  pageNo: number | null;
  title?: string;
};

export default function PageViewer({ documentId, pageNo, title }: Props) {
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
      <header className="panel-header">
        <h3>原文</h3>
        {title && <p className="muted">{title}</p>}
      </header>

      {!hasSelection ? (
        <p className="empty small">点击左侧引用，在这里查看原文页图和 OCR 文本。</p>
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
