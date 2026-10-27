import fitz

from app.ingest.ocr import ocr_page


def test_ocr_returns_text_and_confidence(monkeypatch, tmp_path):
    pdf_path = tmp_path / "dummy.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()

    class FakeEngine:
        def __call__(self, _image):
            return (
                [
                    ([[0, 0], [10, 0], [10, 10], [0, 10]], "测试文本", 0.9),
                    ([[0, 12], [10, 12], [10, 22], [0, 22]], "Hello", 0.8),
                ],
                None,
            )

    monkeypatch.setattr("app.ingest.ocr.get_engine", lambda: FakeEngine())
    text, confidence = ocr_page(pdf_path, 1, 150)
    assert "测试文本" in text
    assert "Hello" in text
    assert confidence == 0.85
