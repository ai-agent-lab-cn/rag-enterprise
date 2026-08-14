import time
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
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
from .schemas import (
    AnswerEvaluationReportResponse,
    AnswerEvaluationReportSummary,
    AnswerRecordResponse,
    ConversationDetailResponse,
    ConversationSummaryResponse,
    DocumentInfo,
    EvaluationReportResponse,
    EvaluationReportSummary,
    HealthResponse,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    QueryRequest,
    QueryResponse,
)
from .service import RAGService, RAGServiceProtocol
from .store import ChromaStore


# FastAPI 路由、依赖注入、上传限制和 CORS
@lru_cache
def get_service() -> RAGService:
    settings = get_settings()
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
    return KnowledgeBaseRepository(get_settings().knowledge_bases_path)


KnowledgeBasesDependency = Annotated[KnowledgeBaseRepository, Depends(get_knowledge_bases)]


@lru_cache
def get_conversations() -> ConversationRepository:
    return ConversationRepository(get_settings().conversations_path)


ConversationsDependency = Annotated[ConversationRepository, Depends(get_conversations)]


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 服务启动时迁移 V2 原始文件；只移动根目录文件，不扫描其他知识库目录。
        KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, settings.upload_path).migrate_legacy_uploads()
        get_knowledge_bases()
        get_conversations()
        if settings.demo_seed_path is not None:
            await run_in_threadpool(seed_demo_document, settings.demo_seed_path, get_service())
        yield

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url="/api/docs",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )
    install_error_handlers(app)

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            collection_ready=True,
            generation_ready=bool(settings.gemini_api_key),
            models={
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
                "generation": settings.generation_model,
            },
        )

    @app.post("/api/documents", response_model=DocumentInfo, status_code=201)
    async def upload_document(
        file: UploadedFile,
        service: ServiceDependency,
    ) -> DocumentInfo:
        return await _upload_document(file, service, DEFAULT_KNOWLEDGE_BASE_ID, settings)

    @app.get("/api/documents", response_model=list[DocumentInfo])
    async def list_documents(service: ServiceDependency) -> list[DocumentInfo]:
        return await run_in_threadpool(service.list_documents, DEFAULT_KNOWLEDGE_BASE_ID)

    @app.get("/api/knowledge-bases", response_model=list[KnowledgeBaseResponse])
    async def list_knowledge_bases(
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
    ) -> list[KnowledgeBaseResponse]:
        records = await run_in_threadpool(knowledge_bases.list)
        return [await _knowledge_base_response(item, service) for item in records]

    @app.post("/api/knowledge-bases", response_model=KnowledgeBaseResponse, status_code=201)
    async def create_knowledge_base(
        payload: KnowledgeBaseCreate,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
    ) -> KnowledgeBaseResponse:
        try:
            record = await run_in_threadpool(
                knowledge_bases.create,
                payload.name.strip(),
                payload.description.strip(),
            )
        except ValueError as exc:
            raise AppError("KNOWLEDGE_BASE_NAME_CONFLICT", "知识库名称已存在。", 409) from exc
        return await _knowledge_base_response(record, service)

    @app.get("/api/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
    async def get_knowledge_base(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
    ) -> KnowledgeBaseResponse:
        record = await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        return await _knowledge_base_response(record, service)

    @app.put("/api/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
    async def update_knowledge_base(
        knowledge_base_id: str,
        payload: KnowledgeBaseUpdate,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
    ) -> KnowledgeBaseResponse:
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
        return await _knowledge_base_response(record, service)

    @app.delete("/api/knowledge-bases/{knowledge_base_id}", status_code=204)
    async def delete_knowledge_base(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
        conversations: ConversationsDependency,
    ) -> None:
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
    ) -> DocumentInfo:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        return await _upload_document(file, service, knowledge_base_id, settings)

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/documents",
        response_model=list[DocumentInfo],
    )
    async def list_scoped_documents(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
    ) -> list[DocumentInfo]:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        return await run_in_threadpool(service.list_documents, knowledge_base_id)

    @app.get("/api/evaluations", response_model=list[EvaluationReportSummary])
    async def list_evaluations(
        reports: EvaluationReportsDependency,
    ) -> list[EvaluationReportSummary]:
        return await run_in_threadpool(reports.list_official)

    @app.get("/api/evaluations/{report_id}", response_model=EvaluationReportResponse)
    async def get_evaluation(
        report_id: str,
        reports: EvaluationReportsDependency,
    ) -> EvaluationReportResponse:
        return await run_in_threadpool(reports.get_official, report_id)

    @app.get(
        "/api/evaluations/answers/reports",
        response_model=list[AnswerEvaluationReportSummary],
    )
    async def list_answer_evaluations(
        reports: EvaluationReportsDependency,
    ) -> list[AnswerEvaluationReportSummary]:
        return await run_in_threadpool(reports.list_official_answers)

    @app.get(
        "/api/evaluations/answers/reports/{report_id}",
        response_model=AnswerEvaluationReportResponse,
    )
    async def get_answer_evaluation(
        report_id: str,
        reports: EvaluationReportsDependency,
    ) -> AnswerEvaluationReportResponse:
        return await run_in_threadpool(reports.get_official_answer, report_id)

    @app.delete("/api/documents/{document_id}", status_code=204)
    async def delete_document(
        document_id: str,
        service: ServiceDependency,
    ) -> None:
        await _delete_document(document_id, service, DEFAULT_KNOWLEDGE_BASE_ID, settings)

    @app.delete(
        "/api/knowledge-bases/{knowledge_base_id}/documents/{document_id}",
        status_code=204,
    )
    async def delete_scoped_document(
        knowledge_base_id: str,
        document_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        service: ServiceDependency,
    ) -> None:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        await _delete_document(document_id, service, knowledge_base_id, settings)

    @app.post("/api/query", response_model=QueryResponse)
    async def query(
        payload: QueryRequest,
        service: ServiceDependency,
        conversations: ConversationsDependency,
    ) -> QueryResponse:
        if payload.rerank_k > payload.retrieve_k:
            raise AppError("INVALID_TOP_K", "rerank_k 不能大于 retrieve_k。")
        return await _execute_recorded_query(
            payload,
            service,
            conversations,
            DEFAULT_KNOWLEDGE_BASE_ID,
            settings,
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
    ) -> QueryResponse:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        if payload.rerank_k > payload.retrieve_k:
            raise AppError("INVALID_TOP_K", "rerank_k 不能大于 retrieve_k。")
        return await _execute_recorded_query(
            payload,
            service,
            conversations,
            knowledge_base_id,
            settings,
        )

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/conversations",
        response_model=list[ConversationSummaryResponse],
    )
    async def list_conversations(
        knowledge_base_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
    ) -> list[ConversationSummaryResponse]:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
        items = await run_in_threadpool(conversations.list_conversations, knowledge_base_id)
        return [ConversationSummaryResponse(**item) for item in items]

    @app.get(
        "/api/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}",
        response_model=ConversationDetailResponse,
    )
    async def get_conversation(
        knowledge_base_id: str,
        conversation_id: str,
        knowledge_bases: KnowledgeBasesDependency,
        conversations: ConversationsDependency,
    ) -> ConversationDetailResponse:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
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
    ) -> None:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
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
    ) -> AnswerRecordResponse:
        await _require_knowledge_base(knowledge_bases, knowledge_base_id)
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
) -> KnowledgeBaseResponse:
    documents = await run_in_threadpool(service.list_documents, record.knowledge_base_id)
    return KnowledgeBaseResponse(
        **record.to_json(),
        document_count=len(documents),
        chunk_count=sum(item.chunk_count for item in documents),
    )


