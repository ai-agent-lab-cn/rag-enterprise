from io import BytesIO
from zipfile import ZipFile

import pytest

from backend.app.errors import AppError
from backend.app.parsers import REGISTRY, parse_structured_document


def _zip(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_registry_exposes_all_v54_formats() -> None:
    assert {".md", ".txt", ".pdf", ".html", ".htm", ".docx", ".xlsx", ".csv"} <= REGISTRY.extensions


def test_markdown_preserves_heading_path() -> None:
    parsed = parse_structured_document("guide.md", "# 部署\n\n生产环境说明".encode())
    assert parsed.parser_name == "text"
    assert parsed.sections[1].heading_path == ("部署",)


def test_html_extracts_heading_list_and_table() -> None:
    parsed = parse_structured_document(
        "page.html", b"<h1>Guide</h1><p>Body</p><li>Item</li><table><td>Cell</td></table>"
    )
    assert [node.node_type for node in parsed.nodes] == ["heading", "paragraph", "list", "table"]


def test_csv_preserves_row_location() -> None:
    parsed = parse_structured_document("data.csv", b"name,value\nRAG,42")
    assert parsed.sections[0].sheet_name == "CSV"
    assert parsed.sections[0].row_start == 2


def test_docx_extracts_paragraph() -> None:
    content = _zip(
        {
            "word/document.xml": """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>企业知识</w:t></w:r></w:p></w:body></w:document>"""
        }
    )
    assert parse_structured_document("guide.docx", content).sections[0].text == "企业知识"


def test_unknown_format_is_stable_error() -> None:
    with pytest.raises(AppError) as raised:
        parse_structured_document("archive.zip", b"content")
    assert raised.value.code == "UNSUPPORTED_FORMAT"
