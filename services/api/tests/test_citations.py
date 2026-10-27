from app.qa.citations import (
    citation_label,
    extract_citations,
    validate_citations,
)

RETRIEVED = [
    {"document_id": "d1", "file_name": "报告.pdf", "page_no": 12},
    {"document_id": "d1", "file_name": "报告.pdf", "page_no": 13},
]


def test_extract_pdf_citation():
    text = "根据资料，营收增长 20% [报告.pdf p.12]。"
    citations = extract_citations(text)
    assert citations[0]["file_name"] == "报告.pdf"
    assert citations[0]["page_no"] == 12
    assert citations[0]["kind"] == "pdf"


def test_extract_markdown_citation():
    text = "见配置说明 [笔记.md §3]。"
    citations = extract_citations(text)
    assert citations[0]["file_name"] == "笔记.md"
    assert citations[0]["page_no"] == 3
    assert citations[0]["kind"] == "markdown"


def test_extract_citations_keeps_document_order():
    text = "[b.md §2] 与 [a.pdf p.4]"
    citations = extract_citations(text)
    assert [citation["file_name"] for citation in citations] == ["b.md", "a.pdf"]


def test_validate_rejects_unknown_page():
    text = "结论 [报告.pdf p.99]。"
    citations = extract_citations(text)
    valid, invalid = validate_citations(citations, RETRIEVED)
    assert valid == []
    assert invalid[0]["page_no"] == 99


def test_validate_rejects_unknown_file():
    text = "结论 [其他.pdf p.12]。"
    citations = extract_citations(text)
    valid, invalid = validate_citations(citations, RETRIEVED)
    assert valid == []
    assert invalid[0]["file_name"] == "其他.pdf"


def test_validate_accepts_retrieved_page():
    text = "结论 [报告.pdf p.13]。"
    citations = extract_citations(text)
    valid, invalid = validate_citations(citations, RETRIEVED)
    assert valid == citations
    assert invalid == []


def test_citation_label_matches_document_kind():
    assert citation_label("报告.pdf", 12) == "报告.pdf p.12"
    assert citation_label("报告.PDF", 12, "application/pdf") == "报告.PDF p.12"
    assert citation_label("笔记.md", 3) == "笔记.md §3"
