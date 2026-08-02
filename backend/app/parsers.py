from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from .errors import AppError

SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf"}

# MD/TXT/PDF 解析和错误处理
@dataclass(frozen=True)
class ParsedSection:
    text: str
    page: int | None
    paragraph: int


def _text_sections(raw: str) -> list[ParsedSection]:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [part.strip() for part in normalized.split("\n\n") if part.strip()]
    return [ParsedSection(text=text, page=None, paragraph=index) for index, text in enumerate(paragraphs)]


def parse_document(filename: str, content: bytes) -> list[ParsedSection]:
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise AppError("UNSUPPORTED_FILE", "仅支持 Markdown、TXT 和 PDF 文件。", 415)
    if not content:
        raise AppError("EMPTY_FILE", "上传的文件为空。")

    if extension in {".md", ".txt"}:
        try:
            sections = _text_sections(content.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_ENCODING", "文本文件必须使用 UTF-8 编码。") from exc
    else:
        try:
            reader = PdfReader(BytesIO(content))
            sections = []
            paragraph = 0
            for page_number, page in enumerate(reader.pages, start=1):
                for section in _text_sections(page.extract_text() or ""):
                    sections.append(ParsedSection(section.text, page_number, paragraph))
                    paragraph += 1
        except Exception as exc:
            raise AppError("INVALID_PDF", "PDF 无法解析或已损坏。") from exc

    if not sections:
        raise AppError("NO_TEXT", "文件中没有可索引的文本内容。")
    return sections
