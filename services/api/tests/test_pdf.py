import fitz

from app.ingest.pdf import extract_pdf_pages


def test_extract_text_pdf(tmp_path):
    path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello local knowledge base")
    doc.save(path)
    doc.close()

    pages = extract_pdf_pages(path)
    assert len(pages) == 1
    assert pages[0].page_no == 1
    assert "Hello local knowledge base" in pages[0].text
    assert pages[0].is_scanned is False
