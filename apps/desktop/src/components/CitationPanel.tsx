import type { Citation, SourceChunk } from "../api/types";

const LOW_CONFIDENCE_THRESHOLD = 0.75;

type Props = {
  citations: Citation[];
  invalid: Citation[];
  sources: SourceChunk[];
  confidence: Record<string, number>;
  selected: { documentId: string; pageNo: number } | null;
  onSelect: (source: SourceChunk) => void;
};

function citationLabel(citation: Citation): string {
  const mark = citation.kind === "markdown" ? "§" : "p.";
  return `${citation.file_name} ${mark}${citation.page_no}`;
}

export default function CitationPanel({
  citations,
  invalid,
  sources,
  confidence,
  selected,
  onSelect,
}: Props) {
  const lowConfidenceNames = Array.from(
    new Set(
      citations
        .map((citation) => citation.file_name)
        .filter((name) => {
          const value = confidence[name];
          return value !== undefined && value < LOW_CONFIDENCE_THRESHOLD;
        }),
    ),
  );

  return (
    <section className="citation-panel">
      <header className="panel-header">
        <h3>引用</h3>
        <span className="muted">{citations.length} 条</span>
      </header>

      {lowConfidenceNames.length > 0 && (
        <p className="notice warn small">
          低置信度提示：{lowConfidenceNames.join("、")} 的 OCR 平均置信度低于 75%，请核对原文。
        </p>
      )}

      {citations.length === 0 ? (
        <p className="muted small">本次回答没有可验证的引用。</p>
      ) : (
        <ul className="citation-list">
          {citations.map((citation, index) => {
            const source = sources.find(
              (item) =>
                item.file_name === citation.file_name &&
                item.page_no === citation.page_no,
            );
            const isLow =
              confidence[citation.file_name] !== undefined &&
              confidence[citation.file_name] < LOW_CONFIDENCE_THRESHOLD;
            const isSelected =
              source !== undefined &&
              selected?.documentId === source.document_id &&
              selected?.pageNo === source.page_no;

            return (
              <li key={`${citation.file_name}-${citation.page_no}-${index}`}>
                <button
                  type="button"
                  className={`citation-item${isSelected ? " selected" : ""}`}
                  disabled={!source}
                  onClick={() => source && onSelect(source)}
                  title={source ? "查看原文页" : "引用未匹配到检索片段"}
                >
                  <span>{citationLabel(citation)}</span>
                  {isLow && <span className="tag warn">低置信度</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {invalid.length > 0 && (
        <div className="invalid-citations">
          <h4>未通过校验的引用</h4>
          <p className="muted small">以下来源没有出现在检索片段中，可能是模型编造的。</p>
          <ul>
            {invalid.map((citation, index) => (
              <li key={`${citation.file_name}-${citation.page_no}-${index}`}>
                {citationLabel(citation)}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
