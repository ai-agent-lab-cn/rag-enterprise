from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID


class DocumentInfo(BaseModel):
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    document_id: str
    filename: str
    chunk_count: int
    status: str = "ready"


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("知识库名称不能为空")
        return value


class KnowledgeBaseUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=500)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("知识库名称不能为空")
        return value


class KnowledgeBaseResponse(BaseModel):
    knowledge_base_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    is_default: bool
    document_count: int
    chunk_count: int


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    retrieve_k: int = Field(default=10, ge=1, le=50)
    rerank_k: int = Field(default=5, ge=1, le=20)
    conversation_id: str | None = Field(default=None, pattern=r"^conv_[a-f0-9]{16}$")


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
    conversation_id: str | None = None
    record_id: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    model_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    prompt_version: str | None = None
    prompt_hash: str | None = None


class AnswerRecordResponse(BaseModel):
    record_id: str
    conversation_id: str
    knowledge_base_id: str
    question: str
    status: str
    answer: str | None
    sources: list[Source]
    latency_ms: dict[str, float]
    models: dict[str, str]
    model_metadata: dict[str, str | int | float | bool]
    prompt_version: str | None
    prompt_hash: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime


class ConversationSummaryResponse(BaseModel):
    conversation_id: str
    knowledge_base_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    turn_count: int
    last_status: str | None


class ConversationDetailResponse(BaseModel):
    conversation_id: str
    knowledge_base_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    records: list[AnswerRecordResponse]


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
