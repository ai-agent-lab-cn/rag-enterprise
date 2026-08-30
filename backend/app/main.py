import time
from contextlib import asynccontextmanager
from datetime import datetime
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, File, Form, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .audit import AuditRepository
from .auth import AuthenticatedSession, AuthRepository, UserRecord
from .chunking import chunking_version
from .config import get_settings
from .data_source_sync import build_connector, enqueue_sync
from .database import check_schema_version
from .demo import seed_demo_document
from .errors import AppError, install_error_handlers
from .evaluation_governance import BadCaseUpdate
from .evaluation_reports import EvaluationReportRepository
from .history import ConversationRepository
from .knowledge_bases import (
    DEFAULT_KNOWLEDGE_BASE_ID,
    KnowledgeBaseRecord,
    KnowledgeBaseRepository,
    KnowledgeBaseScope,
)
from .models import get_embedding_model, get_generator, get_reranker
from .observability import MetricsRegistry, ObservabilityMiddleware, bind_actor, hash_identifier
from .postgres_documents import PostgresAsyncRAGService, check_embedding_model
from .postgres_evaluation import PostgresEvaluationGovernanceRepository
from .postgres_repositories import (
    PostgresAuthRepository,
    PostgresCategoryRepository,
    PostgresCategoryTemplateRepository,
    PostgresDataSourceRepository,
    PostgresKnowledgeBaseRepository,
)
from .retrieval_access import RetrievalAccessContext
from .schemas import (
    AcceptanceRunCreate,
    AcceptanceRunResponse,
    AclPolicyResponse,
    AclUpdate,
    AnswerEvaluationReportResponse,
    AnswerEvaluationReportSummary,
    AnswerRecordResponse,
    AuditEventResponse,
    AuthBootstrapRequest,
    AuthBootstrapStatus,
    AuthLoginRequest,
    AuthTokenResponse,
    BadCaseResponse,
    BatchCategoryUpdate,
    CategoryCreate,
    CategoryResponse,
    CategoryTemplateItemCreate,
    CategoryTemplateItemResponse,
    CategoryTemplateItemUpdate,
    CategoryTemplateResponse,
    CategoryUpdate,
    CitationResponse,
    ClassificationUpdate,
    ConversationDetailResponse,
    ConversationSummaryResponse,
    DataSourceConnectionTestResponse,
    DataSourceCreate,
    DataSourceResponse,
    DataSourceUpdate,
    DocumentInfo,
    DocumentMetadata,
    DocumentVersionResponse,
    EvaluationCenterOverviewResponse,
    EvaluationReportResponse,
    EvaluationReportSummary,
    GovernedBadCaseResponse,
    GovernedBadCaseUpdate,
    HealthResponse,
    IndexVersionResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    LivenessResponse,
    MemberCreate,
    MemberUpdate,
    MetricsResponse,
    ParsingPreviewResponse,
    PipelineEvaluationResponse,
    QueryRequest,
    QueryResponse,
    ReadinessResponse,
    ReprocessDocumentVersionRequest,
    SyncEnqueueResponse,
    SyncRunResponse,
    UserResponse,
)
from .security import AbuseProtection, SecurityBoundaryMiddleware, validate_upload
from .service import RAGService, RAGServiceProtocol


# FastAPI 路由、依赖注入、上传限制和 CORS
@lru_cache
def get_service() -> RAGService:
    """构造唯一的 RAG 运行时。

    Chroma 降级路径已移除：向量存储只剩 pgvector 一种实现，缺少 DATABASE_URL 时直接
    失败，而不是悄悄退回一个能力与生产不同的本地存储。
    """

    settings = get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required")
    return PostgresAsyncRAGService(
        settings=settings,
        embedder=get_embedding_model(),
        reranker=get_reranker(),
        generator=get_generator(),
    )


ServiceDependency = Annotated[RAGServiceProtocol, Depends(get_service)]
UploadedFile = Annotated[UploadFile, File()]


@lru_cache
def get_evaluation_reports() -> EvaluationReportRepository:
    """报告查询不依赖 RAGService，避免只读请求初始化重量模型。"""

    return EvaluationReportRepository(get_settings().evaluation_reports_path)


EvaluationReportsDependency = Annotated[EvaluationReportRepository, Depends(get_evaluation_reports)]


@lru_cache
def get_evaluation_governance() -> PostgresEvaluationGovernanceRepository | None:
    database_url = get_settings().database_url
    return PostgresEvaluationGovernanceRepository(database_url) if database_url else None


EvaluationGovernanceDependency = Annotated[
    PostgresEvaluationGovernanceRepository | None,
    Depends(get_evaluation_governance),
]


@lru_cache
def get_knowledge_bases() -> KnowledgeBaseRepository:
    settings = get_settings()
    if settings.database_url:
        return PostgresKnowledgeBaseRepository(settings.database_url)
    return KnowledgeBaseRepository(settings.knowledge_bases_path)


KnowledgeBasesDependency = Annotated[KnowledgeBaseRepository, Depends(get_knowledge_bases)]


@lru_cache
def get_data_sources() -> PostgresDataSourceRepository | None:
    database_url = get_settings().database_url
    return PostgresDataSourceRepository(database_url) if database_url else None


DataSourcesDependency = Annotated[PostgresDataSourceRepository | None, Depends(get_data_sources)]


@lru_cache
def get_categories() -> PostgresCategoryRepository | None:
    database_url = get_settings().database_url
    return PostgresCategoryRepository(database_url) if database_url else None


CategoriesDependency = Annotated[PostgresCategoryRepository | None, Depends(get_categories)]


@lru_cache
def get_category_templates() -> PostgresCategoryTemplateRepository | None:
    database_url = get_settings().database_url
    return PostgresCategoryTemplateRepository(database_url) if database_url else None


CategoryTemplatesDependency = Annotated[
    PostgresCategoryTemplateRepository | None,
    Depends(get_category_templates),
]


@lru_cache
def get_conversations() -> ConversationRepository:
    return ConversationRepository(get_settings().conversations_path)


ConversationsDependency = Annotated[ConversationRepository, Depends(get_conversations)]


@lru_cache
def get_auth_repository() -> AuthRepository:
    settings = get_settings()
    if settings.database_url:
        return PostgresAuthRepository(settings.database_url, settings.session_ttl_hours)
    return AuthRepository(settings.auth_path, settings.session_ttl_hours)


AuthRepositoryDependency = Annotated[AuthRepository, Depends(get_auth_repository)]


@lru_cache
def get_audit_repository() -> AuditRepository:
    return AuditRepository(get_settings().audit_path)


AuditRepositoryDependency = Annotated[AuditRepository, Depends(get_audit_repository)]
bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Depends(bearer_scheme),
]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


async def get_current_session(
    auth: AuthRepositoryDependency,
    credentials: BearerCredentials,
) -> AuthenticatedSession:
    if credentials is None:
        raise AppError("AUTHENTICATION_REQUIRED", "请先登录。", 401)
    if credentials.scheme.casefold() != "bearer" or not credentials.credentials.strip():
        raise AppError("AUTHENTICATION_REQUIRED", "登录凭据无效，请重新登录。", 401)
    session = await run_in_threadpool(auth.resolve_session, credentials.credentials.strip())
    if session is None:
        raise AppError("SESSION_INVALID", "登录已过期或失效，请重新登录。", 401)
    bind_actor(session.user.user_id)
    return session


CurrentSessionDependency = Annotated[AuthenticatedSession, Depends(get_current_session)]


