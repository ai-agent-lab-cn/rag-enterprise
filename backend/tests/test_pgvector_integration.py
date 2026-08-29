"""pgvector 上的端到端集成行为：入库、检索来源、生成失败降级、知识库隔离、API 真实链路。

原 test_chroma_integration.py 用 Chroma 当存储验证的是同一批业务行为；Chroma 移除后
改走 PostgresAsyncRAGService + IndexWorker 的真实索引链路重写。与 Chroma 版最大的差别
是索引异步：index_document 只入队，必须先跑 Worker 才有分块可检索。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings, get_settings
from backend.app.database import apply_migrations
from backend.app.errors import AppError
from backend.app.main import (
    create_app,
    get_audit_repository,
    get_auth_repository,
    get_conversations,
    get_knowledge_bases,
    get_service,
)
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import PostgresDataSourceRepository

KNOWLEDGE_BASE_ID = "kb_default"
TEAM_KNOWLEDGE_BASE_ID = "kb_team"
EVIDENCE_DOCUMENT = "# 可追溯问答\n\n答案必须展示来源、段落和原文证据。".encode()


class DeterministicEmbedder:
    model_name = "deterministic-embedding-v1"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        if "来源" in text or "追溯" in text or "证据" in text:
            return [1.0, 0.0, 0.0]
        if "部署" in text or "容器" in text:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


class DeterministicReranker:
    model_name = "deterministic-reranker-v1"

    def score(self, question: str, chunks: list[str]) -> list[float]:
        del question
        return [
            2.0 if "原文证据" in chunk else 1.0 if "来源" in chunk or "追溯" in chunk else 0.0
            for chunk in chunks
        ]


class DisabledGenerator:
    model_name = "disabled-generator"
    ready = True

    def generate(self, prompt: str) -> tuple[str, dict[str, object]]:
        del prompt
        return "[STATUS: ANSWERED]\n集成测试仅验证检索链路。[来源 1]", {}


def _reset(database_url: str, *knowledge_base_ids: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        for knowledge_base_id in knowledge_base_ids:
            connection.execute(
                """INSERT INTO knowledge_bases
                   (knowledge_base_id, name, name_normalized, description, is_default,
                    created_at, updated_at)
                   VALUES (%s, %s, %s, '', %s, %s, %s)""",
                (
                    knowledge_base_id,
                    knowledge_base_id,
                    knowledge_base_id,
                    knowledge_base_id == KNOWLEDGE_BASE_ID,
                    now,
                    now,
                ),
            )


def _settings(tmp_path: Path, database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        embedding_model=DeterministicEmbedder.model_name,
        chunk_size=200,
        chunk_overlap=0,
        frontend_origin="http://localhost:5173",
    )


def _service(settings: Settings) -> PostgresAsyncRAGService:
    return PostgresAsyncRAGService(
        settings,
        DeterministicEmbedder(),
        DeterministicReranker(),
        DisabledGenerator(),
    )


def _drain(settings: Settings, limit: int = 20) -> int:
    worker = IndexWorker(settings, DeterministicEmbedder())
    processed = 0
    while processed < limit and worker.run_once():
        processed += 1
    return processed


@pytest.fixture
def isolated_service(tmp_path: Path) -> Iterator[PostgresAsyncRAGService]:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url, KNOWLEDGE_BASE_ID, TEAM_KNOWLEDGE_BASE_ID)
    yield _service(_settings(tmp_path, database_url))


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_pgvector_import_query_sources_and_delete(
    isolated_service: PostgresAsyncRAGService,
) -> None:
    """入库后来源必须能指回原文，删除必须同时清掉分块且第二次删除返回 False。"""

    document = isolated_service.index_document("retrieval-evidence.md", EVIDENCE_DOCUMENT)
    isolated_service.index_document(
        "deployment.md",
        "# 部署\n\n应用使用容器启动。".encode(),
    )
    # 索引是异步的：入队时还没有任何分块，跑完 Worker 才有。
    assert isolated_service.store.count(KNOWLEDGE_BASE_ID) == 0
    assert _drain(isolated_service.settings) == 2

    assert isolated_service.store.count(KNOWLEDGE_BASE_ID) == 4
    assert {item.filename for item in isolated_service.list_documents()} == {
        "deployment.md",
        "retrieval-evidence.md",
    }

    response = isolated_service.query("如何追溯答案来源？", retrieve_k=2, rerank_k=1)

    assert response.sources[0].document_id == document.document_id
    assert response.sources[0].filename == "retrieval-evidence.md"
    assert response.sources[0].paragraph == 1
    assert response.sources[0].document_version_id is not None
    assert len(response.sources[0].content_sha256 or "") == 64
    assert response.sources[0].heading_path == ["可追溯问答"]
    assert "原文证据" in response.sources[0].text
    assert response.sources[0].retrieval_score == pytest.approx(1.0)
    assert response.sources[0].rerank_score == pytest.approx(2.0)
    citation = PostgresDataSourceRepository(
        isolated_service.database_url
    ).get_citation(KNOWLEDGE_BASE_ID, response.sources[0].chunk_id, "usr_reader")
    assert citation is not None
    assert citation["document_version_id"] == response.sources[0].document_version_id
    assert citation["text"] == response.sources[0].text
    assert isolated_service.update_document_acl(
        document.document_id,
        [],
        ["usr_reader"],
    ) == 2
    assert PostgresDataSourceRepository(
        isolated_service.database_url
    ).get_citation(KNOWLEDGE_BASE_ID, response.sources[0].chunk_id, "usr_reader") is None

    assert isolated_service.delete_document(document.document_id) is True
    assert isolated_service.delete_document(document.document_id) is False
    assert isolated_service.store.count(KNOWLEDGE_BASE_ID) == 2


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_generation_failure_keeps_ranked_sources(
    isolated_service: PostgresAsyncRAGService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """生成失败只降级答案，检索到的来源和 Prompt 溯源字段必须原样返回。"""

    isolated_service.index_document(
        "failure-evidence.md",
        "# 故障策略\n\n即使模型不可用，也必须保留检索来源。".encode(),
    )
    assert _drain(isolated_service.settings) == 1

    def fail_generation(_prompt: str) -> tuple[str, dict[str, object]]:
        raise AppError("MODEL_TIMEOUT", "生成模型响应超时，请稍后重试。", 504)

    monkeypatch.setattr(isolated_service.generator, "generate", fail_generation)
    response = isolated_service.query("模型故障时如何处理？", 5, 2)

    assert response.answer_status == "generation_failed"
    assert response.error_code == "MODEL_TIMEOUT"
    assert response.sources
    assert response.prompt_version == "v5-8-grounded-governance-1"
    assert len(response.prompt_hash or "") == 64


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_retrieval_api_uses_a_real_pgvector_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API 层跑在真实 pgvector 服务上：上传、索引、检索、留档、删除必须首尾闭合。

    conftest 的 client 夹具注入的是 FakeService，覆盖不到"路由与真实存储之间是否对得上"
    ——原文件读不到、分块数不对、留档来源与本次回答不一致这类问题只有真服务能暴露，
    因此这里自建应用并把 get_service 指向真实的 PostgresAsyncRAGService。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url, KNOWLEDGE_BASE_ID)
    settings = get_settings()
    monkeypatch.setattr(settings, "database_url", database_url)
    monkeypatch.setattr(settings, "upload_path", tmp_path / "api-uploads")
    monkeypatch.setattr(settings, "embedding_model", DeterministicEmbedder.model_name)
    monkeypatch.setattr(settings, "conversations_path", tmp_path / "conversations" / "records.json")
    monkeypatch.setattr(settings, "audit_path", tmp_path / "audit" / "events.json")
    get_knowledge_bases.cache_clear()
    get_conversations.cache_clear()
    get_auth_repository.cache_clear()
    get_audit_repository.cache_clear()
    service = _service(settings)
    app = create_app()
    app.dependency_overrides[get_service] = lambda: service

    with TestClient(app) as client:
        bootstrap = client.post(
            "/api/auth/bootstrap",
            json={
                "username": "integration-admin",
                "password": "integration-test-password",
                "display_name": "集成测试管理员",
            },
        )
        assert bootstrap.status_code == 201
        client.headers["Authorization"] = f"Bearer {bootstrap.json()['access_token']}"
        uploaded = client.post(
            "/api/documents",
            files={
                "file": (
                    "retrieval-evidence.md",
                    EVIDENCE_DOCUMENT.decode(),
                    "text/markdown",
                )
            },
        )
        assert uploaded.status_code == 201
        document_id = uploaded.json()["document_id"]
        assert uploaded.json()["status"] == "pending"
        # 原始文件由服务落盘，Worker 之后要从同一路径读回来重解析。
        document_upload_path = settings.upload_path / KNOWLEDGE_BASE_ID / document_id
        assert list(document_upload_path.iterdir())

        # 索引尚未执行时检索必须明确报"处理中"，而不是谎称知识库为空。
        processing = client.post(
            "/api/query",
            json={"question": "如何追溯答案来源？", "retrieve_k": 5, "rerank_k": 1},
        )
        assert processing.status_code == 409
        assert processing.json()["error"]["code"] == "DOCUMENTS_PROCESSING"

        assert _drain(settings) == 1

        listed = client.get("/api/documents")
        assert listed.status_code == 200
        assert listed.json()[0]["document_id"] == document_id
        assert listed.json()[0]["status"] == "ready"
        assert listed.json()[0]["chunk_count"] == 2

        queried = client.post(
            "/api/query",
            json={"question": "如何追溯答案来源？", "retrieve_k": 5, "rerank_k": 1},
        )
        assert queried.status_code == 200
        assert queried.json()["sources"][0]["document_id"] == document_id
        assert queried.json()["sources"][0]["filename"] == "retrieval-evidence.md"
        assert queried.json()["prompt_version"] == "v3-grounded-answer-1"
        assert len(queried.json()["prompt_hash"]) == 64
        assert queried.json()["models"] == {
            "embedding": "deterministic-embedding-v1",
            "reranker": "deterministic-reranker-v1",
            "generation": "disabled-generator",
        }

        saved_answer = client.get(
            f"/api/knowledge-bases/{KNOWLEDGE_BASE_ID}/answers/" + queried.json()["record_id"]
        )
        assert saved_answer.status_code == 200
        assert saved_answer.json()["sources"][0]["text"].endswith("原文证据。")
        assert saved_answer.json()["prompt_hash"] == queried.json()["prompt_hash"]

        deleted = client.delete(f"/api/documents/{document_id}")
        assert deleted.status_code == 204
        assert client.get("/api/documents").json() == []
        assert list(document_upload_path.iterdir()) == []

    get_knowledge_bases.cache_clear()
    get_conversations.cache_clear()
    get_auth_repository.cache_clear()
    get_audit_repository.cache_clear()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_documents_and_results_stay_in_the_requested_knowledge_base(
    isolated_service: PostgresAsyncRAGService,
) -> None:
    """文档列表与检索结果都不得跨知识库泄漏。"""

    default_document = isolated_service.index_document(
        "default.md",
        "# 默认资料\n\n默认知识库提供来源和原文证据。".encode(),
    )
    team_document = isolated_service.index_document(
        "team.md",
        "# 团队部署\n\n团队知识库记录容器部署。".encode(),
        TEAM_KNOWLEDGE_BASE_ID,
    )
    assert _drain(isolated_service.settings) == 2

    assert {item.document_id for item in isolated_service.list_documents()} == {
        default_document.document_id
    }
    assert {
        item.document_id for item in isolated_service.list_documents(TEAM_KNOWLEDGE_BASE_ID)
    } == {team_document.document_id}

    default_response = isolated_service.query("来源证据是什么？", 5, 2)
    team_response = isolated_service.query("如何容器部署？", 5, 2, TEAM_KNOWLEDGE_BASE_ID)

    assert {source.knowledge_base_id for source in default_response.sources} == {
        KNOWLEDGE_BASE_ID
    }
    assert {source.knowledge_base_id for source in team_response.sources} == {
        TEAM_KNOWLEDGE_BASE_ID
    }
    assert all(
        source.document_id != team_document.document_id for source in default_response.sources
    )
    assert all(
        source.document_id != default_document.document_id for source in team_response.sources
    )


def _create_directory_source(database_url: str, name: str = "手册目录") -> str:
    """插入一条 local_directory 数据源，模拟同步流程已创建好的数据源。"""

    data_source_id = "ds_directory_fixture"
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES (%s, %s, 'local_directory', %s,
                       '{"root": "/mnt/docs", "include_suffixes": [".md"]}', %s, %s)""",
            (data_source_id, KNOWLEDGE_BASE_ID, name, now, now),
        )
    return data_source_id


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_index_document_keeps_same_named_files_in_different_directories_apart(
    tmp_path: Path,
) -> None:
    """目录树里不同子目录下的同名文件必须是两个文档。

    改造前 ``safe_name = Path(filename).name`` 会把 a/x.md 与 b/x.md 算成同一个
    document_id，后者覆盖前者；而且每个文件会自建一个 data_source，同步来的对象
    不会归属那个目录数据源。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url, KNOWLEDGE_BASE_ID)
    settings = _settings(tmp_path, database_url)
    service = _service(settings)
    source_id = _create_directory_source(database_url)

    first = service.index_document(
        "x.md", "来自 a 目录的内容".encode(), KNOWLEDGE_BASE_ID,
        data_source_id=source_id, relative_path="a/x.md",
    )
    second = service.index_document(
        "x.md", "来自 b 目录的内容".encode(), KNOWLEDGE_BASE_ID,
        data_source_id=source_id, relative_path="b/x.md",
    )

    assert first.document_id != second.document_id
    with psycopg.connect(database_url) as connection:
        rows = connection.execute(
            "SELECT filename, data_source_id FROM documents ORDER BY filename"
        ).fetchall()
    assert [row[0] for row in rows] == ["a/x.md", "b/x.md"]
    # 归属传入的数据源，而不是每个文件自建一个
    assert {row[1] for row in rows} == {source_id}
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM data_sources").fetchone()[0] == 1


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_index_document_rejects_traversal_in_relative_path(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url, KNOWLEDGE_BASE_ID)
    service = _service(_settings(tmp_path, database_url))
    source_id = _create_directory_source(database_url)

    with pytest.raises(AppError) as error:
        service.index_document(
            "x.md", b"content", KNOWLEDGE_BASE_ID,
            data_source_id=source_id, relative_path="../escape.md",
        )

    assert error.value.code == "SOURCE_OBJECT_KEY_INVALID"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_reprocess_version_actually_reindexes_the_document(tmp_path: Path) -> None:
    """重新处理必须真的跑完，而不是入队一个注定失败的任务。

    这个入口此前零测试覆盖。V5-5 把 worker 的 rebuild 分支改成「从 rebuild_batch_id
    反查索引版本」之后，reprocess_version 自己生成的 batch id 没有对应的索引版本，
    任务必然以「rebuild batch has no index version」失败——而它被
    POST /api/knowledge-bases/{id}/document-versions/{version}/reprocess 用着。
    """

    from backend.app.chunking import chunking_version
    from backend.app.postgres_repositories import PostgresDataSourceRepository

    database_url = os.environ["TEST_DATABASE_URL"]
    _reset(database_url, KNOWLEDGE_BASE_ID)
    settings = _settings(tmp_path, database_url)
    service = _service(settings)
    service.index_document("guide.md", EVIDENCE_DOCUMENT, KNOWLEDGE_BASE_ID)
    _drain(settings)
    with psycopg.connect(database_url) as connection:
        version_id = connection.execute(
            "SELECT current_version_id FROM documents WHERE knowledge_base_id=%s",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()[0]

    job_id = PostgresDataSourceRepository(database_url).reprocess_version(
        KNOWLEDGE_BASE_ID, version_id, chunking_version(120, 0), 1
    )
    assert job_id is not None
    assert _drain(settings) == 1

    with psycopg.connect(database_url) as connection:
        job = connection.execute(
            """SELECT status, failure_reason FROM index_jobs
               WHERE document_version_id=%s ORDER BY created_at DESC LIMIT 1""",
            (version_id,),
        ).fetchone()
        current = connection.execute(
            """SELECT v.status FROM documents d
               JOIN document_versions v ON v.document_version_id = d.current_version_id
               WHERE d.knowledge_base_id=%s""",
            (KNOWLEDGE_BASE_ID,),
        ).fetchone()[0]
    assert job[0] == "succeeded", f"重新处理失败：{job[1]}"
    assert current == "ready"
    # 重新处理不得产生新的索引版本：它是单文档操作，不是全库重建。
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM index_versions").fetchone()[0] == 1
