import re
import unicodedata
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    category: str = "未分类"
    category_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_type: str = "file"
    created_at: datetime | None = None
    source_system: str = "upload"
    external_resource_id: str | None = None
    owner_user_id: str | None = None
    department: str | None = None
    sensitivity: Literal["public", "internal", "confidential", "restricted"] = "internal"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    retrieval_status: Literal["searchable", "expired", "deleted"] = "searchable"
    acl_version: int = 1
    allow_user_ids: list[str] = Field(default_factory=list)
    deny_user_ids: list[str] = Field(default_factory=list)
    classification_status: Literal["pending", "auto_assigned", "review_required", "manual", "failed"] = (
        "pending"
    )
    classification_confidence: float | None = Field(default=None, ge=0, le=1)
    suggested_category_id: str | None = None
    classification_model: str | None = None
    classified_at: datetime | None = None


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=300)
    sort_order: int = Field(default=100, ge=0, le=10000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if not (normalized := value.strip()):
            raise ValueError("分类名称不能为空")
        return normalized


class CategoryUpdate(CategoryCreate):
    active: bool = True


class CategoryResponse(BaseModel):
    category_id: str
    knowledge_base_id: str
    name: str
    description: str
    sort_order: int
    active: bool
    is_system: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class BatchCategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_ids: list[str] = Field(min_length=1, max_length=500)
    category_id: str = Field(pattern=r"^cat_[a-f0-9]{16}$")


class ClassificationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(pattern=r"^cat_[a-f0-9]{16}$")


class DocumentMetadata(BaseModel):
    """文档级治理属性；版本与分块只能继承，不能自行扩大范围。"""

    model_config = ConfigDict(extra="forbid")
    category: str = Field(default="未分类", min_length=1, max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_system: str = Field(default="upload", min_length=1, max_length=80)
    external_resource_id: str | None = Field(default=None, max_length=200)
    owner_user_id: str | None = Field(default=None, pattern=r"^usr_[a-f0-9]{16}$")
    department: str | None = Field(default=None, max_length=80)
    sensitivity: Literal["public", "internal", "confidential", "restricted"] = "internal"
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    retrieval_status: Literal["searchable", "expired", "deleted"] = "searchable"
    acl_version: int = Field(default=1, ge=1)
    allow_user_ids: list[str] = Field(default_factory=list, max_length=100)
    deny_user_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("category")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        if not (normalized := value.strip()):
            raise ValueError("文档分类不能为空")
        return normalized

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            tag = value.strip()
            if not tag:
                raise ValueError("文档标签不能为空")
            if len(tag) > 64:
                raise ValueError("文档标签不能超过 64 个字符")
            if tag not in normalized:
                normalized.append(tag)
        return normalized

    @field_validator("source_system", "external_resource_id", "department")
    @classmethod
    def normalize_optional_terms(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("allow_user_ids", "deny_user_ids")
    @classmethod
    def normalize_acl_users(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            if not re.fullmatch(r"usr_[a-f0-9]{16}", value):
                raise ValueError("ACL 用户 ID 格式无效")
            if value not in normalized:
                normalized.append(value)
        return normalized

    @model_validator(mode="after")
    def validate_governance_boundaries(self) -> "DocumentMetadata":
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("valid_from 不能晚于 valid_to")
        if set(self.allow_user_ids).intersection(self.deny_user_ids):
            raise ValueError("同一用户不能同时出现在 Allow 与 Deny")
        return self


class AclUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_user_ids: list[str] = Field(default_factory=list, max_length=100)
    deny_user_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("allow_user_ids", "deny_user_ids")
    @classmethod
    def normalize_users(cls, values: list[str]) -> list[str]:
        return DocumentMetadata.normalize_acl_users(values)

    @model_validator(mode="after")
    def validate_precedence(self) -> "AclUpdate":
        if set(self.allow_user_ids).intersection(self.deny_user_ids):
            raise ValueError("同一用户不能同时出现在 Allow 与 Deny")
        return self


class AclPolicyResponse(BaseModel):
    version: int = Field(ge=1)
    allow_user_ids: list[str]
    deny_user_ids: list[str]


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
    parser_name: str | None = None
    parser_version: str | None = None
    chunking_version: str | None = None
    processing_options: dict[str, object] = Field(default_factory=dict)
    parse_status: Literal["pending", "parsing", "chunking", "ready", "failed"] = "pending"
    parse_failure_code: str | None = None
    node_count: int = 0
    parsed_chunk_count: int = 0


class ParsingPreviewResponse(BaseModel):
    document_version_id: str
    document_id: str
    filename: str
    version_number: int
    status: Literal["pending", "indexing", "ready", "failed", "superseded"]
    parse_status: Literal["pending", "parsing", "chunking", "ready", "failed"]
    parse_failure_code: str | None
    failure_reason: str | None
    parser_name: str | None
    parser_version: str | None
    chunking_version: str | None
    processing_options: dict[str, object]
    is_current: bool
    tree: list[dict[str, object]]
    chunks: list[dict[str, object]]


class ReprocessDocumentVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chunk_size: int = Field(default=700, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0, le=1000)

    @model_validator(mode="after")
    def validate_overlap(self) -> "ReprocessDocumentVersionRequest":
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")
        return self


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
    acl_version: int = Field(default=1, ge=1)
    allow_user_ids: list[str] = Field(default_factory=list)
    deny_user_ids: list[str] = Field(default_factory=list)
    allowed_actions: list[Literal["detail", "edit", "disable", "enable", "update_file", "delete"]]


class QueryMetadataFilter(BaseModel):
    """受控检索过滤条件；禁止透传 SQL、JSONPath 或任意表达式。"""

    model_config = ConfigDict(extra="forbid")

    categories: list[str] = Field(default_factory=list, max_length=10)
    category_ids: list[str] = Field(default_factory=list, max_length=10)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_types: list[Literal["file", "object_storage", "web", "connector"]] = Field(
        default_factory=list, max_length=4
    )
    created_from: datetime | None = None
    created_to: datetime | None = None

    @field_validator("categories", "tags")
    @classmethod
    def normalize_terms(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            term = value.strip()
            if not term:
                raise ValueError("过滤值不能为空")
            if len(term) > 64:
                raise ValueError("过滤值不能超过 64 个字符")
            if term not in normalized:
                normalized.append(term)
        return normalized

    @field_validator("category_ids")
    @classmethod
    def normalize_category_ids(cls, values: list[str]) -> list[str]:
        if any(not re.fullmatch(r"cat_[a-f0-9]{16}", value) for value in values):
            raise ValueError("分类 ID 格式无效")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_time_range(self) -> "QueryMetadataFilter":
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError("created_from 不能晚于 created_to")
        return self


class QueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    retrieve_k: int = Field(default=10, ge=1, le=50)
    rerank_k: int = Field(default=5, ge=1, le=20)
    conversation_id: str | None = Field(default=None, pattern=r"^conv_[a-f0-9]{16}$")
    filters: QueryMetadataFilter | None = None

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
    # 默认为空表示通路未知：V5 之前保存的历史回答没有这两个字段，
    # 页面必须按"缺失即不展示"处理，不能把旧记录当成向量召回。
    retrieval_channels: list[str] = Field(default_factory=list)
    lexical_score: float | None = None
    retrieval_methods: list[Literal["vector", "lexical"]] = Field(default_factory=list)
    query_match_count: int = Field(default=1, ge=1)


class QueryExecutionMetadata(BaseModel):
    strategy: Literal["original", "normalized", "controlled_expansion"]
    query_count: int = Field(ge=1, le=4)
    expansion_count: int = Field(ge=0, le=3)
    fallback_used: bool = False
    applied_filters: QueryMetadataFilter | None = None
    retrieved_candidate_count: int = Field(default=0, ge=0)
    fused_candidate_count: int = Field(default=0, ge=0)
    returned_source_count: int = Field(default=0, ge=0)
    filter_match_count: int | None = Field(default=None, ge=0)


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
    bad_case_category: str | None = None
    error_code: str | None
    error_message: str | None
    created_at: datetime


class BadCaseResponse(BaseModel):
    record_id: str
    conversation_id: str
    knowledge_base_id: str
    question: str
    bad_case_category: str
    error_code: str | None
    error_message: str | None
    query_metadata: QueryExecutionMetadata | None = None
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
