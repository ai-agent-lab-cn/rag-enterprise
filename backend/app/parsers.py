from __future__ import annotations

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from io import BytesIO, StringIO
from pathlib import Path
from typing import Protocol
from zipfile import BadZipFile, ZipFile

from pypdf import PdfReader

from .errors import AppError

PARSER_SCHEMA_VERSION = "1"
SUPPORTED_EXTENSIONS = {".md", ".txt", ".pdf", ".html", ".htm", ".docx", ".xlsx", ".csv"}


@dataclass(frozen=True)
class SourceLocation:
    page_number: int | None = None
    heading_path: list[str] = field(default_factory=list)
    paragraph_index: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    column_start: int | None = None
    column_end: int | None = None
    source_url: str | None = None


@dataclass(frozen=True)
class DocumentNode:
    node_id: str
    node_type: str
    text: str
    level: int = 0
    location: SourceLocation = field(default_factory=SourceLocation)
    children: list[DocumentNode] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedSection:
    """兼容现有切片入口，同时携带 V5-4 结构和原文位置。"""

    text: str
    page: int | None
    paragraph: int
    node_id: str = ""
    node_type: str = "paragraph"
    heading_path: tuple[str, ...] = ()
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    column_start: int | None = None
    column_end: int | None = None

    def location(self) -> SourceLocation:
        return SourceLocation(
            page_number=self.page,
            heading_path=list(self.heading_path),
            paragraph_index=self.paragraph,
            sheet_name=self.sheet_name,
            row_start=self.row_start,
            row_end=self.row_end,
            column_start=self.column_start,
            column_end=self.column_end,
        )


@dataclass(frozen=True)
class ParsedDocument:
    parser_name: str
    parser_version: str
    nodes: list[DocumentNode]
    sections: list[ParsedSection]

    def tree_payload(self) -> list[dict[str, object]]:
        return [asdict(node) for node in self.nodes]


class DocumentParser(Protocol):
    name: str
    version: str
    extensions: set[str]

    def parse(self, filename: str, content: bytes) -> ParsedDocument: ...


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, DocumentParser] = {}

    def register(self, parser: DocumentParser) -> None:
        for extension in parser.extensions:
            if extension in self._parsers:
                raise ValueError(f"parser already registered for {extension}")
            self._parsers[extension] = parser

    def resolve(self, filename: str) -> DocumentParser:
        extension = Path(filename).suffix.lower()
        if parser := self._parsers.get(extension):
            return parser
        raise AppError(
            "UNSUPPORTED_FORMAT",
            "仅支持 Markdown、TXT、PDF、HTML、DOCX、XLSX 和 CSV 文件。",
            415,
        )

    @property
    def extensions(self) -> set[str]:
        return set(self._parsers)


