import pytest

from backend.app.errors import AppError
from backend.app.parsers import parse_document


def test_markdown_paragraphs_are_numbered() -> None:
    sections = parse_document("notes.md", "第一段\n\n第二段".encode())
    assert [section.paragraph for section in sections] == [0, 1]
    assert sections[1].text == "第二段"


@pytest.mark.parametrize("filename", ["empty.md", "empty.txt"])
def test_empty_file_is_rejected(filename: str) -> None:
    with pytest.raises(AppError) as error:
        parse_document(filename, b"")
    assert error.value.code == "EMPTY_FILE"


def test_invalid_utf8_is_rejected() -> None:
    with pytest.raises(AppError) as error:
        parse_document("bad.txt", b"\xff")
    assert error.value.code == "INVALID_ENCODING"
