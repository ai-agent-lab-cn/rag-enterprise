from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .errors import AppError, install_error_handlers
from .evaluation_reports import EvaluationReportRepository
from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID, KnowledgeBaseScope
from .models import get_embedding_model, get_generator, get_reranker
from .schemas import (
    DocumentInfo,
    EvaluationReportResponse,
    EvaluationReportSummary,
    HealthResponse,
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


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 服务启动时迁移 V2 原始文件；只移动根目录文件，不扫描其他知识库目录。
        KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, settings.upload_path).migrate_legacy_uploads()
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
        allow_methods=["GET", "POST", "DELETE"],
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
        filename = file.filename or "document.txt"
        content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise AppError("FILE_TOO_LARGE", f"文件不能超过 {settings.max_upload_mb} MB。", 413)
        result = await run_in_threadpool(service.index_document, filename, content)
        scope = KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, settings.upload_path)
        scope.migrate_legacy_uploads()
        scope.upload_path.mkdir(parents=True, exist_ok=True)
        extension = filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else "txt"
        (scope.upload_path / f"{result.document_id}.{extension}").write_bytes(content)
        return result

    @app.get("/api/documents", response_model=list[DocumentInfo])
    async def list_documents(service: ServiceDependency) -> list[DocumentInfo]:
        return await run_in_threadpool(service.list_documents)

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

    @app.delete("/api/documents/{document_id}", status_code=204)
    async def delete_document(
        document_id: str,
        service: ServiceDependency,
    ) -> None:
        deleted = await run_in_threadpool(service.delete_document, document_id)
        if not deleted:
            raise AppError("DOCUMENT_NOT_FOUND", "未找到该文档。", 404)
        scope = KnowledgeBaseScope(DEFAULT_KNOWLEDGE_BASE_ID, settings.upload_path)
        scope.migrate_legacy_uploads()
        for path in scope.upload_path.glob(f"{document_id}.*"):
            path.unlink(missing_ok=True)

    @app.post("/api/query", response_model=QueryResponse)
    async def query(
        payload: QueryRequest,
        service: ServiceDependency,
    ) -> QueryResponse:
        if payload.rerank_k > payload.retrieve_k:
            raise AppError("INVALID_TOP_K", "rerank_k 不能大于 retrieve_k。")
        return await run_in_threadpool(
            service.query,
            payload.question.strip(),
            payload.retrieve_k,
            payload.rerank_k,
        )

    return app


app = create_app()
