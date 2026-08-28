import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, validate_knowledge_base_id
from .parsers import ParsedSection

# 切分算法本身发生变化时必须递增，否则重建无法区分“配置不同”与“实现不同”。
CHUNKING_ALGORITHM_VERSION = "v1"
_CHUNKING_VERSION_PATTERN = re.compile(r"^(v\d+)-(\d+)-(\d+)$")


def chunking_version(chunk_size: int, chunk_overlap: int) -> str:
    """描述一批 chunk 由什么切分配置产出，用于判断哪些文档需要重建。"""

    if chunk_overlap >= chunk_size:
        raise ValueError("chunk overlap must be smaller than chunk size")
    return f"{CHUNKING_ALGORITHM_VERSION}-{chunk_size}-{chunk_overlap}"


def parse_chunking_version(value: str) -> tuple[str, int, int]:
    """还原切分配置，使重建任务按入队时的目标执行而不是按当前进程配置执行。"""

    match = _CHUNKING_VERSION_PATTERN.fullmatch(value)
    if not match:
        raise ValueError(f"chunking version is invalid: {value}")
    algorithm, chunk_size, chunk_overlap = match.group(1), int(match.group(2)), int(match.group(3))
    if algorithm != CHUNKING_ALGORITHM_VERSION:
        raise ValueError(f"chunking algorithm {algorithm} is not supported by {CHUNKING_ALGORITHM_VERSION}")
    if chunk_overlap >= chunk_size:
        raise ValueError(f"chunking version is invalid: {value}")
    return algorithm, chunk_size, chunk_overlap


# 重叠切片、metadata、稳定 document/chunk ID
@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    knowledge_base_id: str
    document_id: str
    filename: str
    text: str
    page: int | None
    paragraph: int
    chunk_index: int
    char_count: int
    summary: str
    governance_metadata: dict[str, Any] = field(default_factory=dict)
    node_id: str = ""
    node_type: str = "paragraph"
    heading_path: tuple[str, ...] = ()
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None

    def metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "knowledge_base_id": self.knowledge_base_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "paragraph": self.paragraph,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
            "summary": self.summary,
            "node_id": self.node_id,
            "node_type": self.node_type,
            "heading_path": list(self.heading_path),
        }
        if self.page is not None:
            data["page"] = self.page
        if self.sheet_name is not None:
            data["sheet_name"] = self.sheet_name
        if self.row_start is not None:
            data["row_start"] = self.row_start
            data["row_end"] = self.row_end
        return {**data, **self.governance_metadata}


def stable_document_id(filename: str, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()[:20]
    safe_name = filename.strip().lower().encode("utf-8")
    name_digest = hashlib.sha256(safe_name).hexdigest()[:8]
    return f"doc_{name_digest}_{digest}"


def split_sections(
    document_id: str,
    filename: str,
    sections: list[ParsedSection],
    chunk_size: int,
    overlap: int,
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID,
    governance_metadata: dict[str, Any] | None = None,
) -> list[Chunk]:
    if overlap >= chunk_size:
        raise ValueError("chunk overlap must be smaller than chunk size")
    validate_knowledge_base_id(knowledge_base_id)

    chunks: list[Chunk] = []
    step = chunk_size - overlap
    for section in sections:
        start = 0
        while start < len(section.text):
            text = section.text[start : start + chunk_size].strip()
            if text:
                index = len(chunks)
                chunks.append(
                    Chunk(
                        # 保留 V2 默认知识库的旧 ID；新知识库加前缀，chunk_id 在全局唯一。
                        chunk_id=(
                            f"{document_id}:chunk:{index:05d}"
                            if knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID
                            else f"{knowledge_base_id}:{document_id}:chunk:{index:05d}"
                        ),
                        knowledge_base_id=knowledge_base_id,
                        document_id=document_id,
                        filename=filename,
                        text=text,
                        page=section.page,
                        paragraph=section.paragraph,
                        chunk_index=index,
                        char_count=len(text),
                        summary=text[:80].replace("\n", " "),
                        governance_metadata=dict(governance_metadata or {}),
                        node_id=section.node_id,
                        node_type=section.node_type,
                        heading_path=section.heading_path,
                        sheet_name=section.sheet_name,
                        row_start=section.row_start,
                        row_end=section.row_end,
                    )
                )
            if start + chunk_size >= len(section.text):
                break
            start += step
    return chunks
