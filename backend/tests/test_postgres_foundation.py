from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path

import chromadb
import psycopg
import pytest
from psycopg import sql

from backend.app.database import apply_migrations, check_schema_version, migration_files
from scripts import legacy_to_postgres, postgres_backup


def test_migration_files_are_contiguous() -> None:
    assert [path.name for path in migration_files()] == ["0001_postgres_foundation.sql"]


def test_source_fingerprint_changes_with_content(tmp_path: Path) -> None:
    auth = tmp_path / "auth/store.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("one", encoding="utf-8")
    first = legacy_to_postgres.fingerprint(legacy_to_postgres.source_manifest(tmp_path))
    auth.write_text("two", encoding="utf-8")
    second = legacy_to_postgres.fingerprint(legacy_to_postgres.source_manifest(tmp_path))
    assert first != second


def test_postgres_backup_manifest_and_tamper_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / "guide.md").write_text("guide", encoding="utf-8")

    def fake_run(command: list[str]) -> None:
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
    assert apply_migrations(database_url) == 1
    check_schema_version(database_url, 1)

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

    upload.write_text("changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="目标数据库不是空库"):
        legacy_to_postgres.migrate(tmp_path, database_url, "rongrag_documents")

    restore_url = os.getenv("TEST_RESTORE_DATABASE_URL")
    if restore_url:
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
            assert connection.execute("SELECT count(*) FROM users").fetchone()[0] == 1
            assert connection.execute("SELECT count(*) FROM chunks").fetchone()[0] == 2
        assert (restored_uploads / "kb_default/guide.md").read_text() == "changed"
