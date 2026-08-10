from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID


class DocumentInfo(BaseModel):
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    document_id: str
    filename: str
    chunk_count: int
    status: str = "ready"


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    retrieve_k: int = Field(default=10, ge=1, le=50)
    rerank_k: int = Field(default=5, ge=1, le=20)


class Source(BaseModel):
    chunk_id: str
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    document_id: str
    filename: str
    page: int | None = None
    paragraph: int
    chunk_index: int
    char_count: int
    summary: str
    text: str
    retrieval_score: float
    rerank_score: float


class QueryResponse(BaseModel):
    answer: str
    sources: list[Source]
    model: str
    latency_ms: dict[str, float]


class HealthResponse(BaseModel):
    status: str
    collection_ready: bool
    generation_ready: bool
    models: dict[str, str]


class EvaluationMetricResponse(BaseModel):
    value: float
    threshold: float
    baseline: float | None
    passed: bool
    regressed: bool


class EvaluationReportSummary(BaseModel):
    report_id: str
    dataset_id: str
    dataset_version: str
    commit: str
    run_at: datetime
    models: dict[str, str]
    passed: bool


class EvaluationReportResponse(EvaluationReportSummary):
    parameters: dict[str, int | float | str | bool]
    query_count: int
    recall_at_5: EvaluationMetricResponse
    vector_mrr: EvaluationMetricResponse
    rerank_mrr: EvaluationMetricResponse


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
