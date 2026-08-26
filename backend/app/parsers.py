import re
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


def _split_blocks(normalized: str) -> list[str]:
    """按空行分段，但围栏代码块内部的空行不作为分隔。

    否则一段 ``` 代码块会被中间的空行拦腰截断，产出两个各自不完整的分块。
    """

    blocks: list[str] = []
    current: list[str] = []
    fenced = False
    for line in normalized.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            current.append(line)
            continue
        if not fenced and not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return [block.strip() for block in blocks if block.strip()]


def _attach_code_to_context(blocks: list[str]) -> list[str]:
    """把纯代码块并入前一段说明文字。

    独立成段的命令行没有任何自然语言描述，向量检索几乎不可能命中它——
    "怎么校验备份包"与一段 shell 命令之间没有可匹配的语义。并入前文之后，
    命令随其说明或小标题一起被编码，才具备被检索到的可能。
    """

    merged: list[str] = []
    for block in blocks:
        if block.startswith("```") and merged:
            merged[-1] = f"{merged[-1]}\n\n{block}"
        else:
            merged.append(block)
    return merged


_LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+")
LIST_SPLIT_MIN_ITEMS = 3
LIST_SPLIT_MIN_CHARS = 200


def _split_long_lists(blocks: list[str]) -> list[str]:
    """把多要点的长列表段落按条目拆开。

    一个塞了五六个要点的段落只会得到一个被稀释的向量：问权限、问审计、问发布触发，
    三个互不相干的信息需求指向同一个模糊表示。含代码块的段落不拆，避免把刚并入
    上下文的命令又切碎。
    """

    result: list[str] = []
    for block in blocks:
        lines = block.split("\n")
        starts = [index for index, line in enumerate(lines) if _LIST_ITEM.match(line)]
        if (
            "```" in block
            or len(starts) < LIST_SPLIT_MIN_ITEMS
            or len(block) < LIST_SPLIT_MIN_CHARS
        ):
            result.append(block)
            continue
        if starts[0] > 0 and (lead := "\n".join(lines[: starts[0]]).strip()):
            result.append(lead)
        bounds = [*starts, len(lines)]
        for begin, end in zip(bounds[:-1], bounds[1:], strict=True):
            if item := "\n".join(lines[begin:end]).strip():
                result.append(item)
    return result


def _text_sections(raw: str) -> list[ParsedSection]:
    # 曾尝试给每段补上所属小节标题以缓解长段落的语义稀释，实测有害：同一小节下的
    # 段落因共享标题前缀而向量趋同，区分度下降，改写集召回 recall@5 从 0.5345
    # 跌到 0.4586。长段落问题需要真正的按要点切分来解决，而那要求重做段落级标注。
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = _split_long_lists(_attach_code_to_context(_split_blocks(normalized)))
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
