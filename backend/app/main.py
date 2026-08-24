import time
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, File, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .audit import AuditRepository
from .auth import AuthenticatedSession, AuthRepository, UserRecord
from .config import get_settings
from .database import check_schema_version
from .demo import seed_demo_document
from .errors import AppError, install_error_handlers
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
from .postgres_documents import PostgresAsyncRAGService
from .postgres_repositories import (
    PostgresAuthRepository,
    PostgresDataSourceRepository,
    PostgresKnowledgeBaseRepository,
)
from .schemas import (
    AnswerEvaluationReportResponse,
    AnswerEvaluationReportSummary,
    AnswerRecordResponse,
    AuditEventResponse,
    AuthBootstrapRequest,
    AuthBootstrapStatus,
    AuthLoginRequest,
    AuthTokenResponse,
    ConversationDetailResponse,
    ConversationSummaryResponse,
    DataSourceResponse,
    DocumentInfo,
    DocumentVersionResponse,
    EvaluationReportResponse,
    EvaluationReportSummary,
    HealthResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    LivenessResponse,
    MemberCreate,
    MemberUpdate,
    MetricsResponse,
    QueryRequest,
    QueryResponse,
    ReadinessResponse,
    UserResponse,
)
from .security import AbuseProtection, SecurityBoundaryMiddleware, validate_upload, write_private_file
from .service import RAGService, RAGServiceProtocol
from .store import ChromaStore


# FastAPI 路由、依赖注入、上传限制和 CORS
@lru_cache
def get_service() -> RAGService:
    settings = get_settings()
    if settings.database_url:
        return PostgresAsyncRAGService(
            settings=settings,
            embedder=get_embedding_model(),
            reranker=get_reranker(),
            generator=get_generator(),
        )
    return RAGService(
        settings=settings,
        store=ChromaStore(settings.chroma_path, settings.collection_name, settings.embedding_model),
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
                lambda: conversations.list_conversations(DEFAULT_KNOWLEDGE_BASE_ID),
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
        responses = [
            await _knowledge_base_response(item, service, current.user) for item in records
        ]
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

    @app.post("/api/data-sources/{data_source_id}/sync", response_model=DocumentInfo, status_code=202)
    async def sync_data_source(
        data_source_id: str,
        sources: DataSourcesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        auth: AuthRepositoryDependency,
        audit: AuditRepositoryDependency,
    ) -> DocumentInfo:
        if sources is None:
            raise AppError("POSTGRES_REQUIRED", "数据源同步需要 PostgreSQL 运行时。", 503)
        payload = await run_in_threadpool(sources.sync_payload, data_source_id)
        if payload is None:
            raise AppError("DATA_SOURCE_NOT_FOUND", "未找到该数据源。", 404)
        _require_knowledge_base_access(auth, current.user, str(payload["knowledge_base_id"]))
        if not payload["enabled"]:
            raise AppError("DATA_SOURCE_DISABLED", "数据源已停用，请先启用。", 409)
        if not payload["source_path"]:
            raise AppError("DATA_SOURCE_EMPTY", "数据源没有可同步的当前文件。", 409)
        source_path = (settings.upload_path / str(payload["source_path"])).resolve()
        if not source_path.is_relative_to(settings.upload_path.resolve()) or not source_path.is_file():
            raise AppError("SOURCE_FILE_MISSING", "数据源原始文件不存在。", 409)
        result = await run_in_threadpool(
            service.index_document,
            str(payload["name"]),
            source_path.read_bytes(),
            str(payload["knowledge_base_id"]),
        )
        await _record_audit(audit, "data_source.sync", current.user, "data_source", data_source_id)
        return result

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

    @app.post("/api/knowledge-bases", response_model=KnowledgeBaseResponse, status_code=201)
    async def create_knowledge_base(
        payload: KnowledgeBaseCreate,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        current: CurrentSessionDependency,
        audit: AuditRepositoryDependency,
    ) -> KnowledgeBaseResponse:
        _require_admin(current.user)
        try:
            record = await run_in_threadpool(
                knowledge_bases.create,
                payload.name.strip(),
                payload.description.strip(),
            )
        except ValueError as exc:
            raise AppError("KNOWLEDGE_BASE_NAME_CONFLICT", "知识库名称已存在。", 409) from exc
        await _record_audit(
            audit,
            "knowledge_base.create",
            current.user,
            "knowledge_base",
            record.knowledge_base_id,
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
        history = await run_in_threadpool(conversations.list_conversations, knowledge_base_id)
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
        )

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
        await _require_accessible_knowledge_base(
            knowledge_bases, auth, current.user, knowledge_base_id
        )
        if sources is None:
            return []
        rows = await run_in_threadpool(sources.list_document_versions, knowledge_base_id)
        return [DocumentVersionResponse(**row) for row in _page(rows, offset, limit)]

    @app.get("/api/evaluations", response_model=list[EvaluationReportSummary])
    async def list_evaluations(
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
        offset: PageOffset = 0,
        limit: PageLimit = 50,
    ) -> list[EvaluationReportSummary]:
        _require_admin(current.user)
        items = await run_in_threadpool(reports.list_official)
        return _page(items, offset, limit)

    @app.get("/api/evaluations/{report_id}", response_model=EvaluationReportResponse)
    async def get_evaluation(
        report_id: str,
        reports: EvaluationReportsDependency,
        current: CurrentSessionDependency,
    ) -> EvaluationReportResponse:
        _require_admin(current.user)
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
        _require_admin(current.user)
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
        _require_admin(current.user)
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
        items = await run_in_threadpool(conversations.list_conversations, knowledge_base_id)
        return [ConversationSummaryResponse(**item) for item in _page(items, offset, limit)]

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
    source_file_bytes = sum(
        item.stat().st_size for item in upload_path.iterdir() if item.is_file()
    ) if upload_path.exists() else 0
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
    actions = ["detail", "sync"]
    if user.role == "admin":
        actions.extend(["edit", "disable" if row["enabled"] else "enable"])
        if not row["document_count"] and index_status not in {"queued", "running"}:
            actions.append("delete")
    normalized = {
        **row,
        "upload_status": upload_status,
        "index_status": index_status,
        "sync_status": index_status,
        "last_indexed_at": row.get("last_indexed_at") or row.get("last_synced_at"),
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
            result = await run_in_threadpool(
                service.index_document,
                filename,
                content,
                knowledge_base_id,
            )
        if not getattr(service, "stores_source_files", False):
            scope = KnowledgeBaseScope(knowledge_base_id, settings.upload_path)
            scope.migrate_legacy_uploads()
            extension = filename.rsplit(".", maxsplit=1)[-1].lower()
            await run_in_threadpool(
                write_private_file,
                scope.upload_path / f"{result.document_id}.{extension}",
                content,
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
    scope = KnowledgeBaseScope(knowledge_base_id, settings.upload_path)
    scope.migrate_legacy_uploads()
    for path in scope.upload_path.glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)
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
            )
    except AppError as exc:
        failure_latency = {"total": _duration_ms(started)}
        metrics.record_rag(failure_latency, retrieval_failed=True, answer_failed=True)
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
            error_code=exc.code,
            error_message=exc.message,
        )
        details = dict(exc.details) if isinstance(exc.details, dict) else {}
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
        error_code=result.error_code,
        error_message=result.error_message,
    )
    return result.model_copy(
        update={
            "conversation_id": conversation["conversation_id"],
            "record_id": record["record_id"],
        }
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
