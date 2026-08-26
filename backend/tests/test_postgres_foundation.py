from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import chromadb
import psycopg
import pytest
from psycopg import sql

from backend.app.config import Settings
from backend.app.database import apply_migrations, check_schema_version, migration_files
from backend.app.postgres_documents import IndexWorker, PostgresAsyncRAGService
from backend.app.postgres_repositories import (
    PostgresAuthRepository,
    PostgresDataSourceRepository,
    PostgresKnowledgeBaseRepository,
)
from scripts import legacy_to_postgres, postgres_backup


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
        "0004_index_embedding_guard.sql",
    ]


def test_source_fingerprint_changes_with_content(tmp_path: Path) -> None:
    auth = tmp_path / "auth/store.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("one", encoding="utf-8")
    first = legacy_to_postgres.fingerprint(legacy_to_postgres.source_manifest(tmp_path))
    auth.write_text("two", encoding="utf-8")
    second = legacy_to_postgres.fingerprint(legacy_to_postgres.source_manifest(tmp_path))
    assert first != second


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

    assert apply_migrations(database_url) == 4
    check_schema_version(database_url, 4)
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
def test_legacy_migration_is_atomic_idempotent_and_invalidates_sessions(tmp_path: Path) -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    assert apply_migrations(database_url) == 4
    check_schema_version(database_url, 4)

    now = "2026-08-22T00:00:00+00:00"
    auth = tmp_path / "auth/store.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(
        json.dumps(
            {
                "version": 1,
                "users": [
                    {
                        "user_id": "usr_0123456789abcdef",
                        "username": "admin",
                        "display_name": "Admin",
                        "role": "admin",
                        "active": True,
                        "password_hash": "preserved-hash",
                        "created_at": now,
                        "updated_at": now,
                    }
                ],
                "sessions": [
                    {
                        "session_id": "ses_0123456789abcdef",
                        "user_id": "usr_0123456789abcdef",
                        "token_hash": "a" * 64,
                        "created_at": now,
                        "expires_at": "2027-08-22T00:00:00+00:00",
                        "revoked_at": None,
                    }
                ],
                "memberships": [{"user_id": "usr_0123456789abcdef", "knowledge_base_id": "kb_default"}],
            }
        ),
        encoding="utf-8",
    )
    registry = tmp_path / "knowledge_bases/registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "knowledge_bases": [
                    {
                        "knowledge_base_id": "kb_default",
                        "name": "默认知识库",
                        "description": "",
                        "created_at": now,
                        "updated_at": now,
                        "is_default": True,
                    },
                    {
                        "knowledge_base_id": "kb_secondary",
                        "name": "独立知识库",
                        "description": "",
                        "created_at": now,
                        "updated_at": now,
                        "is_default": False,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    upload = tmp_path / "uploads/kb_default/guide.md"
    upload.parent.mkdir(parents=True)
    upload.write_text("production guide", encoding="utf-8")
    second_upload = tmp_path / "uploads/kb_secondary/guide.md"
    second_upload.parent.mkdir(parents=True)
    second_upload.write_text("secondary guide", encoding="utf-8")
    collection = chromadb.PersistentClient(path=str(tmp_path / "chroma")).get_or_create_collection(
        "rongrag_documents"
    )
    collection.add(
        ids=["doc_guide:chunk:00000", "kb_secondary:doc_guide:chunk:00000"],
        documents=["production guide", "secondary guide"],
        embeddings=[[0.1, 0.2, 0.3], [0.3, 0.2, 0.1]],
        metadatas=[
            {
                "knowledge_base_id": "kb_default",
                "document_id": "doc_guide",
                "filename": "guide.md",
                "chunk_index": 0,
            },
            {
                "knowledge_base_id": "kb_secondary",
                "document_id": "doc_guide",
                "filename": "guide.md",
                "chunk_index": 0,
            },
        ],
    )

    first = legacy_to_postgres.migrate(tmp_path, database_url, "rongrag_documents")
    second = legacy_to_postgres.migrate(tmp_path, database_url, "rongrag_documents")
    assert first == second
    assert first == {
        "users": 1,
        "knowledge_bases": 2,
        "memberships": 1,
        "sessions": 0,
        "documents": 2,
        "document_versions": 2,
        "chunks": 2,
    }
    with psycopg.connect(database_url) as connection:
        assert connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        version = connection.execute(
            """SELECT source_file_bytes, source_path FROM document_versions
            WHERE knowledge_base_id = 'kb_default'"""
        ).fetchone()
        assert version == (len(b"production guide"), "uploads/kb_default/guide.md")

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
        assert connection.execute("SELECT count(*) FROM index_jobs").fetchone()[0] == 1
    worker = IndexWorker(settings, FakeEmbedder())
    assert worker.run_once() is True
    current = service.list_documents("kb_default")[0]
    assert current.status == "ready"
    assert current.chunk_count > 0
    source = next(item for item in data_sources.list() if item["name"] == "guide.md")
    assert source["document_count"] == 1
    assert source["upload_status"] == "succeeded"
    assert source["sync_status"] == "succeeded"
    assert source["last_indexed_at"] == source["last_synced_at"]
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

    upload.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="目标数据库不是空库"):
        legacy_to_postgres.migrate(tmp_path, database_url, "rongrag_documents")

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
        assert (restored_uploads / "kb_default/guide.md").read_text() == "changed"
