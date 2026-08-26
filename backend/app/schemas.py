import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID


class AuthBootstrapRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        if not (normalized := value.strip()):
            raise ValueError("显示名称不能为空")
        return normalized


class AuthBootstrapStatus(BaseModel):
    required: bool


class AuthLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    user_id: str
    username: str
    display_name: str
    role: Literal["admin", "member"]
    active: bool
    created_at: datetime
    updated_at: datetime


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: UserResponse


class MemberCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)
    role: Literal["admin", "member"] = "member"

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        if not (normalized := value.strip()):
            raise ValueError("显示名称不能为空")
        return normalized


class MemberUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    role: Literal["admin", "member"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=128)

    @field_validator("display_name")
    @classmethod
    def normalize_optional_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (normalized := value.strip()):
            raise ValueError("显示名称不能为空")
        return normalized


class DocumentInfo(BaseModel):
    knowledge_base_id: str = DEFAULT_KNOWLEDGE_BASE_ID
    document_id: str
    filename: str
    chunk_count: int
    status: str = "ready"


class DocumentVersionResponse(BaseModel):
    document_version_id: str
    document_id: str
    filename: str
    version_number: int
    content_sha256: str
    source_file_bytes: int
    source_type: str
    status: Literal["pending", "indexing", "ready", "failed", "superseded"]
    failure_reason: str | None
    created_at: datetime
    indexed_at: datetime | None
    is_current: bool


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
    source_file_bytes: int = 0
    index_status: Literal["empty", "processing", "ready", "failed"] = "empty"
    current_user_permission: Literal["admin", "use"] = "use"
    allowed_actions: list[Literal["detail", "edit", "delete"]] = ["detail"]


class DataSourceResponse(BaseModel):
    data_source_id: str
    name: str
    source_type: Literal["file", "object_storage", "web", "connector"]
    knowledge_base_id: str
    knowledge_base_name: str
    enabled: bool
    upload_status: Literal["idle", "succeeded"]
    index_status: Literal["idle", "queued", "running", "succeeded", "failed"]
    sync_status: Literal["idle", "queued", "running", "succeeded", "failed"]
    document_count: int
    source_file_bytes: int
    last_indexed_at: datetime | None
    last_synced_at: datetime | None
    failure_reason: str | None
    updated_at: datetime
    allowed_actions: list[Literal["detail", "edit", "disable", "enable", "sync", "delete"]]


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    retrieve_k: int = Field(default=10, ge=1, le=50)
    rerank_k: int = Field(default=5, ge=1, le=20)
    conversation_id: str | None = Field(default=None, pattern=r"^conv_[a-f0-9]{16}$")

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("问题不能为空")
        if any(
            unicodedata.category(character) == "Cc" and character not in {"\n", "\t"}
            for character in normalized
        ):
            raise ValueError("问题包含不支持的控制字符")
        return normalized


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
    vector_score: float | None = None
    lexical_score: float | None = None
    retrieval_methods: list[Literal["vector", "lexical"]] = Field(default_factory=list)
    query_match_count: int = Field(default=1, ge=1)


class QueryExecutionMetadata(BaseModel):
    strategy: Literal["original", "normalized", "controlled_expansion"]
    query_count: int = Field(ge=1, le=4)
    expansion_count: int = Field(ge=0, le=3)
    fallback_used: bool = False


class QueryResponse(BaseModel):
    answer: str
    answer_status: str = "answered"
    error_code: str | None = None
    error_message: str | None = None
    sources: list[Source]
    model: str
    latency_ms: dict[str, float]
    conversation_id: str | None = None
    record_id: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    model_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    prompt_version: str | None = None
    prompt_hash: str | None = None
    query_metadata: QueryExecutionMetadata | None = None


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
    query_metadata: QueryExecutionMetadata | None = None
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
    version: str
    collection_ready: bool
    generation_ready: bool
    models: dict[str, str]


class LivenessResponse(BaseModel):
    status: Literal["alive"] = "alive"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    checks: dict[str, Literal["ok", "failed"]]


class MetricsResponse(BaseModel):
    generated_at: datetime
    requests: dict[str, Any]
    rag: dict[str, int | float]
    indexing: dict[str, int | float]


class AuditEventResponse(BaseModel):
    event_id: str
    occurred_at: datetime
    action: str
    actor_hash: str | None
    actor_role: str | None
    resource_type: str
    resource_id: str | None
    result: Literal["success", "denied", "failed"]
    request_id: str
    metadata: dict[str, str | bool | int | float]
    previous_hash: str
    event_hash: str


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
    hybrid_mrr: EvaluationMetricResponse | None = None


class AnswerEvaluationMetricResponse(EvaluationMetricResponse):
    direction: str


class AnswerEvaluationReportSummary(BaseModel):
    report_id: str
    dataset_id: str
    dataset_version: str
    commit: str
    run_at: datetime
    prompt_version: str
    models: dict[str, str]
    passed: bool


class AnswerEvaluationReportResponse(AnswerEvaluationReportSummary):
    prompt_hash: str
    parameters: dict[str, int | float | str | bool]
    case_count: int
    metrics: dict[str, AnswerEvaluationMetricResponse | None]


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