def _split_blocks(normalized: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    fenced = False
    for line in normalized.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


_HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")


def _attach_code_to_context(blocks: list[str]) -> list[str]:
    merged: list[str] = []
    for block in blocks:
        if block.startswith("```") and merged:
            merged[-1] = f"{merged[-1]}\n\n{block}"
        else:
            merged.append(block)
    return merged


def _split_long_lists(blocks: list[str]) -> list[str]:
    result: list[str] = []
    for block in blocks:
        lines = block.split("\n")
        starts = [index for index, line in enumerate(lines) if _LIST_ITEM.match(line)]
        if "```" in block or len(starts) < 3 or len(block) < 200:
            result.append(block)
            continue
        if starts[0] > 0 and (lead := "\n".join(lines[: starts[0]]).strip()):
            result.append(lead)
        bounds = [*starts, len(lines)]
        for begin, end in zip(bounds[:-1], bounds[1:], strict=True):
            if item := "\n".join(lines[begin:end]).strip():
                result.append(item)
    return result


def _text_document(raw: str, parser_name: str, parser_version: str) -> ParsedDocument:
    heading_path: list[str] = []
    nodes: list[DocumentNode] = []
    sections: list[ParsedSection] = []
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = _split_long_lists(_attach_code_to_context(_split_blocks(normalized)))
    for block in blocks:
        match = _HEADING.match(block.splitlines()[0])
        node_type = "heading" if match else "code" if block.startswith("```") else "paragraph"
        level = len(match.group(1)) if match else 0
        text = match.group(2).strip() if match and len(block.splitlines()) == 1 else block
        if match:
            heading_path = heading_path[: level - 1] + [match.group(2).strip()]
        paragraph = len(sections)
        node_id = f"node_{paragraph:05d}"
        location = SourceLocation(heading_path=list(heading_path), paragraph_index=paragraph)
        nodes.append(DocumentNode(node_id, node_type, text, level, location))
        sections.append(ParsedSection(text, None, paragraph, node_id, node_type, tuple(heading_path)))
    return ParsedDocument(parser_name, parser_version, nodes, sections)


class TextParser:
    name = "text"
    version = "2.0"
    extensions = {".md", ".txt"}

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        try:
            return _text_document(content.decode("utf-8"), self.name, self.version)
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_ENCODING", "文本文件必须使用 UTF-8 编码。", 422) from exc


class PdfParser:
    name = "pypdf"
    version = "2.0"
    extensions = {".pdf"}

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise AppError("PASSWORD_PROTECTED", "PDF 已加密，无法解析。", 422)
            nodes: list[DocumentNode] = []
            sections: list[ParsedSection] = []
            for page_number, page in enumerate(reader.pages, start=1):
                for block in _split_blocks(page.extract_text() or ""):
                    paragraph = len(sections)
                    node_id = f"node_{paragraph:05d}"
                    location = SourceLocation(page_number=page_number, paragraph_index=paragraph)
                    nodes.append(DocumentNode(node_id, "paragraph", block, location=location))
                    sections.append(ParsedSection(block, page_number, paragraph, node_id))
            return ParsedDocument(self.name, self.version, nodes, sections)
        except AppError:
            raise
        except Exception as exc:
            raise AppError("FILE_CORRUPTED", "PDF 无法解析或已损坏。", 422) from exc


class _HTMLExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[tuple[str, int, str]] = []
        self._tag = ""
        self._level = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "li", "pre", "td", "th"} or re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self._tag = tag
            self._level = int(tag[1]) if tag.startswith("h") else 0

    def handle_endtag(self, tag: str) -> None:
        if tag == self._tag:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._tag:
            self._buffer.append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buffer).split())
        if text:
            kind = (
                "heading"
                if self._tag.startswith("h")
                else "list"
                if self._tag == "li"
                else "table"
                if self._tag in {"td", "th"}
                else "code"
                if self._tag == "pre"
                else "paragraph"
            )
            self.blocks.append((kind, self._level, text))
        self._tag, self._level, self._buffer = "", 0, []


class HtmlDocumentParser:
    name = "html"
    version = "1.0"
    extensions = {".html", ".htm"}

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        try:
            extractor = _HTMLExtractor()
            extractor.feed(content.decode("utf-8"))
            extractor._flush()
        except (UnicodeDecodeError, ValueError) as exc:
            raise AppError("FILE_CORRUPTED", "HTML 无法解析。", 422) from exc
        return _blocks_document(extractor.blocks, self.name, self.version)


class DocxParser:
    name = "docx"
    version = "1.0"
    extensions = {".docx"}

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        try:
            with ZipFile(BytesIO(content)) as archive:
                root = ET.fromstring(archive.read("word/document.xml"))
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            blocks: list[tuple[str, int, str]] = []
            for element in root.findall(".//w:body/*", ns):
                text = "".join(node.text or "" for node in element.findall(".//w:t", ns)).strip()
                if not text:
                    continue
                if element.tag.endswith("}tbl"):
                    blocks.append(("table", 0, text))
                    continue
                style = element.find(".//w:pStyle", ns)
                value = style.attrib.get(f"{{{ns['w']}}}val", "") if style is not None else ""
                level_match = re.search(r"([1-6])$", value)
                blocks.append(
                    ("heading", int(level_match.group(1)), text) if level_match else ("paragraph", 0, text)
                )
            return _blocks_document(blocks, self.name, self.version)
        except (BadZipFile, KeyError, ET.ParseError) as exc:
            raise AppError("FILE_CORRUPTED", "DOCX 无法解析或已损坏。", 422) from exc


class CsvParser:
    name = "csv"
    version = "1.0"
    extensions = {".csv"}

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        try:
            rows = list(csv.reader(StringIO(content.decode("utf-8-sig"))))
        except (UnicodeDecodeError, csv.Error) as exc:
            raise AppError("FILE_CORRUPTED", "CSV 编码或格式无效。", 422) from exc
        return _table_document(rows, "CSV", self.name, self.version)


