from pathlib import Path

from app.utils.paths import is_supported


def test_office_formats_are_supported():
    for name in ["a.docx", "b.pptx", "c.xlsx", "d.md", "e.pdf"]:
        assert is_supported(Path(name))


def test_legacy_office_formats_are_not_supported_yet():
    assert not is_supported(Path("old.doc"))
    assert not is_supported(Path("old.ppt"))