def create_app() -> FastAPI:
    settings = get_settings()
    metrics = MetricsRegistry()
    abuse_protection = AbuseProtection(
        login_limit=settings.login_rate_limit,
        expensive_limit=settings.expensive_rate_limit,
        window_seconds=settings.rate_limit_window_seconds,
        concurrency_limit=settings.max_concurrent_expensive_requests,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if settings.database_url:
            await run_in_threadpool(
                check_schema_version,
                settings.database_url,
                settings.required_database_schema_version,
            )
            # 换了向量模型却直接启动，会把新维度写进既有索引，必须尽早失败。
            await run_in_threadpool(check_embedding_model, settings.database_url, settings.embedding_model)
        # 服务启动时迁移 V2 原始文件；只移动根目录文件，不扫描其他知识库目录。
        KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, settings.upload_path).migrate_legacy_uploads()
        get_knowledge_bases()
        get_conversations()
        get_auth_repository()
        get_audit_repository()
        if settings.demo_seed_path is not None:
            await run_in_threadpool(seed_demo_document, settings.demo_seed_path, get_service())
        yield

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url=None if settings.app_environment == "production" else "/api/docs",
        redoc_url=None,
        openapi_url=None if settings.app_environment == "production" else "/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(
        SecurityBoundaryMiddleware,
        max_body_bytes=settings.max_request_body_mb * 1024 * 1024,
    )
    app.add_middleware(ObservabilityMiddleware, metrics=metrics)
    install_error_handlers(app)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version=app.version,
            collection_ready=True,
            generation_ready=bool(settings.gemini_api_key),
            models={
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
                "generation": settings.generation_model,
            },
        )

    @app.get("/api/health/live", response_model=LivenessResponse)
    async def liveness() -> LivenessResponse:
        return LivenessResponse()

    @app.get("/api/health/ready", response_model=ReadinessResponse)
    async def readiness(
        response: Response,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
    ) -> ReadinessResponse:
        checks: dict[str, str] = {}
        for name, check in (
            ("auth_store", auth.has_users),
            ("audit_store", audit.verify),
            ("knowledge_base_registry", knowledge_bases.list),
            (
                "conversation_store",
                lambda: conversations.count_conversations(DEFAULT_KNOWLEDGE_BASE_ID),
            ),
        ):
            try:
                await run_in_threadpool(check)
                checks[name] = "ok"
            except Exception:
                checks[name] = "failed"
        status = "ready" if all(value == "ok" for value in checks.values()) else "not_ready"
        if status == "not_ready":
            response.status_code = 503
        return ReadinessResponse(status=status, checks=checks)

    @app.get("/api/system/metrics", response_model=MetricsResponse)
    async def system_metrics(current: CurrentSessionDependency) -> MetricsResponse:
        _require_admin(current.user)
        return MetricsResponse(**metrics.snapshot())

    @app.get("/api/audit/events", response_model=list[AuditEventResponse])
    async def list_audit_events(
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
        action: Annotated[str | None, Query(max_length=80, pattern=r"^[a-z][a-z0-9_.-]+$")] = None,
        result: Annotated[str | None, Query(pattern=r"^(success|denied|failed)$")] = None,
    ) -> list[AuditEventResponse]:
        _require_admin(current.user)
        events = await run_in_threadpool(
            audit.list,
            offset=offset,
            limit=limit,
            action=action,
            result=result,
        )
        return [AuditEventResponse(**event) for event in events]

    @app.get("/api/auth/bootstrap", response_model=AuthBootstrapStatus)
    async def auth_bootstrap_status(auth: AuthRepositoryDependency) -> AuthBootstrapStatus:
        return AuthBootstrapStatus(required=not auth.has_users())

    @app.post("/api/auth/bootstrap", response_model=AuthTokenResponse, status_code=201)
    async def bootstrap_auth(
        payload: AuthBootstrapRequest,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> AuthTokenResponse:
        try:
            session = await run_in_threadpool(
                auth.bootstrap_admin,
                payload.username,
                payload.password,
                payload.display_name,
            )
        except ValueError as exc:
            raise AppError("AUTH_BOOTSTRAP_COMPLETED", "管理员初始化已经完成。", 409) from exc
        await _record_audit(
            audit,
            "auth.bootstrap",
            session.user,
            "user",
            session.user.user_id,
        )
        return _auth_token_response(session)

    @app.post("/api/auth/login", response_model=AuthTokenResponse)
    async def login(
        payload: AuthLoginRequest,
        request: Request,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> AuthTokenResponse:
        try:
            abuse_protection.check_login(_client_key(request), payload.username)
        except AppError:
            await _record_audit(
                audit,
                "auth.login_rate_limited",
                None,
                "user",
                None,
                result="denied",
                metadata={"target_actor_hash": _anonymous_actor(payload.username)},
            )
            raise
        session = await run_in_threadpool(auth.authenticate, payload.username, payload.password)
        if session is None:
            await _record_audit(
                audit,
                "auth.login",
                None,
                "user",
                None,
                result="denied",
                metadata={"target_actor_hash": _anonymous_actor(payload.username)},
            )
            raise AppError("INVALID_CREDENTIALS", "用户名或密码错误。", 401)
        bind_actor(session.user.user_id)
        await _record_audit(
            audit,
            "auth.login",
            session.user,
            "session",
            None,
        )
        return _auth_token_response(session)

    @app.post("/api/auth/logout", status_code=204)
    async def logout(
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        await run_in_threadpool(auth.revoke_session, current.token)
        await _record_audit(audit, "auth.logout", current.user, "session", None)

    @app.get("/api/auth/me", response_model=UserResponse)
    async def current_user(current: CurrentSessionDependency) -> UserResponse:
        return _user_response(current.user)

    @app.get("/api/members", response_model=list[UserResponse])
    async def list_members(
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[UserResponse]:
        _require_admin(current.user)
        users = await run_in_threadpool(auth.list_users)
        return [_user_response(item) for item in _page(users, offset, limit)]

    @app.post("/api/members", response_model=UserResponse, status_code=201)
    async def create_member(
        payload: MemberCreate,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> UserResponse:
        _require_admin(current.user)
        try:
            user = await run_in_threadpool(
                auth.create_user,
                payload.username,
                payload.password,
                payload.display_name,
                payload.role,
            )
        except ValueError as exc:
            raise AppError("USERNAME_CONFLICT", "用户名已存在。", 409) from exc
        await _record_audit(
            audit,
            "member.create",
            current.user,
            "user",
            user.user_id,
            metadata={"role": user.role, "target_actor_hash": _anonymous_actor(user.user_id)},
        )
        return _user_response(user)

    @app.put("/api/members/{user_id}", response_model=UserResponse)
    async def update_member(
        user_id: str,
        payload: MemberUpdate,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> UserResponse:
        _require_admin(current.user)
        try:
            user = await run_in_threadpool(
                auth.update_user,
                user_id,
                display_name=payload.display_name,
                role=payload.role,
                active=payload.active,
                password=payload.password,
            )
        except (PermissionError, ValueError) as exc:
            if isinstance(exc, PermissionError):
                raise AppError("LAST_ADMIN_REQUIRED", "至少保留一名启用的管理员。", 409) from exc
            raise AppError("MEMBER_NOT_FOUND", "未找到该成员。", 404) from exc
        if user is None:
            raise AppError("MEMBER_NOT_FOUND", "未找到该成员。", 404)
        await _record_audit(
            audit,
            "member.update",
            current.user,
            "user",
            user.user_id,
            metadata={
                "role": user.role,
                "active": user.active,
                "target_actor_hash": _anonymous_actor(user.user_id),
            },
        )
        return _user_response(user)

    @app.post("/api/documents", response_model=DocumentInfo, status_code=201)
    async def upload_document(
        file: UploadedFile,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> DocumentInfo:
        _require_knowledge_base_access(auth, current.user, DEFAULT_KNOWLEDGE_BASE_ID)
        return await _upload_document(
            file,
            service,
            DEFAULT_KNOWLEDGE_BASE_ID,
            settings,
            abuse_protection,
            metrics,
            audit,
            current.user,
        )

    @app.get("/api/documents", response_model=list[DocumentInfo])
    async def list_documents(
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[DocumentInfo]:
        _require_knowledge_base_access(auth, current.user, DEFAULT_KNOWLEDGE_BASE_ID)
        documents = await run_in_threadpool(service.list_documents, DEFAULT_KNOWLEDGE_BASE_ID)
        return _page(documents, offset, limit)

    @app.get("/api/knowledge-bases", response_model=list[KnowledgeBaseResponse])
    async def list_knowledge_bases(
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
        name: str = Query(default="", max_length=80),
        status: str = Query(default="", pattern=r"^(|empty|processing|ready|failed)$"),
        sort: str = Query(default="updated_desc", pattern=r"^(updated_desc|updated_asc)$"),
    ) -> list[KnowledgeBaseResponse]:
        records = await run_in_threadpool(knowledge_bases.list)
        accessible_ids = await run_in_threadpool(auth.accessible_knowledge_base_ids, current.user)
        if accessible_ids is not None:
            records = [item for item in records if item.knowledge_base_id in accessible_ids]
        if name.strip():
            needle = name.strip().casefold()
            records = [item for item in records if needle in item.name.casefold()]
        responses = [await _knowledge_base_response(item, service, current.user) for item in records]
        if status:
            responses = [item for item in responses if item.index_status == status]
        responses.sort(key=lambda item: item.updated_at, reverse=sort == "updated_desc")
        return _page(responses, offset, limit)

    @app.get("/api/data-sources", response_model=list[DataSourceResponse])
    async def list_data_sources(
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[DataSourceResponse]:
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源管理需要 PostgreSQL 运行时。", 503)
        accessible_ids = await run_in_threadpool(auth.accessible_knowledge_base_ids, current.user)
        rows = await run_in_threadpool(sources.list, accessible_ids)
        return [_data_source_response(row, current.user) for row in _page(rows, offset, limit)]

    @app.post("/api/knowledge-bases/{knowledge_base_id}/data-sources", status_code=201)
    async def create_data_source(
        knowledge_base_id: str,
        payload: DataSourceCreate,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> dict[str, str]:
        _require_admin(current.user)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源管理需要 PostgreSQL 运行时。", 503)
        try:
            data_source_id = await run_in_threadpool(
                sources.create,
                knowledge_base_id,
                payload.name.strip(),
                payload.source_type,
                payload.configuration,
                payload.default_category_id,
                payload.metadata_defaults,
            )
        except ValueError as exc:
            raise AppError("DATA_SOURCE_CONFIGURATION_INVALID", "知识库或默认分类无效。", 400) from exc
        await _record_audit(
            audit,
            "data_source.create",
            current.user,
            "data_source",
            data_source_id,
            metadata={"knowledge_base_id": knowledge_base_id, "source_type": payload.source_type},
        )
        return {"data_source_id": data_source_id}

    @app.put("/api/data-sources/{data_source_id}", status_code=204)
    async def update_data_source(
        data_source_id: str,
        payload: DataSourceUpdate,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源管理需要 PostgreSQL 运行时。", 503)
        try:
            updated = await run_in_threadpool(
                sources.update,
                data_source_id,
                payload.name.strip(),
                payload.configuration,
                payload.default_category_id,
                payload.metadata_defaults,
            )
        except ValueError as exc:
            raise AppError("DATA_SOURCE_CONFIGURATION_INVALID", "默认分类无效。", 400) from exc
        if not updated:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        await _record_audit(audit, "data_source.update", current.user, "data_source", data_source_id)

    @app.post(
        "/api/data-sources/{data_source_id}/test",
        response_model=DataSourceConnectionTestResponse,
    )
    async def test_data_source_connection(
        data_source_id: str,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
    ) -> DataSourceConnectionTestResponse:
        _require_admin(current.user)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源管理需要 PostgreSQL 运行时。", 503)
        source = await run_in_threadpool(sources.get, data_source_id)
        if source is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        configuration = dict(source.get("configuration") or {})
        connector = await run_in_threadpool(
            build_connector,
            configuration,
            str(source["source_type"]),
            settings.max_upload_mb * 1024 * 1024,
        )
        discovered = await run_in_threadpool(lambda: sum(1 for _ in connector.list_objects()))
        return DataSourceConnectionTestResponse(
            ok=True, discovered_count=discovered, message=f"连接成功，发现 {discovered} 个可处理对象。"
        )

    @app.post(
        "/api/data-sources/{data_source_id}/sync",
        response_model=SyncEnqueueResponse,
        status_code=202,
    )
    async def trigger_data_source_sync(
        data_source_id: str,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> SyncEnqueueResponse:
        _require_admin(current.user)
        if sources is None or await run_in_threadpool(sources.get, data_source_id) is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        result = await run_in_threadpool(enqueue_sync, sources.database_url, data_source_id)
        await _record_audit(audit, "data_source.sync", current.user, "data_source", data_source_id)
        return SyncEnqueueResponse(**result)

    @app.get(
        "/api/data-sources/{data_source_id}/sync-runs",
        response_model=list[SyncRunResponse],
    )
    async def list_data_source_sync_runs(
        data_source_id: str,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        limit: int = Query(default=50, ge=1, le=100),
    ) -> list[SyncRunResponse]:
        _require_admin(current.user)
        if sources is None or await run_in_threadpool(sources.get, data_source_id) is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        rows = await run_in_threadpool(sources.list_sync_runs, data_source_id, limit)
        return [SyncRunResponse(**row) for row in rows]

    @app.post(
        "/api/data-sources/{data_source_id}/retry",
        response_model=SyncEnqueueResponse,
        status_code=202,
    )
    async def retry_data_source_sync(
        data_source_id: str,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> SyncEnqueueResponse:
        return await trigger_data_source_sync(data_source_id, sources, current, audit)

    @app.put("/api/data-sources/{data_source_id}/enabled", status_code=204)
    async def set_data_source_enabled(
        data_source_id: str,
        enabled: bool,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        if sources is None or not await run_in_threadpool(sources.set_enabled, data_source_id, enabled):
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        await _record_audit(
            audit,
            "data_source.enable" if enabled else "data_source.disable",
            current.user,
            "data_source",
            data_source_id,
        )

    @app.delete("/api/data-sources/{data_source_id}", status_code=204)
    async def delete_data_source(
        data_source_id: str,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源管理需要 PostgreSQL 运行时。", 503)
        try:
            deleted = await run_in_threadpool(sources.delete, data_source_id)
        except ValueError as exc:
            raise AppError(
                "DATA_SOURCE_IN_USE",
                "请先删除关联文档，并等待运行中的索引任务完成。",
                409,
            ) from exc
        if not deleted:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        await _record_audit(audit, "data_source.delete", current.user, "data_source", data_source_id)

    @app.get("/api/category-templates/default", response_model=CategoryTemplateResponse)
    async def get_default_category_template(
        templates: CategoryTemplatesDependency,
        current: CurrentSessionDependency,
    ) -> CategoryTemplateResponse:
        _require_admin(current.user)
        if templates is None:
            raise AppError("POSTGRES_REQUIRED", "分类模板治理需要 PostgreSQL 运行时。", 503)
        return CategoryTemplateResponse(**await run_in_threadpool(templates.get_default))

    @app.post(
        "/api/category-templates/default/items",
        response_model=CategoryTemplateItemResponse,
        status_code=201,
    )
    async def create_default_category_template_item(
        payload: CategoryTemplateItemCreate,
        templates: CategoryTemplatesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> CategoryTemplateItemResponse:
        _require_admin(current.user)
        if templates is None:
            raise AppError("POSTGRES_REQUIRED", "分类模板治理需要 PostgreSQL 运行时。", 503)
        try:
            row = await run_in_threadpool(
                templates.create_item, payload.name, payload.description, payload.sort_order
            )
        except PermissionError as exc:
            raise AppError("CATEGORY_TEMPLATE_RESERVED_NAME", "“未分类”为系统保留名称。", 409) from exc
        except ValueError as exc:
            raise AppError("CATEGORY_TEMPLATE_NAME_CONFLICT", "模板分类名称已存在。", 409) from exc
        await _record_audit(
            audit, "category_template.create", current.user, "category_template_item",
            str(row["template_item_id"]),
        )
        return CategoryTemplateItemResponse(**row)

    @app.put(
        "/api/category-templates/default/items/{template_item_id}",
        response_model=CategoryTemplateItemResponse,
    )
    async def update_default_category_template_item(
        template_item_id: str,
        payload: CategoryTemplateItemUpdate,
        templates: CategoryTemplatesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> CategoryTemplateItemResponse:
        _require_admin(current.user)
        if templates is None:
            raise AppError("POSTGRES_REQUIRED", "分类模板治理需要 PostgreSQL 运行时。", 503)
        try:
            row = await run_in_threadpool(
                templates.update_item, template_item_id, payload.name, payload.description,
                payload.sort_order, payload.active,
            )
        except PermissionError as exc:
            raise AppError("CATEGORY_TEMPLATE_RESERVED_NAME", "“未分类”为系统保留名称。", 409) from exc
        except ValueError as exc:
            raise AppError("CATEGORY_TEMPLATE_NAME_CONFLICT", "模板分类名称已存在。", 409) from exc
        if row is None:
            raise AppError("CATEGORY_TEMPLATE_ITEM_NOT_FOUND", "未找到该模板分类。", 404)
        await _record_audit(
            audit, "category_template.update", current.user, "category_template_item", template_item_id
        )
        return CategoryTemplateItemResponse(**row)

    @app.delete("/api/category-templates/default/items/{template_item_id}", status_code=204)
    async def delete_default_category_template_item(
        template_item_id: str,
        templates: CategoryTemplatesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        if templates is None:
            raise AppError("POSTGRES_REQUIRED", "分类模板治理需要 PostgreSQL 运行时。", 503)
        if not await run_in_threadpool(templates.delete_item, template_item_id):
            raise AppError("CATEGORY_TEMPLATE_ITEM_NOT_FOUND", "未找到该模板分类。", 404)
        await _record_audit(
            audit, "category_template.delete", current.user, "category_template_item", template_item_id
        )

    @app.post("/api/knowledge-bases", response_model=KnowledgeBaseResponse, status_code=201)
    async def create_knowledge_base(
        payload: KnowledgeBaseCreate,
        knowledge_bases: KnowledgeBasesDependency,
        categories: CategoriesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> KnowledgeBaseResponse:
        _require_admin(current.user)
        apply_template = (
            categories is not None
            if payload.apply_default_category_template is None
            else payload.apply_default_category_template
        )
        if apply_template and categories is None:
            raise AppError("POSTGRES_REQUIRED", "默认分类模板需要 PostgreSQL 运行时。", 503)
        try:
            record = await run_in_threadpool(
                knowledge_bases.create,
                payload.name.strip(),
                payload.description.strip(),
                apply_template,
            )
        except ValueError as exc:
            raise AppError("KNOWLEDGE_BASE_NAME_CONFLICT", "知识库名称已存在。", 409) from exc
        copied_count = 0
        if categories is not None:
            copied_count = len(
                [
                    item
                    for item in await run_in_threadpool(categories.list, record.knowledge_base_id)
                    if not item["is_system"]
                ]
            )
        await _record_audit(
            audit,
            "knowledge_base.create",
            current.user,
            "knowledge_base",
            record.knowledge_base_id,
            metadata={
                "apply_default_category_template": apply_template,
                "copied_category_count": copied_count,
            },
        )
        return await _knowledge_base_response(record, service, current.user)

    @app.get("/api/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
    async def get_knowledge_base(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> KnowledgeBaseResponse:
        record = await _require_accessible_knowledge_base(
            knowledge_bases,
            auth,
            current.user,
            knowledge_base_id,
        )
        return await _knowledge_base_response(record, service, current.user)

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/categories",
        response_model=list[CategoryResponse],
    )
    async def list_categories(
        knowledge_base_id: str,
        categories: CategoriesDependency,
        knowledge_bases: KnowledgeBasesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> list[CategoryResponse]:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if categories is None:
            raise AppError("POSTGRES_REQUIRED", "分类治理需要 PostgreSQL 运行时。", 503)
        rows = await run_in_threadpool(categories.list, knowledge_base_id)
        return [CategoryResponse(**row) for row in rows]

    @app.post(
        "/api/knowledge-bases/{knowledge_base_id}/categories",
        response_model=CategoryResponse,
        status_code=201,
    )
    async def create_category(
        knowledge_base_id: str,
        payload: CategoryCreate,
        categories: CategoriesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> CategoryResponse:
        _require_admin(current.user)
        if categories is None:
            raise AppError("POSTGRES_REQUIRED", "分类治理需要 PostgreSQL 运行时。", 503)
        try:
            row = await run_in_threadpool(
                categories.create,
                knowledge_base_id,
                payload.name,
                payload.description,
                payload.sort_order,
            )
        except ValueError as exc:
            raise AppError("CATEGORY_NAME_CONFLICT", "分类名称已存在。", 409) from exc
        await _record_audit(audit, "category.create", current.user, "category", str(row["category_id"]))
        return CategoryResponse(**row)

    @app.put(
        "/api/knowledge-bases/{knowledge_base_id}/categories/{category_id}",
        response_model=CategoryResponse,
    )
    async def update_category(
        knowledge_base_id: str,
        category_id: str,
        payload: CategoryUpdate,
        categories: CategoriesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> CategoryResponse:
        _require_admin(current.user)
        if categories is None:
            raise AppError("POSTGRES_REQUIRED", "分类治理需要 PostgreSQL 运行时。", 503)
        try:
            row = await run_in_threadpool(
                categories.update,
                knowledge_base_id,
                category_id,
                payload.name,
                payload.description,
                payload.sort_order,
                payload.active,
            )
        except PermissionError as exc:
            raise AppError("SYSTEM_CATEGORY_PROTECTED", "系统分类不可重命名或停用。", 409) from exc
        except ValueError as exc:
            raise AppError("CATEGORY_NAME_CONFLICT", "分类名称已存在。", 409) from exc
        if row is None:
            raise AppError("CATEGORY_NOT_FOUND", "未找到该分类。", 404)
        await _record_audit(audit, "category.update", current.user, "category", category_id)
        return CategoryResponse(**row)

    @app.delete("/api/knowledge-bases/{knowledge_base_id}/categories/{category_id}", status_code=204)
    async def delete_category(
        knowledge_base_id: str,
        category_id: str,
        categories: CategoriesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        if categories is None:
            raise AppError("POSTGRES_REQUIRED", "分类治理需要 PostgreSQL 运行时。", 503)
        try:
            deleted = await run_in_threadpool(categories.delete, knowledge_base_id, category_id)
        except PermissionError as exc:
            raise AppError("SYSTEM_CATEGORY_PROTECTED", "系统分类不可删除。", 409) from exc
        except ValueError as exc:
            raise AppError(
                "CATEGORY_IN_USE",
                "分类仍被资料引用，请先批量迁移资料。",
                409,
                {"document_count": int(str(exc))},
            ) from exc
        if not deleted:
            raise AppError("CATEGORY_NOT_FOUND", "未找到该分类。", 404)
        await _record_audit(audit, "category.delete", current.user, "category", category_id)

    @app.put(
        "/api/knowledge-bases/{knowledge_base_id}/documents/categories",
        response_model=dict[str, int],
    )
    async def batch_assign_category(
        knowledge_base_id: str,
        payload: BatchCategoryUpdate,
        categories: CategoriesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> dict[str, int]:
        _require_admin(current.user)
        if categories is None:
            raise AppError("POSTGRES_REQUIRED", "分类治理需要 PostgreSQL 运行时。", 503)
        updated = await run_in_threadpool(
            categories.assign, knowledge_base_id, payload.document_ids, payload.category_id
        )
        if updated is None:
            raise AppError("CATEGORY_NOT_FOUND", "分类不存在或已停用。", 404)
        await _record_audit(
            audit,
            "document.category.batch_update",
            current.user,
            "knowledge_base",
            knowledge_base_id,
            metadata={"updated": updated},
        )
        return {"updated": updated}

    @app.put(
        "/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/classification",
        response_model=dict[str, int],
    )
    async def confirm_document_classification(
        knowledge_base_id: str,
        document_id: str,
        payload: ClassificationUpdate,
        categories: CategoriesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> dict[str, int]:
        _require_admin(current.user)
        if categories is None:
            raise AppError("POSTGRES_REQUIRED", "分类治理需要 PostgreSQL 运行时。", 503)
        updated = await run_in_threadpool(
            categories.assign, knowledge_base_id, [document_id], payload.category_id
        )
        if updated is None:
            raise AppError("CATEGORY_NOT_FOUND", "分类不存在或已停用。", 404)
        if updated == 0:
            raise AppError("DOCUMENT_NOT_FOUND", "未找到该文档。", 404)
        await _record_audit(audit, "document.classification.confirm", current.user, "document", document_id)
        return {"updated": updated}

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/members",
        response_model=list[UserResponse],
    )
    async def list_knowledge_base_members(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[UserResponse]:
        _require_admin(current.user)
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        users = await run_in_threadpool(auth.list_knowledge_base_users, knowledge_base_id)
        return [_user_response(item) for item in _page(users, offset, limit)]

    @app.put(
        "/api/knowledge-bases/{knowledge_base_id}/members/{user_id}",
        status_code=204,
    )
    async def grant_knowledge_base_member(
        knowledge_base_id: str,
        user_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        try:
            await run_in_threadpool(auth.grant_knowledge_base, user_id, knowledge_base_id)
        except (LookupError, ValueError) as exc:
            raise AppError("MEMBER_NOT_FOUND", "未找到可授权的成员。", 404) from exc
        await _record_audit(
            audit,
            "knowledge_base.member_grant",
            current.user,
            "knowledge_base",
            knowledge_base_id,
            metadata={"target_actor_hash": _anonymous_actor(user_id)},
        )

    @app.delete(
        "/api/knowledge-bases/{knowledge_base_id}/members/{user_id}",
        status_code=204,
    )
    async def revoke_knowledge_base_member(
        knowledge_base_id: str,
        user_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        try:
            await run_in_threadpool(auth.revoke_knowledge_base, user_id, knowledge_base_id)
        except ValueError as exc:
            raise AppError("MEMBER_NOT_FOUND", "未找到可撤销的成员。", 404) from exc
        await _record_audit(
            audit,
            "knowledge_base.member_revoke",
            current.user,
            "knowledge_base",
            knowledge_base_id,
            metadata={"target_actor_hash": _anonymous_actor(user_id)},
        )

    @app.put("/api/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
    async def update_knowledge_base(
        knowledge_base_id: str,
        payload: KnowledgeBaseUpdate,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> KnowledgeBaseResponse:
        _require_admin(current.user)
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        try:
            record = await run_in_threadpool(
                knowledge_bases.update,
                knowledge_base_id,
                payload.name.strip(),
                payload.description.strip(),
            )
        except ValueError as exc:
            raise AppError("KNOWLEDGE_BASE_NAME_CONFLICT", "知识库名称已存在。", 409) from exc
        if record is None:
            raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
        await _record_audit(
            audit,
            "knowledge_base.update",
            current.user,
            "knowledge_base",
            knowledge_base_id,
        )
        return await _knowledge_base_response(record, service, current.user)

    @app.delete("/api/knowledge-bases/{knowledge_base_id}", status_code=204)
    async def delete_knowledge_base(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_admin(current.user)
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        if knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID:
            raise AppError("DEFAULT_KNOWLEDGE_BASE_PROTECTED", "默认知识库不能删除。", 409)
        documents = await run_in_threadpool(service.list_documents, knowledge_base_id)
        upload_scope = KnowledgeBaseScope(knowledge_base_id, settings.upload_path)
        has_original_files = upload_scope.upload_path.exists() and any(upload_scope.upload_path.iterdir())
        history = await run_in_threadpool(conversations.count_conversations, knowledge_base_id)
        if documents or has_original_files or history:
            raise AppError("KNOWLEDGE_BASE_NOT_EMPTY", "请先删除知识库中的文档和会话。", 409)
        await run_in_threadpool(knowledge_bases.delete, knowledge_base_id)
        await run_in_threadpool(auth.remove_knowledge_base, knowledge_base_id)
        await _record_audit(
            audit,
            "knowledge_base.delete",
            current.user,
            "knowledge_base",
            knowledge_base_id,
        )

    @app.post(
        "/api/knowledge-bases/{knowledge_base_id}/documents",
        response_model=DocumentInfo,
        status_code=201,
    )
    async def upload_scoped_document(
        knowledge_base_id: str,
        file: UploadedFile,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
        category: Annotated[str, Form()] = "未分类",
        tags: Annotated[list[str] | None, Form()] = None,
    ) -> DocumentInfo:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        return await _upload_document(
            file,
            service,
            knowledge_base_id,
            settings,
            abuse_protection,
            metrics,
            audit,
            current.user,
            DocumentMetadata(category=category, tags=tags or []).model_dump(mode="json"),
        )

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/index-versions",
        response_model=list[IndexVersionResponse],
    )
    async def list_scoped_index_versions(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[IndexVersionResponse]:
        """索引版本只读视图。切换与回滚是高风险操作，只提供 CLI 入口，不开放写接口。"""

        _require_admin(current.user)
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        versions = await run_in_threadpool(service.list_index_versions, knowledge_base_id)
        return _page([IndexVersionResponse(**item) for item in versions], offset, limit)

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/documents",
        response_model=list[DocumentInfo],
    )
    async def list_scoped_documents(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[DocumentInfo]:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        documents = await run_in_threadpool(service.list_documents, knowledge_base_id)
        return _page(documents, offset, limit)

    @app.patch(
        "/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/metadata",
        response_model=DocumentInfo,
    )
    async def update_scoped_document_metadata(
        knowledge_base_id: str,
        document_id: str,
        payload: DocumentMetadata,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> DocumentInfo:
        _require_admin(current.user)
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        updated = await run_in_threadpool(
            service.update_document_metadata,
            document_id,
            payload.model_dump(mode="json", exclude_unset=True),
            knowledge_base_id,
        )
        if not updated:
            raise AppError("DOCUMENT_NOT_FOUND", "未找到该文档。", 404)
        await _record_audit(
            audit,
            "document.metadata.update",
            current.user,
            "document",
            document_id,
            metadata={"knowledge_base_id": knowledge_base_id},
        )
        documents = await run_in_threadpool(service.list_documents, knowledge_base_id)
        return next(item for item in documents if item.document_id == document_id)

    @app.put(
        "/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}/acl",
        response_model=AclPolicyResponse,
    )
    async def update_scoped_document_acl(
        knowledge_base_id: str,
        document_id: str,
        payload: AclUpdate,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> AclPolicyResponse:
        _require_admin(current.user)
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        version = await run_in_threadpool(
            service.update_document_acl,
            document_id,
            payload.allow_user_ids,
            payload.deny_user_ids,
            knowledge_base_id,
        )
        if version is None:
            raise AppError("DOCUMENT_NOT_FOUND", "未找到该文档。", 404)
        await _record_audit(
            audit,
            "document.acl.update",
            current.user,
            "document",
            document_id,
            metadata={"knowledge_base_id": knowledge_base_id, "acl_version": version},
        )
        return AclPolicyResponse(version=version, **payload.model_dump())

    @app.put(
        "/api/data-sources/{data_source_id}/acl",
        response_model=AclPolicyResponse,
    )
    async def update_data_source_acl(
        data_source_id: str,
        payload: AclUpdate,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> AclPolicyResponse:
        _require_admin(current.user)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源 ACL 管理需要 PostgreSQL 运行时。", 503)
        updated = await run_in_threadpool(
            sources.update_acl,
            data_source_id,
            payload.allow_user_ids,
            payload.deny_user_ids,
        )
        if updated is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        version = int(updated["version"])
        await _record_audit(
            audit,
            "data_source.acl.update",
            current.user,
            "data_source",
            data_source_id,
            metadata={
                "knowledge_base_id": str(updated["knowledge_base_id"]),
                "acl_version": version,
            },
        )
        return AclPolicyResponse(version=version, **payload.model_dump())

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/document-versions",
        response_model=list[DocumentVersionResponse],
    )
    async def list_document_versions(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 100,
    ) -> list[DocumentVersionResponse]:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if sources is None:
            return []
        rows = await run_in_threadpool(sources.list_document_versions, knowledge_base_id)
        return [DocumentVersionResponse(**row) for row in _page(rows, offset, limit)]

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/citations/{chunk_id}",
        response_model=CitationResponse,
    )
    async def get_citation_source(
        knowledge_base_id: str,
        chunk_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> CitationResponse:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "可信引用定位需要 PostgreSQL 运行时。", 503)
        row = await run_in_threadpool(
            sources.get_citation,
            knowledge_base_id,
            chunk_id,
            current.user.user_id,
        )
        if row is None:
            raise AppError(
                "CITATION_NOT_FOUND",
                "未找到当前可用引用，或当前用户没有查看权限。",
                404,
            )
        return CitationResponse(**row)

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/document-versions/{document_version_id}/parsing",
        response_model=ParsingPreviewResponse,
    )
    async def get_document_parsing_preview(
        knowledge_base_id: str,
        document_version_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> ParsingPreviewResponse:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "解析与切片预览需要 PostgreSQL 运行时。", 503)
        row = await run_in_threadpool(
            sources.get_parsing_preview,
            knowledge_base_id,
            document_version_id,
            current.user.user_id,
        )
        if row is None:
            raise AppError(
                "PARSING_PREVIEW_NOT_FOUND",
                "未找到解析结果，或当前用户没有查看权限。",
                404,
            )
        return ParsingPreviewResponse(**row)

    @app.post(
        "/api/knowledge-bases/{knowledge_base_id}/document-versions/{document_version_id}/reprocess",
        response_model=dict[str, str],
        status_code=202,
    )
    async def reprocess_document_version(
        knowledge_base_id: str,
        document_version_id: str,
        payload: ReprocessDocumentVersionRequest,
        knowledge_bases: KnowledgeBasesDependency,
        sources: DataSourcesDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> dict[str, str]:
        _require_admin(current.user)
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "重新处理需要 PostgreSQL 运行时。", 503)
        try:
            index_job_id = await run_in_threadpool(
                sources.reprocess_version,
                knowledge_base_id,
                document_version_id,
                chunking_version(payload.chunk_size, payload.chunk_overlap),
                settings.index_job_max_attempts,
            )
        except Exception as exc:
            if "index_jobs_one_active_version_idx" in str(exc):
                raise AppError("INDEX_JOB_ACTIVE", "该版本正在处理。", 409) from exc
            raise
        if index_job_id is None:
            raise AppError("DOCUMENT_VERSION_NOT_FOUND", "未找到该文档版本。", 404)
        await _record_audit(
            audit,
            "document.version.reprocess",
            current.user,
            "document_version",
            document_version_id,
            metadata={"knowledge_base_id": knowledge_base_id},
        )
        return {"index_job_id": index_job_id}

    @app.get("/api/evaluation-center/overview", response_model=EvaluationCenterOverviewResponse)
    async def get_evaluation_center_overview(
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
    ) -> EvaluationCenterOverviewResponse:
        return await run_in_threadpool(reports.center_overview)

    @app.get("/api/evaluation-center/pipeline", response_model=PipelineEvaluationResponse)
    async def get_pipeline_evaluation(
        governance: EvaluationGovernanceDependency,
        current: CurrentSessionDependency,
        knowledge_base_id: str | None = Query(default=None),
        data_source_id: str | None = Query(default=None),
    ) -> PipelineEvaluationResponse:
        if governance is None:
            raise AppError("POSTGRES_REQUIRED", "工程指标需要 PostgreSQL 运行时。", 503)
        summary = await run_in_threadpool(governance.pipeline_summary, knowledge_base_id, data_source_id)
        return PipelineEvaluationResponse(**summary)

    @app.get(
        "/api/evaluation-center/bad-cases",
        response_model=list[GovernedBadCaseResponse],
    )
    async def list_governed_bad_cases(
        governance: EvaluationGovernanceDependency,
        current: CurrentSessionDependency,
        knowledge_base_id: str | None = Query(default=None),
        status: str | None = Query(default=None),
        severity: str | None = Query(default=None),
        failure_stage: str | None = Query(default=None),
        limit: PageLimit = 100,
    ) -> list[GovernedBadCaseResponse]:
        if governance is None:
            raise AppError("POSTGRES_REQUIRED", "Bad Case 治理需要 PostgreSQL 运行时。", 503)
        items = await run_in_threadpool(
            governance.list_bad_cases,
            knowledge_base_id=knowledge_base_id,
            status=status,
            severity=severity,
            failure_stage=failure_stage,
            limit=limit,
        )
        return [GovernedBadCaseResponse(**item) for item in items]

    @app.put(
        "/api/evaluation-center/bad-cases/{case_id}",
        response_model=GovernedBadCaseResponse,
    )
    async def update_governed_bad_case(
        case_id: str,
        payload: GovernedBadCaseUpdate,
        governance: EvaluationGovernanceDependency,
        current: CurrentSessionDependency,
    ) -> GovernedBadCaseResponse:
        _require_admin(current.user)
        if governance is None:
            raise AppError("POSTGRES_REQUIRED", "Bad Case 治理需要 PostgreSQL 运行时。", 503)
        try:
            item = await run_in_threadpool(
                governance.update_bad_case,
                case_id,
                BadCaseUpdate(**payload.model_dump()),
            )
        except ValueError as exc:
            raise AppError("INVALID_BAD_CASE_TRANSITION", str(exc), 409) from exc
        if item is None:
            raise AppError("BAD_CASE_NOT_FOUND", "未找到该 Bad Case。", 404)
        return GovernedBadCaseResponse(**item)

    @app.get(
        "/api/evaluation-center/acceptance-runs",
        response_model=list[AcceptanceRunResponse],
    )
    async def list_acceptance_runs(
        governance: EvaluationGovernanceDependency,
        current: CurrentSessionDependency,
        knowledge_bases: KnowledgeBasesDependency,
        auth: AuthRepositoryDependency,
        knowledge_base_id: str = Query(),
        limit: PageLimit = 50,
    ) -> list[AcceptanceRunResponse]:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if governance is None:
            raise AppError("POSTGRES_REQUIRED", "链路验收需要 PostgreSQL 运行时。", 503)
        items = await run_in_threadpool(governance.list_acceptance_runs, knowledge_base_id, limit)
        return [AcceptanceRunResponse(**item) for item in items]

    @app.post(
        "/api/evaluation-center/acceptance-runs",
        response_model=AcceptanceRunResponse,
        status_code=201,
    )
    async def start_acceptance_run(
        payload: AcceptanceRunCreate,
        governance: EvaluationGovernanceDependency,
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
        knowledge_bases: KnowledgeBasesDependency,
        auth: AuthRepositoryDependency,
    ) -> AcceptanceRunResponse:
        _require_admin(current.user)
        await _require_accessible_knowledge_base(
            knowledge_bases,
            auth,
            current.user,
            payload.knowledge_base_id,
        )
        if governance is None:
            raise AppError("POSTGRES_REQUIRED", "链路验收需要 PostgreSQL 运行时。", 503)
        overview = await run_in_threadpool(reports.center_overview)
        retrieval_passed = bool(overview.retrieval_report and overview.retrieval_report.passed)
        answer_passed = bool(overview.answer_report and overview.answer_report.passed)
        retrieval_detail = (
            await run_in_threadpool(reports.get_official, overview.retrieval_report.report_id)
            if overview.retrieval_report
            else None
        )
        acl_leak_count = int(retrieval_detail.acl_leak_count or 0) if retrieval_detail else 0
        item = await run_in_threadpool(
            governance.run_acceptance,
            payload.knowledge_base_id,
            current.user.user_id,
            retrieval_passed,
            answer_passed,
            acl_leak_count,
        )
        return AcceptanceRunResponse(**item)

    @app.get("/api/evaluations", response_model=list[EvaluationReportSummary])
    async def list_evaluations(
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[EvaluationReportSummary]:
        items = await run_in_threadpool(reports.list_official)
        return _page(items, offset, limit)

    @app.get("/api/evaluations/{report_id}", response_model=EvaluationReportResponse)
    async def get_evaluation(
        report_id: str,
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
    ) -> EvaluationReportResponse:
        return await run_in_threadpool(reports.get_official, report_id)

    @app.get(
        "/api/evaluations/answers/reports",
        response_model=list[AnswerEvaluationReportSummary],
    )
    async def list_answer_evaluations(
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[AnswerEvaluationReportSummary]:
        items = await run_in_threadpool(reports.list_official_answers)
        return _page(items, offset, limit)

    @app.get(
        "/api/evaluations/answers/reports/{report_id}",
        response_model=AnswerEvaluationReportResponse,
    )
    async def get_answer_evaluation(
        report_id: str,
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
    ) -> AnswerEvaluationReportResponse:
        return await run_in_threadpool(reports.get_official_answer, report_id)

    @app.delete("/api/documents/{document_id}", status_code=204)
    async def delete_document(
        document_id: str,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        _require_knowledge_base_access(auth, current.user, DEFAULT_KNOWLEDGE_BASE_ID)
        await _delete_document(
            document_id,
            service,
            DEFAULT_KNOWLEDGE_BASE_ID,
            settings,
            audit,
            current.user,
        )

    @app.delete(
        "/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
        status_code=204,
    )
    async def delete_scoped_document(
        knowledge_base_id: str,
        document_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> None:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        await _delete_document(
            document_id,
            service,
            knowledge_base_id,
            settings,
            audit,
            current.user,
        )

    @app.post("/api/query", response_model=QueryResponse)
    async def query(
        payload: QueryRequest,
        service: ServiceDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> QueryResponse:
        _require_knowledge_base_access(auth, current.user, DEFAULT_KNOWLEDGE_BASE_ID)
        if payload.rerank_k > payload.retrieve_k:
            raise AppError("INVALID_TOP_K", "rerank_k 不能大于 retrieve_k。")
        return await _execute_recorded_query(
            payload,
            service,
            conversations,
            DEFAULT_KNOWLEDGE_BASE_ID,
            settings,
            abuse_protection,
            metrics,
            current.user.user_id,
        )

    @app.post(
        "/api/knowledge-bases/{knowledge_base_id}/query",
        response_model=QueryResponse,
    )
    async def query_knowledge_base(
        knowledge_base_id: str,
        payload: QueryRequest,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> QueryResponse:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if payload.rerank_k > payload.retrieve_k:
            raise AppError("INVALID_TOP_K", "rerank_k 不能大于 retrieve_k。")
        return await _execute_recorded_query(
            payload,
            service,
            conversations,
            knowledge_base_id,
            settings,
            abuse_protection,
            metrics,
            current.user.user_id,
        )

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/conversations",
        response_model=list[ConversationSummaryResponse],
    )
    async def list_conversations(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[ConversationSummaryResponse]:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        items = await run_in_threadpool(
            conversations.list_conversations, knowledge_base_id, current.user.user_id
        )
        return [ConversationSummaryResponse(**item) for item in _page(items, offset, limit)]

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/bad-cases",
        response_model=list[BadCaseResponse],
    )
    async def list_bad_cases(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        category: str | None = Query(default=None, max_length=80),
        error_code: str | None = Query(default=None, max_length=80),
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[BadCaseResponse]:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        if created_from and created_to and created_from > created_to:
            raise AppError("INVALID_TIME_RANGE", "created_from 不能晚于 created_to。")
        items = await run_in_threadpool(
            conversations.list_bad_cases,
            knowledge_base_id,
            current.user.user_id,
            category,
            error_code,
            created_from,
            created_to,
        )
        return [BadCaseResponse(**item) for item in _page(items, offset, limit)]

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}",
        response_model=ConversationDetailResponse,
    )
    async def get_conversation(
        knowledge_base_id: str,
        conversation_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> ConversationDetailResponse:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        try:
            item = await run_in_threadpool(
                conversations.get_conversation,
                knowledge_base_id,
                conversation_id,
                current.user.user_id,
            )
        except ValueError as exc:
            raise AppError("CONVERSATION_NOT_FOUND", "未找到该会话。", 404) from exc
        if item is None:
            raise AppError("CONVERSATION_NOT_FOUND", "未找到该会话。", 404)
        return ConversationDetailResponse(**item)

    @app.delete(
        "/api/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}",
        status_code=204,
    )
    async def delete_conversation(
        knowledge_base_id: str,
        conversation_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> None:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        try:
            deleted = await run_in_threadpool(
                conversations.delete_conversation,
                knowledge_base_id,
                conversation_id,
                current.user.user_id,
            )
        except ValueError as exc:
            raise AppError("CONVERSATION_NOT_FOUND", "未找到该会话。", 404) from exc
        if not deleted:
            raise AppError("CONVERSATION_NOT_FOUND", "未找到该会话。", 404)

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/answers/{record_id}",
        response_model=AnswerRecordResponse,
    )
    async def get_answer_record(
        knowledge_base_id: str,
        record_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
    ) -> AnswerRecordResponse:
        await _require_accessible_knowledge_base(knowledge_bases, auth, current.user, knowledge_base_id)
        try:
            item = await run_in_threadpool(
                conversations.get_answer,
                knowledge_base_id,
                record_id,
                current.user.user_id,
            )
        except ValueError as exc:
            raise AppError("ANSWER_RECORD_NOT_FOUND", "未找到该回答记录。", 404) from exc
        if item is None:
            raise AppError("ANSWER_RECORD_NOT_FOUND", "未找到该回答记录。", 404)
        return AnswerRecordResponse(**item)

    return app


app = create_app()


def _user_response(user: UserRecord) -> UserResponse:
    return UserResponse(**user.to_public())


def _auth_token_response(session: AuthenticatedSession) -> AuthTokenResponse:
    return AuthTokenResponse(
        access_token=session.token,
        expires_at=session.expires_at,
        user=_user_response(session.user),
    )


def _client_key(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _page[T](items: list[T], offset: int, limit: int) -> list[T]:
    return items[offset : offset + limit]


def _require_admin(user: UserRecord) -> None:
    if user.role != "admin":
        raise AppError("ADMIN_REQUIRED", "该操作仅限管理员。", 403)


def _require_knowledge_base_access(
    auth: AuthRepository,
    user: UserRecord,
    knowledge_base_id: str,
) -> None:
    try:
        allowed = auth.can_access_knowledge_base(user, knowledge_base_id)
    except ValueError as exc:
        raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404) from exc
    if not allowed:
        # 不向未授权成员泄露知识库是否真实存在。
        raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)


async def _require_accessible_knowledge_base(
    repository: KnowledgeBaseRepository,
    auth: AuthRepository,
    user: UserRecord,
    knowledge_base_id: str,
) -> KnowledgeBaseRecord:
    _require_knowledge_base_access(auth, user, knowledge_base_id)
    return await _require_knowledge_base(repository, knowledge_base_id)


async def _require_knowledge_base(
    repository: KnowledgeBaseRepository,
    knowledge_base_id: str,
) -> KnowledgeBaseRecord:
    try:
        record = await run_in_threadpool(repository.get, knowledge_base_id)
    except ValueError as exc:
        raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404) from exc
    if record is None:
        raise AppError("KNOWLEDGE_BASE_NOT_FOUND", "未找到该知识库。", 404)
    return record


async def _knowledge_base_response(
    record: KnowledgeBaseRecord,
    service: RAGServiceProtocol,
    user: UserRecord,
) -> KnowledgeBaseResponse:
    documents = await run_in_threadpool(service.list_documents, record.knowledge_base_id)
    statuses = {item.status for item in documents}
    if not documents:
        index_status = "empty"
    elif "failed" in statuses:
        index_status = "failed"
    elif statuses & {"queued", "pending", "indexing", "processing"}:
        index_status = "processing"
    else:
        index_status = "ready"
    upload_path = KnowledgeBaseScope(
        record.knowledge_base_id,
        get_settings().upload_path,
    ).upload_path
    source_file_bytes = (
        sum(item.stat().st_size for item in upload_path.iterdir() if item.is_file())
        if upload_path.exists()
        else 0
    )
    allowed_actions = ["detail"]
    if user.role == "admin":
        allowed_actions.append("edit")
        if not record.is_default and not documents and index_status != "processing":
            allowed_actions.append("delete")
    return KnowledgeBaseResponse(
        **record.to_json(),
        document_count=len(documents),
        chunk_count=sum(item.chunk_count for item in documents),
        source_file_bytes=source_file_bytes,
        index_status=index_status,
        current_user_permission="admin" if user.role == "admin" else "use",
        allowed_actions=allowed_actions,
    )


def _data_source_response(row: dict[str, object], user: UserRecord) -> DataSourceResponse:
    raw_status = str(row.get("sync_status") or "idle")
    index_status = raw_status if raw_status in {"queued", "running", "succeeded", "failed"} else "idle"
    upload_status = "succeeded" if row.get("upload_status") == "succeeded" else "idle"
    acl = dict(row.get("acl") or {})
    actions = ["detail"]
    source_type = str(row.get("source_type") or "file")
    if source_type == "file":
        actions.append("update_file")
    if user.role == "admin":
        actions.extend(["edit", "disable" if row["enabled"] else "enable"])
        if source_type in {"local_directory", "object_storage"} and row["enabled"]:
            actions.extend(["test", "sync"])
        if not row["document_count"] and index_status not in {"queued", "running"}:
            actions.append("delete")
    configuration = dict(row.get("configuration") or {})
    normalized = {
        **row,
        "configuration": {
            key: value
            for key, value in configuration.items()
            if key not in {"default_category_id", "metadata_defaults"}
        },
        "default_category_id": configuration.get("default_category_id"),
        "metadata_defaults": dict(configuration.get("metadata_defaults") or {}),
        "upload_status": upload_status,
        "index_status": index_status,
        "sync_status": (
            raw_status
            if raw_status in {"idle", "queued", "running", "succeeded", "failed", "aborted"}
            else "idle"
        ),
        "last_indexed_at": row.get("last_indexed_at") or row.get("last_synced_at"),
        "acl_version": int(acl.get("version", 1)),
        "allow_user_ids": list(acl.get("allow_user_ids", [])),
        "deny_user_ids": list(acl.get("deny_user_ids", [])),
        "allowed_actions": actions,
    }
    return DataSourceResponse(**normalized)


async def _upload_document(
    file: UploadFile,
    service: RAGServiceProtocol,
    knowledge_base_id: str,
    settings,
    abuse_protection: AbuseProtection,
    metrics: MetricsRegistry,
    audit: AuditRepository,
    user: UserRecord,
    metadata: dict[str, object] | None = None,
) -> DocumentInfo:
    abuse_protection.check_expensive(user.user_id)
    started = time.perf_counter()
    try:
        content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise AppError("FILE_TOO_LARGE", f"文件不能超过 {settings.max_upload_mb} MB。", 413)
        filename = validate_upload(
            file.filename,
            file.content_type,
            content,
            max_filename_chars=settings.max_filename_chars,
        )
        with abuse_protection.concurrency.slot():
            arguments = (filename, content, knowledge_base_id)
            result = await run_in_threadpool(
                service.index_document,
                *arguments,
                **({"metadata": metadata} if metadata is not None else {}),
            )
    except Exception:
        metrics.record_index(_duration_ms(started), failed=True)
        await _record_audit(
            audit,
            "document.upload",
            user,
            "knowledge_base",
            knowledge_base_id,
            result="failed",
        )
        raise
    metrics.record_index(_duration_ms(started), failed=False)
    await _record_audit(audit, "document.upload", user, "document", result.document_id)
    return result


async def _delete_document(
    document_id: str,
    service: RAGServiceProtocol,
    knowledge_base_id: str,
    settings,
    audit: AuditRepository,
    user: UserRecord,
) -> None:
    deleted = await run_in_threadpool(service.delete_document, document_id, knowledge_base_id)
    if not deleted:
        raise AppError("DOCUMENT_NOT_FOUND", "未找到该文档。", 404)
    await _record_audit(audit, "document.delete", user, "document", document_id)


async def _execute_recorded_query(
    payload: QueryRequest,
    service: RAGServiceProtocol,
    conversations: ConversationRepository,
    knowledge_base_id: str,
    settings,
    abuse_protection: AbuseProtection,
    metrics: MetricsRegistry,
    user_id: str,
) -> QueryResponse:
    abuse_protection.check_expensive(user_id)
    question = payload.question.strip()
    try:
        conversation = await run_in_threadpool(
            conversations.resolve_conversation,
            knowledge_base_id,
            question,
            payload.conversation_id,
            user_id,
        )
    except (LookupError, PermissionError, ValueError) as exc:
        raise AppError("CONVERSATION_NOT_FOUND", "未找到该知识库中的会话。", 404) from exc

    started = time.perf_counter()
    try:
        with abuse_protection.concurrency.slot():
            result = await run_in_threadpool(
                service.query,
                question,
                payload.retrieve_k,
                payload.rerank_k,
                knowledge_base_id,
                payload.filters,
                RetrievalAccessContext(user_id),
            )
    except AppError as exc:
        failure_latency = {"total": _duration_ms(started)}
        metrics.record_rag(failure_latency, retrieval_failed=True, answer_failed=True)
        error_details = dict(exc.details) if isinstance(exc.details, dict) else {}
        record = await run_in_threadpool(
            conversations.record,
            conversation_id=conversation["conversation_id"],
            knowledge_base_id=knowledge_base_id,
            question=question,
            status="failed",
            answer=None,
            sources=[],
            latency_ms=failure_latency,
            models={
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
                "generation": settings.generation_model,
            },
            model_metadata={"configured_model": settings.generation_model},
            prompt_version=None,
            prompt_hash=None,
            query_metadata=error_details.get("query_metadata"),
            bad_case_category=error_details.get("bad_case_category"),
            error_code=exc.code,
            error_message=exc.message,
        )
        governance = get_evaluation_governance()
        if governance and error_details.get("bad_case_category"):
            await run_in_threadpool(
                _capture_online_bad_case,
                governance,
                record_id=record["record_id"],
                knowledge_base_id=knowledge_base_id,
                question=question,
                category=str(error_details["bad_case_category"]),
                answer_status=None,
                answer=None,
                source_ids=[],
            )
        details = error_details
        details.update(
            {
                "conversation_id": conversation["conversation_id"],
                "record_id": record["record_id"],
            }
        )
        raise AppError(exc.code, exc.message, exc.status_code, details) from exc

    record_status = (
        "success"
        if result.answer_status in {"answered", "insufficient_evidence", "source_conflict"}
        else "failed"
    )
    metrics.record_rag(
        result.latency_ms,
        retrieval_failed=False,
        answer_failed=record_status == "failed",
    )
    record = await run_in_threadpool(
        conversations.record,
        conversation_id=conversation["conversation_id"],
        knowledge_base_id=knowledge_base_id,
        question=question,
        status=record_status,
        answer=result.answer,
        sources=[item.model_dump(mode="json") for item in result.sources],
        latency_ms=result.latency_ms,
        models=result.models,
        model_metadata=result.model_metadata,
        prompt_version=result.prompt_version,
        prompt_hash=result.prompt_hash,
        answer_status=result.answer_status,
        generation_governance=(
            result.generation_governance.model_dump(mode="json") if result.generation_governance else None
        ),
        query_metadata=(result.query_metadata.model_dump(mode="json") if result.query_metadata else None),
        bad_case_category=(f"answer_{result.answer_status}" if record_status == "failed" else None),
        error_code=result.error_code,
        error_message=result.error_message,
    )
    if record_status == "failed":
        governance = get_evaluation_governance()
        if governance:
            await run_in_threadpool(
                _capture_online_bad_case,
                governance,
                record_id=record["record_id"],
                knowledge_base_id=knowledge_base_id,
                question=question,
                category=f"answer_{result.answer_status}",
                answer_status=result.answer_status,
                answer=result.answer,
                source_ids=[item.chunk_id for item in result.sources],
            )
    return result.model_copy(
        update={
            "conversation_id": conversation["conversation_id"],
            "record_id": record["record_id"],
        }
    )


def _capture_online_bad_case(
    repository: PostgresEvaluationGovernanceRepository,
    *,
    record_id: str,
    knowledge_base_id: str,
    question: str,
    category: str,
    answer_status: str | None,
    answer: str | None,
    source_ids: list[str],
) -> str:
    failure_stage = "generation" if category.startswith("answer_") else "retrieval"
    if category.startswith(("parsing_", "chunking_")):
        failure_stage = "processing"
    if category.startswith("pipeline_"):
        failure_stage = "pipeline"
    return repository.capture_online_bad_case(
        record_id=record_id,
        knowledge_base_id=knowledge_base_id,
        question=question,
        category=category,
        failure_stage=failure_stage,
        actual_answer_status=answer_status,
        actual_answer=answer,
        actual_source_ids=source_ids,
    )


async def _record_audit(
    audit: AuditRepository,
    action: str,
    actor: UserRecord | None,
    resource_type: str,
    resource_id: str | None,
    *,
    result: str = "success",
    metadata: dict[str, str | bool | int | float] | None = None,
) -> None:
    await run_in_threadpool(
        audit.record,
        action,
        actor_id=actor.user_id if actor is not None else None,
        actor_role=actor.role if actor is not None else None,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        metadata=metadata,
    )


def _anonymous_actor(value: str) -> str:
    return hash_identifier(value.casefold())


def _duration_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
