import hashlib
from dataclasses import dataclass

from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, validate_knowledge_base_id
from .parsers import ParsedSection


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

    def metadata(self) -> dict[str, str | int]:
        data: dict[str, str | int] = {
            "knowledge_base_id": self.knowledge_base_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "paragraph": self.paragraph,
            "chunk_index": self.chunk_index,
            "char_count": self.char_count,
            "summary": self.summary,
        }
        if self.page is not None:
            data["page"] = self.page
        return data


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
                        # 保留 V2 默认知识库的旧 ID；新知识库加前缀以避免 Chroma 全局 ID 冲突。
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
                    )
                )
            if start + chunk_size >= len(section.text):
                break
            start += step
    return chunks