async def _upload_document(
    file: UploadFile,
    service: RAGServiceProtocol,
    knowledge_base_id: str,
    settings,
) -> DocumentInfo:
    filename = file.filename or "document.txt"
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise AppError("FILE_TOO_LARGE", f"文件不能超过 {settings.max_upload_mb} MB。", 413)
    result = await run_in_threadpool(service.index_document, filename, content, knowledge_base_id)
    scope = KnowledgeBaseScope(knowledge_base_id, settings.upload_path)
    scope.migrate_legacy_uploads()
    scope.upload_path.mkdir(parents=True, exist_ok=True)
    extension = filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else "txt"
    (scope.upload_path / f"{result.document_id}.{extension}").write_bytes(content)
    return result


async def _delete_document(
    document_id: str,
    service: RAGServiceProtocol,
    knowledge_base_id: str,
    settings,
) -> None:
    deleted = await run_in_threadpool(service.delete_document, document_id, knowledge_base_id)
    if not deleted:
        raise AppError("DOCUMENT_NOT_FOUND", "未找到该文档。", 404)
    scope = KnowledgeBaseScope(knowledge_base_id, settings.upload_path)
    scope.migrate_legacy_uploads()
    for path in scope.upload_path.glob(f"{document_id}.*"):
        path.unlink(missing_ok=True)


async def _execute_recorded_query(
    payload: QueryRequest,
    service: RAGServiceProtocol,
    conversations: ConversationRepository,
    knowledge_base_id: str,
    settings,
) -> QueryResponse:
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
        result = await run_in_threadpool(
            service.query,
            question,
            payload.retrieve_k,
            payload.rerank_k,
            knowledge_base_id,
        )
    except AppError as exc:
        record = await run_in_threadpool(
            conversations.record,
            conversation_id=conversation["conversation_id"],
            knowledge_base_id=knowledge_base_id,
            question=question,
            status="failed",
            answer=None,
            sources=[],
            latency_ms={"total": round((time.perf_counter() - started) * 1000, 2)},
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
