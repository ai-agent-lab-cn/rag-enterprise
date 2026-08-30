from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from backend.app.config import Settings
from backend.app.database import apply_migrations, check_schema_version, migration_files
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import (
    PostgresAuthRepository,
    PostgresCategoryTemplateRepository,
    PostgresDataSourceRepository,
    PostgresKnowledgeBaseRepository,
)
from scripts import postgres_backup


class FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class FailingEmbedder(FakeEmbedder):
    def encode(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding failed")


def test_migration_files_are_contiguous() -> None:
    assert [path.name for path in migration_files()] == [
        "0001_postgres_foundation.sql",
        "0002_runtime_defaults.sql",
        "0003_index_rebuild.sql",
        "0004_hybrid_retrieval.sql",
        "0005_index_embedding_guard.sql",
        "0006_document_metadata.sql",
        "0007_backfill_chunk_governance.sql",
        "0008_document_categories.sql",
        "0009_structured_parsing.sql",
        "0010_index_versions.sql",
        "0011_data_source_sync.sql",
        "0012_sync_run_governance.sql",
        "0013_evaluation_governance.sql",
        "0014_acceptance_runs.sql",
        "0015_default_category_template.sql",
    ]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_thirteen_adds_evaluation_and_bad_case_governance() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        assert apply_migrations(database_url) == 15

    with psycopg.connect(database_url) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema='public'"""
            ).fetchall()
        }
        assert {"evaluation_runs", "bad_cases", "regression_cases"} <= tables


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_fourteen_adds_acceptance_runs() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15

    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "SELECT to_regclass('public.acceptance_runs')"
        ).fetchone()[0] == "acceptance_runs"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_fifteen_adds_seeded_default_category_template() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15

    with psycopg.connect(database_url) as connection:
        template = connection.execute(
            "SELECT template_id, is_default, active FROM category_templates"
        ).fetchone()
        names = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM category_template_items ORDER BY sort_order"
            ).fetchall()
        ]
    assert template == ("category_template_default", True, True)
    assert names == ["产品资料", "技术文档", "操作手册", "运维文档", "制度规范", "常见问题"]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_new_knowledge_base_copies_active_template_as_independent_categories() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15

    templates = PostgresCategoryTemplateRepository(database_url)
    disabled = templates.create_item("停用分类", "不会复制", 700)
    templates.update_item(disabled["template_item_id"], "停用分类", "不会复制", 700, False)
    repository = PostgresKnowledgeBaseRepository(database_url)
    first = repository.create("模板知识库", "", True)
    second = repository.create("空分类知识库", "", False)

    with psycopg.connect(database_url) as connection:
        default_categories = connection.execute(
            "SELECT name, is_system FROM document_categories WHERE knowledge_base_id='kb_default'"
        ).fetchall()
        first_categories = connection.execute(
            "SELECT name, is_system FROM document_categories WHERE knowledge_base_id=%s ORDER BY sort_order",
            (first.knowledge_base_id,),
        ).fetchall()
        second_categories = connection.execute(
            "SELECT name, is_system FROM document_categories WHERE knowledge_base_id=%s",
            (second.knowledge_base_id,),
        ).fetchall()
    assert default_categories == [("未分类", True)]
    assert first_categories[0] == ("未分类", True)
    assert [item[0] for item in first_categories[1:]] == [
        "产品资料", "技术文档", "操作手册", "运维文档", "制度规范", "常见问题"
    ]
    assert second_categories == [("未分类", True)]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_twelve_adds_sync_run_governance() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15

    with psycopg.connect(database_url) as connection:
        columns = {
            row[0]
            for row in connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name='sync_runs'"""
            ).fetchall()
        }
        assert {
            "sync_run_id",
            "data_source_id",
            "status",
            "stage",
            "added_count",
            "updated_count",
            "deleted_count",
            "skipped_count",
            "failed_count",
            "cursor",
            "next_cursor",
            "retry_count",
            "error_code",
            "failure_reason",
        } <= columns
        index_job_columns = {
            row[0]
            for row in connection.execute(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_name='index_jobs'"""
            ).fetchall()
        }
        assert "sync_run_id" in index_job_columns


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_two_with_existing_data_upgrades_to_schema_three(tmp_path: Path) -> None:
    """升级必须保留已有版本和任务，且不会把历史分块伪装成新切分配置。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    old_migrations = tmp_path / "migrations"
    old_migrations.mkdir()
    for name in ("0001_postgres_foundation.sql", "0002_runtime_defaults.sql"):
        shutil.copy(Path("backend/migrations") / name, old_migrations / name)
    assert apply_migrations(database_url, old_migrations) == 2

    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, is_default, created_at, updated_at)
               VALUES ('kb_default', '默认知识库', '默认知识库', true, %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, created_at, updated_at)
               VALUES ('src_legacy', 'kb_default', 'file', 'legacy.md', %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO documents
               (document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
               VALUES ('doc_legacy', 'kb_default', 'src_legacy', 'legacy.md', %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO document_versions
               (document_version_id, knowledge_base_id, document_id, version_number,
                content_sha256, source_file_bytes, source_path, status, created_at, indexed_at)
               VALUES ('ver_legacy', 'kb_default', 'doc_legacy', 1, %s, 6,
                       'legacy.md', 'ready', %s, %s)""",
            ("a" * 64, now, now),
        )
        connection.execute(
            "UPDATE documents SET current_version_id = 'ver_legacy' WHERE document_id = 'doc_legacy'"
        )
        connection.execute(
            """INSERT INTO index_jobs
               (index_job_id, knowledge_base_id, data_source_id, document_version_id,
                idempotency_key, status, created_at, updated_at)
               VALUES ('job_legacy', 'kb_default', 'src_legacy', 'ver_legacy',
                       'index:ver_legacy', 'succeeded', %s, %s)""",
            (now, now),
        )

    assert apply_migrations(database_url) == 15
    check_schema_version(database_url, 13)
    with psycopg.connect(database_url) as connection:
        version = connection.execute(
            "SELECT status, chunking_version FROM document_versions WHERE document_version_id = 'ver_legacy'"
        ).fetchone()
        job = connection.execute(
            """SELECT status, job_type, rebuild_batch_id, target_chunking_version
               FROM index_jobs WHERE index_job_id = 'job_legacy'"""
        ).fetchone()
    assert version == ("ready", None)
    assert job == ("succeeded", "index", None, None)


def test_postgres_backup_manifest_and_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "guide.md").write_text("guide", encoding="utf-8")

    def fake_run(command: list[str], *, environment: dict[str, str] | None = None) -> None:
        assert environment is not None
        assert "postgresql://" not in " ".join(command)
        dump_path = Path(command[command.index("--file") + 1])
        dump_path.write_bytes(b"postgres-dump")

    monkeypatch.setattr(postgres_backup, "_run", fake_run)
    backup = tmp_path / "backup.tar.gz"
    postgres_backup.create_backup("postgresql://unused", uploads, backup)
    manifest = postgres_backup.verify_backup(backup)
    assert {item["path"] for item in manifest["files"]} == {"database.dump", "uploads/guide.md"}

    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(backup, "r:gz") as source, tarfile.open(tampered, "w:gz") as target:
        for member in source.getmembers():
            content = source.extractfile(member).read()
            if member.name == "uploads/guide.md":
                content = b"changed"
                member.size = len(content)
            target.addfile(member, io.BytesIO(content))
    with pytest.raises(ValueError, match="完整性"):
        postgres_backup.verify_backup(tampered)


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_postgres_runtime_covers_auth_indexing_and_backup(tmp_path: Path) -> None:
    """PostgreSQL 运行时的端到端覆盖：认证与授权、异步索引、版本升级、失败隔离与备份恢复。

    这里原本还覆盖从 Chroma 全量迁入 PostgreSQL 的原子性与幂等。Chroma 移除后该工具
    一并删除，初始数据改为直接走索引链路建立；其余断言原样保留——``PostgresAuthRepository``
    只有这一个测试覆盖它，随迁移工具一起删掉会造成实质的覆盖损失。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15
    check_schema_version(database_url, 13)

    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, description, is_default,
                created_at, updated_at)
               VALUES ('kb_default', '默认知识库', '默认知识库', '', true, %s, %s)""",
            (now, now),
        )

    bootstrap_settings = Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        index_worker_id="bootstrap-worker",
    )
    bootstrap = PostgresAsyncRAGService(bootstrap_settings, FakeEmbedder(), object(), object())
    bootstrap.index_document("guide.md", b"production guide", "kb_default")
    assert IndexWorker(bootstrap_settings, FakeEmbedder()).run_once() is True

    auth_repository = PostgresAuthRepository(database_url)
    member = auth_repository.create_user("member", "long-enough-password", "Member", "member")
    session = auth_repository.authenticate("member", "long-enough-password")
    assert session is not None
    assert auth_repository.resolve_session(session.token).user.user_id == member.user_id
    assert auth_repository.revoke_session(session.token) is True
    assert auth_repository.resolve_session(session.token) is None
    knowledge_bases = PostgresKnowledgeBaseRepository(database_url)
    created_base = knowledge_bases.create("Runtime KB", "PostgreSQL runtime")
    assert auth_repository.grant_knowledge_base(member.user_id, created_base.knowledge_base_id)
    assert auth_repository.can_access_knowledge_base(member, created_base.knowledge_base_id)

    settings = Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        index_worker_id="test-worker",
    )
    service = PostgresAsyncRAGService(settings, FakeEmbedder(), object(), object())
    queued = service.index_document("guide.md", b"updated production guide", "kb_default")
    duplicate = service.index_document("guide.md", b"updated production guide", "kb_default")
    assert queued.status == duplicate.status == "pending"
    data_sources = PostgresDataSourceRepository(database_url)
    pending_versions = data_sources.list_document_versions("kb_default")
    assert pending_versions[0]["status"] == "pending"
    assert pending_versions[0]["is_current"] is False
    with psycopg.connect(database_url) as connection:
        # 同一内容重复上传只入队一次；初始索引那条已经 succeeded，不计在内。
        assert (
            connection.execute("SELECT count(*) FROM index_jobs WHERE status = 'queued'").fetchone()[0] == 1
        )
    worker = IndexWorker(settings, FakeEmbedder())
    assert worker.run_once() is True
    current = service.list_documents("kb_default")[0]
    assert current.status == "ready"
    assert current.chunk_count > 0
    source = next(item for item in data_sources.list() if item["name"] == "guide.md")
    assert source["document_count"] == 1
    assert source["upload_status"] == "succeeded"
    # 上传型数据源没有「同步」概念：sync 状态自 V11 起读 data_sources 的真实列，
    # 从未同步过就是 idle。旧实现把它派生自最近一次 index job，等于把「索引」
    # 和「同步」混为一谈，也表达不了「同步成功但没有任何变化」。
    assert source["sync_status"] == "idle"
    assert source["last_synced_at"] is None
    assert source["last_indexed_at"] is not None
    assert source["source_file_bytes"] == len(b"updated production guide")
    versions = data_sources.list_document_versions("kb_default")
    assert versions[0]["filename"] == "guide.md"
    assert versions[0]["is_current"] is True
    assert versions[0]["version_number"] == 2
    assert data_sources.set_enabled(str(source["data_source_id"]), False)
    assert data_sources.list()[0]["enabled"] is False
    with pytest.raises(ValueError, match="has documents"):
        data_sources.delete(str(source["data_source_id"]))
    assert data_sources.set_enabled(str(source["data_source_id"]), True)
    retrieved = service.store.query([0.1, 0.2, 0.3], 5, "kb_default")
    assert retrieved
    assert retrieved[0].metadata["document_id"] == queued.document_id
    with psycopg.connect(database_url) as connection:
        current_version_before_failure = connection.execute(
            "SELECT current_version_id FROM documents WHERE knowledge_base_id = 'kb_default'"
        ).fetchone()[0]

    failure_settings = Settings(
        database_url=database_url,
        upload_path=tmp_path / "uploads",
        index_worker_id="failing-worker",
        index_job_max_attempts=1,
    )
    failure_service = PostgresAsyncRAGService(failure_settings, FakeEmbedder(), object(), object())
    failure_service.index_document("guide.md", b"broken update", "kb_default")
    assert IndexWorker(failure_settings, FailingEmbedder()).run_once() is True
    with psycopg.connect(database_url) as connection:
        assert (
            connection.execute(
                "SELECT current_version_id FROM documents WHERE knowledge_base_id = 'kb_default'"
            ).fetchone()[0]
            == current_version_before_failure
        )
        assert (
            connection.execute("SELECT count(*) FROM document_versions WHERE status = 'failed'").fetchone()[0]
            == 1
        )

    with psycopg.connect(database_url) as connection:
        source_path = connection.execute(
            """SELECT source_path FROM document_versions
               WHERE knowledge_base_id = 'kb_default' AND status = 'ready'
               ORDER BY version_number DESC LIMIT 1"""
        ).fetchone()[0]
    upload = settings.upload_path / source_path
    upload.write_text("changed", encoding="utf-8")

    restore_url = os.getenv("TEST_RESTORE_DATABASE_URL")
    if restore_url:
        with psycopg.connect(database_url) as connection:
            source_user_count = connection.execute("SELECT count(*) FROM users").fetchone()[0]
            source_chunk_count = connection.execute("SELECT count(*) FROM chunks").fetchone()[0]
        restored_database = restore_url.rsplit("/", maxsplit=1)[1]
        admin_url = restore_url.rsplit("/", maxsplit=1)[0] + "/postgres"
        with psycopg.connect(admin_url, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(restored_database))
            )
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(restored_database)))
        backup = tmp_path / "postgres-backup.tar.gz"
        postgres_backup.create_backup(database_url, tmp_path / "uploads", backup)
        restored_uploads = tmp_path / "restored-uploads"
        postgres_backup.restore_backup(backup, restore_url, restored_uploads)
        with psycopg.connect(restore_url) as connection:
            assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == source_user_count
            assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == source_chunk_count
        assert (restored_uploads / source_path).read_text() == "changed"


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_nine_with_existing_chunks_upgrades_to_schema_ten(tmp_path: Path) -> None:
    """升级必须把已有分块归入一条 active 索引版本，并固定向量列维度。

    回填不完整会让升级后的知识库检索不到任何内容：读路径按 active 索引版本过滤，
    分块的 index_version_id 为空即等同于全部消失。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    old_migrations = tmp_path / "migrations"
    old_migrations.mkdir()
    for file in migration_files():
        if int(file.name.split("_", maxsplit=1)[0]) <= 9:
            shutil.copy(file, old_migrations / file.name)
    assert apply_migrations(database_url, old_migrations) == 9

    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, is_default, created_at, updated_at)
               VALUES ('kb_default', '默认知识库', '默认知识库', true, %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, created_at, updated_at)
               VALUES ('src_legacy', 'kb_default', 'file', 'legacy.md', %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO documents
               (document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
               VALUES ('doc_legacy', 'kb_default', 'src_legacy', 'legacy.md', %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO document_versions
               (document_version_id, knowledge_base_id, document_id, version_number,
                content_sha256, source_file_bytes, source_path, status, created_at, indexed_at,
                chunking_version, parser_version)
               VALUES ('ver_legacy', 'kb_default', 'doc_legacy', 1, %s, 6, 'legacy.md',
                       'ready', %s, %s, 'v1-700-100', 'structured-1')""",
            ("a" * 64, now, now),
        )
        connection.execute(
            "UPDATE documents SET current_version_id = 'ver_legacy' WHERE document_id = 'doc_legacy'"
        )
        connection.execute(
            """INSERT INTO index_settings (embedding_model, embedding_dimension)
               VALUES ('test/embedding', 3)"""
        )
        connection.execute(
            """INSERT INTO chunks
               (chunk_id, document_version_id, knowledge_base_id, chunk_index, content,
                metadata, embedding, created_at)
               VALUES ('ver_legacy:00000', 'ver_legacy', 'kb_default', 0, '历史分块',
                       %s, '[0.1,0.2,0.3]', %s)""",
            (json.dumps({"document_id": "doc_legacy"}), now),
        )

    assert apply_migrations(database_url) == 15
    check_schema_version(database_url, 13)

    with psycopg.connect(database_url) as connection:
        version = connection.execute(
            """SELECT status, chunking_version, parser_version, embedding_model,
                      embedding_dimension, evaluation_report_id
               FROM index_versions WHERE knowledge_base_id = 'kb_default'"""
        ).fetchone()
        assert version == ("active", "v1-700-100", "structured-1", "test/embedding", 3, "legacy-backfill")
        index_version_id = connection.execute(
            "SELECT active_index_version_id FROM knowledge_bases WHERE knowledge_base_id = 'kb_default'"
        ).fetchone()[0]
        assert index_version_id.startswith("iv_")
        # 历史分块必须归入该版本，否则升级后检索为空
        assert (
            connection.execute(
                "SELECT index_version_id FROM chunks WHERE chunk_id = 'ver_legacy:00000'"
            ).fetchone()[0]
            == index_version_id
        )
        # 维度固定后才能建 HNSW 索引；无维度列会报 column does not have dimensions
        assert (
            connection.execute(
                """SELECT format_type(atttypid, atttypmod) FROM pg_attribute
               WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
            ).fetchone()[0]
            == "vector(3)"
        )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_ten_on_empty_database_keeps_embedding_unconstrained(tmp_path: Path) -> None:
    """空库没有登记过向量模型，维度留待首次索引时固定，迁移本身不猜维度。"""

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15
    with psycopg.connect(database_url) as connection:
        assert (
            connection.execute(
                """SELECT format_type(atttypid, atttypmod) FROM pg_attribute
               WHERE attrelid = 'chunks'::regclass AND attname = 'embedding'"""
            ).fetchone()[0]
            == "vector"
        )
        assert connection.execute("SELECT count(*) FROM index_versions").fetchone()[0] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_empty_knowledge_base_stays_deletable_after_indexing(tmp_path: Path) -> None:
    """索引过又清空的知识库必须仍然可删。

    索引版本记录在分块删除后依然存在，若对知识库用 RESTRICT，删除会被外键永久挡住。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)
    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, is_default, created_at, updated_at)
               VALUES ('kb_temp', '临时知识库', '临时知识库', false, %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO index_versions
               (index_version_id, knowledge_base_id, status, chunking_version, parser_version,
                embedding_model, embedding_dimension, config_fingerprint, evaluation_report_id,
                activated_at)
               VALUES ('iv_temp', 'kb_temp', 'active', 'v1-700-100', 'structured-1',
                       'test/embedding', 3, %s, 'initial-index', %s)""",
            ("b" * 64, now),
        )
        connection.execute(
            "UPDATE knowledge_bases SET active_index_version_id='iv_temp' WHERE knowledge_base_id='kb_temp'"
        )

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("DELETE FROM knowledge_bases WHERE knowledge_base_id='kb_temp'")
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM index_versions").fetchone()[0] == 0


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要 PostgreSQL + pgvector")
def test_schema_eleven_allows_sync_jobs_and_local_directory_sources(tmp_path: Path) -> None:
    """sync 任务不带 rebuild 字段也必须能插入。

    0003 的 index_jobs_rebuild_requires_batch 写的是
    `job_type = 'index' OR (rebuild 字段非空)`，新增的 sync 会落进后半句被要求提供
    rebuild 字段。迁移能过但插入失败，所以这条约束必须同步改写。
    """

    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 15
    check_schema_version(database_url, 13)

    now = datetime.now(UTC)
    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute(
            """INSERT INTO knowledge_bases
               (knowledge_base_id, name, name_normalized, is_default, created_at, updated_at)
               VALUES ('kb_default', '默认知识库', '默认知识库', true, %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO data_sources
               (data_source_id, knowledge_base_id, source_type, name, configuration,
                created_at, updated_at)
               VALUES ('ds_dir', 'kb_default', 'local_directory', '手册目录',
                       '{"root": "/mnt/docs", "include_suffixes": [".md"]}', %s, %s)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO index_jobs
               (index_job_id, knowledge_base_id, data_source_id, idempotency_key,
                status, job_type, created_at, updated_at)
               VALUES ('job_sync', 'kb_default', 'ds_dir', 'sync:ds_dir:1',
                       'queued', 'sync', %s, %s)""",
            (now, now),
        )
        assert (
            connection.execute(
                "SELECT last_sync_status FROM data_sources WHERE data_source_id='ds_dir'"
            ).fetchone()[0]
            == "idle"
        )

    # 同一数据源不得有两个活动 sync 任务
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                """INSERT INTO index_jobs
                   (index_job_id, knowledge_base_id, data_source_id, idempotency_key,
                    status, job_type, created_at, updated_at)
                   VALUES ('job_sync2', 'kb_default', 'ds_dir', 'sync:ds_dir:2',
                           'queued', 'sync', now(), now())"""
            )
