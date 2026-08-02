import hashlib
from dataclasses import dataclass

from .parsers import ParsedSection


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
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
) -> list[Chunk]:
    if overlap >= chunk_size:
        raise ValueError("chunk overlap must be smaller than chunk size")

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
                        chunk_id=f"{document_id}:chunk:{index:05d}",
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
