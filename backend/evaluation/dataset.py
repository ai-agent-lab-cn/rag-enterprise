import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class EvaluationChunk(BaseModel):
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    text: str = Field(min_length=1)


class EvaluationQuery(BaseModel):
    query_id: str = Field(pattern=r"^q\d{3}$")
    question: str = Field(min_length=2)
    relevant_chunk_ids: list[str] = Field(min_length=1)


class EvaluationDataset(BaseModel):
    dataset_id: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    language: str = "zh-CN"
    description: str = Field(min_length=1)
    chunks: list[EvaluationChunk] = Field(min_length=1)
    queries: list[EvaluationQuery] = Field(min_length=20)

    @model_validator(mode="after")
    def validate_references(self) -> "EvaluationDataset":
        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        query_ids = [query.query_id for query in self.queries]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("评测集包含重复的 chunk_id")
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("评测集包含重复的 query_id")
        known_chunk_ids = set(chunk_ids)
        for query in self.queries:
            unknown = set(query.relevant_chunk_ids) - known_chunk_ids
            if unknown:
                raise ValueError(f"{query.query_id} 引用了不存在的分块：{sorted(unknown)}")
        return self


def load_dataset(path: Path) -> EvaluationDataset:
    """读取并校验一个版本化检索评测集。"""

    return EvaluationDataset.model_validate(json.loads(path.read_text(encoding="utf-8")))