class XlsxParser:
    name = "xlsx"
    version = "1.0"
    extensions = {".xlsx"}

    def parse(self, filename: str, content: bytes) -> ParsedDocument:
        try:
            with ZipFile(BytesIO(content)) as archive:
                shared: list[str] = []
                if "xl/sharedStrings.xml" in archive.namelist():
                    shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                    shared = [
                        "".join(t.text or "" for t in item.iter() if t.tag.endswith("}t"))
                        for item in shared_root
                    ]
                workbook = ET.fromstring(archive.read("xl/workbook.xml"))
                sheets = [
                    node.attrib.get("name", f"Sheet {index + 1}")
                    for index, node in enumerate(workbook.iter())
                    if node.tag.endswith("}sheet")
                ]
                sections: list[ParsedSection] = []
                for index, sheet_name in enumerate(sheets, start=1):
                    path = f"xl/worksheets/sheet{index}.xml"
                    if path not in archive.namelist():
                        continue
                    root = ET.fromstring(archive.read(path))
                    rows: list[list[str]] = []
                    for row in (node for node in root.iter() if node.tag.endswith("}row")):
                        values: list[str] = []
                        for cell in (node for node in row if node.tag.endswith("}c")):
                            value_node = next((node for node in cell if node.tag.endswith("}v")), None)
                            value = value_node.text or "" if value_node is not None else ""
                            if cell.attrib.get("t") == "s" and value.isdigit():
                                value = shared[int(value)]
                            values.append(value)
                        rows.append(values)
                    sections.extend(_table_document(rows, sheet_name, self.name, self.version).sections)
            normalized = [
                ParsedSection(
                    section.text,
                    None,
                    index,
                    f"node_{index:05d}",
                    "table",
                    (),
                    section.sheet_name,
                    section.row_start,
                    section.row_end,
                    section.column_start,
                    section.column_end,
                )
                for index, section in enumerate(sections)
            ]
            nodes = [
                DocumentNode(section.node_id, "table", section.text, location=section.location())
                for section in normalized
            ]
            return ParsedDocument(self.name, self.version, nodes, normalized)
        except (BadZipFile, KeyError, ET.ParseError, IndexError) as exc:
            raise AppError("FILE_CORRUPTED", "XLSX 无法解析或已损坏。", 422) from exc


def _blocks_document(blocks: list[tuple[str, int, str]], name: str, version: str) -> ParsedDocument:
    heading_path: list[str] = []
    nodes: list[DocumentNode] = []
    sections: list[ParsedSection] = []
    for kind, level, text in blocks:
        if kind == "heading":
            heading_path = heading_path[: level - 1] + [text]
        index = len(sections)
        node_id = f"node_{index:05d}"
        location = SourceLocation(heading_path=list(heading_path), paragraph_index=index)
        nodes.append(DocumentNode(node_id, kind, text, level, location))
        sections.append(ParsedSection(text, None, index, node_id, kind, tuple(heading_path)))
    return ParsedDocument(name, version, nodes, sections)


def _table_document(rows: list[list[str]], sheet: str, name: str, version: str) -> ParsedDocument:
    if not rows:
        return ParsedDocument(name, version, [], [])
    header = rows[0]
    sections: list[ParsedSection] = []
    for row_number, row in enumerate(rows[1:] or rows, start=2 if len(rows) > 1 else 1):
        values = [
            f"{header[index] if index < len(header) else f'列{index + 1}'}: {value}"
            for index, value in enumerate(row)
            if value
        ]
        if not values:
            continue
        index = len(sections)
        sections.append(
            ParsedSection(
                " | ".join(values),
                None,
                index,
                f"node_{index:05d}",
                "table",
                (),
                sheet,
                row_number,
                row_number,
                1,
                len(row),
            )
        )
    nodes = [
        DocumentNode(section.node_id, "table", section.text, location=section.location())
        for section in sections
    ]
    return ParsedDocument(name, version, nodes, sections)


REGISTRY = ParserRegistry()
for _parser in (TextParser(), PdfParser(), HtmlDocumentParser(), DocxParser(), CsvParser(), XlsxParser()):
    REGISTRY.register(_parser)


def parse_structured_document(filename: str, content: bytes) -> ParsedDocument:
    if not content:
        raise AppError("EMPTY_FILE", "上传的文件为空。", 422)
    parsed = REGISTRY.resolve(filename).parse(filename, content)
    if not parsed.sections:
        raise AppError("NO_TEXT", "文件中没有可索引的文本内容。", 422)
    return parsed


def parse_document(filename: str, content: bytes) -> list[ParsedSection]:
    return parse_structured_document(filename, content).sections
