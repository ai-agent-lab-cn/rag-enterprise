from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chromadb
import psycopg
from pgvector.psycopg import register_vector
from psycopg.types.json import Jsonb

from backend.app.database import check_schema_version

BUSINESS_TABLES = (
    "users",
    "knowledge_bases",
    "knowledge_base_memberships",
    "sessions",
    "data_sources",
    "documents",
    "document_versions",
    "chunks",
    "index_jobs",
)


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_manifest(data_root: Path) -> dict[str, Any]:
    roots = ("auth", "knowledge_bases", "uploads", "chroma")
    files: list[dict[str, Any]] = []
    for root_name in roots:
        root = data_root / root_name
        if not root.exists():
            continue
        paths = [root] if root.is_file() else root.rglob("*")
        for path in paths:
            if path.is_symlink():
                raise ValueError(f"迁移源不允许符号链接：{path}")
            if path.is_file():
                files.append(
                    {
                        "path": path.relative_to(data_root).as_posix(),
                        "size": path.stat().st_size,
                        "sha256": hash_file(path),
                    }
                )
    return {"format_version": 1, "files": sorted(files, key=lambda item: item["path"])}


def fingerprint(manifest: dict[str, Any]) -> str:
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_json(path: Path, required_keys: tuple[str, ...]) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1 or any(not isinstance(payload.get(key), list) for key in required_keys):
        raise ValueError(f"旧数据结构无效：{path}")
    return payload


def stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\0".join(parts).encode()).hexdigest()[:20]
    return f"{prefix}_{digest}"


def _target_state(connection: psycopg.Connection[Any], source_fingerprint: str) -> dict[str, Any] | None:
    prior = connection.execute(
        "SELECT imported_counts FROM legacy_migration_runs WHERE source_fingerprint = %s",
        (source_fingerprint,),
    ).fetchone()
    if prior:
        return dict(prior[0])
    occupied = [
        table
        for table in BUSINESS_TABLES
        if connection.execute(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)").fetchone()[0]
    ]
    if occupied:
        raise RuntimeError(f"目标数据库不是空库，拒绝导入：{', '.join(occupied)}")
    return None


def _read_chunks(data_root: Path, collection_name: str) -> list[dict[str, Any]]:
    chroma_path = data_root / "chroma"
    if not chroma_path.exists():
        return []
    client = chromadb.PersistentClient(path=str(chroma_path))
    if collection_name not in {item.name for item in client.list_collections()}:
        return []
    result = client.get_collection(collection_name).get(include=["documents", "metadatas", "embeddings"])
    embeddings = result.get("embeddings")
    return [
        {
            "chunk_id": chunk_id,
            "content": content or "",
            "metadata": dict(metadata or {}),
            "embedding": embedding.tolist() if hasattr(embedding, "tolist") else list(embedding),
        }
        for chunk_id, content, metadata, embedding in zip(
            result.get("ids") or [],
            result.get("documents") or [],
            result.get("metadatas") or [],
            embeddings if embeddings is not None else [],
            strict=True,
        )
    ]


def migrate(data_root: Path, database_url: str, collection_name: str) -> dict[str, int]:
    data_root = data_root.resolve()
    manifest = source_manifest(data_root)
    source_fingerprint = fingerprint(manifest)
    auth = load_json(data_root / "auth/store.json", ("users", "sessions", "memberships"))
    registry = load_json(data_root / "knowledge_bases/registry.json", ("knowledge_bases",))
    chunks = _read_chunks(data_root, collection_name)
    now = datetime.now(UTC)
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for chunk in chunks:
        metadata = chunk["metadata"]
        knowledge_base_id = str(metadata.get("knowledge_base_id") or "kb_default")
        document_id = str(metadata["document_id"])
        grouped[(knowledge_base_id, document_id)].append(chunk)

    check_schema_version(database_url, 1)
    with psycopg.connect(database_url) as connection:
        register_vector(connection)
        with connection.transaction():
            prior = _target_state(connection, source_fingerprint)
            if prior is not None:
                return {key: int(value) for key, value in prior.items()}

            for item in registry["knowledge_bases"]:
                connection.execute(
                    """INSERT INTO knowledge_bases
                    (knowledge_base_id, name, name_normalized, description, is_default,
                     created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                    (
                        item["knowledge_base_id"],
                        item["name"],
                        str(item["name"]).casefold(),
                        item.get("description", ""),
                        item.get("is_default", False),
                        item["created_at"],
                        item["updated_at"],
                    ),
                )
            for item in auth["users"]:
                connection.execute(
                    """INSERT INTO users
                    (user_id, username, username_normalized, display_name, role, active, password_hash,
                     created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        item["user_id"],
                        item["username"],
                        str(item["username"]).casefold(),
                        item["display_name"],
                        item["role"],
                        item["active"],
                        item["password_hash"],
                        item["created_at"],
                        item["updated_at"],
                    ),
                )
            for item in auth["memberships"]:
                connection.execute(
                    """INSERT INTO knowledge_base_memberships(user_id, knowledge_base_id)
                    VALUES (%s, %s)""",
                    (item["user_id"], item["knowledge_base_id"]),
                )

            for (knowledge_base_id, document_id), document_chunks in grouped.items():
                metadata = document_chunks[0]["metadata"]
                filename = str(metadata["filename"])
                source_id = stable_id("src", knowledge_base_id, document_id)
                version_id = stable_id("ver", knowledge_base_id, document_id, "1")
                file_path = data_root / "uploads" / knowledge_base_id / filename
                if not file_path.exists() and knowledge_base_id == "kb_default":
                    legacy_path = data_root / "uploads" / filename
                    file_path = legacy_path if legacy_path.exists() else file_path
                content_bytes = file_path.stat().st_size if file_path.exists() else 0
                content_hash = (
                    hash_file(file_path)
                    if file_path.exists()
                    else hashlib.sha256(
                        "".join(item["content"] for item in document_chunks).encode()
                    ).hexdigest()
                )
                source_path = file_path.relative_to(data_root).as_posix()
                if not file_path.exists():
                    source_path = f"missing/{filename}"
                created_at = metadata.get("created_at") or now
                connection.execute(
                    """INSERT INTO data_sources
                    (data_source_id, knowledge_base_id, source_type, name, configuration,
                     created_at, updated_at)
                    VALUES (%s, %s, 'file', %s, %s, %s, %s)""",
                    (
                        source_id,
                        knowledge_base_id,
                        filename,
                        Jsonb({"source_path": source_path}),
                        created_at,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO documents
                    (document_id, knowledge_base_id, data_source_id, filename, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (document_id, knowledge_base_id, source_id, filename, created_at, now),
                )
                connection.execute(
                    """INSERT INTO document_versions
                    (document_version_id, knowledge_base_id, document_id, version_number,
                     content_sha256, source_file_bytes, source_path, status, created_at, indexed_at)
                    VALUES (%s, %s, %s, 1, %s, %s, %s, 'ready', %s, %s)""",
                    (
                        version_id,
                        knowledge_base_id,
                        document_id,
                        content_hash,
                        content_bytes,
                        source_path,
                        created_at,
                        now,
                    ),
                )
                connection.execute(
                    """UPDATE documents SET current_version_id = %s
                    WHERE knowledge_base_id = %s AND document_id = %s""",
                    (version_id, knowledge_base_id, document_id),
                )
                for position, chunk in enumerate(document_chunks):
                    metadata = chunk["metadata"]
                    chunk_index = int(metadata.get("chunk_index", position))
                    connection.execute(
                        """INSERT INTO chunks
                        (chunk_id, document_version_id, knowledge_base_id, chunk_index, content,
                         metadata, embedding, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                        (
                            chunk["chunk_id"],
                            version_id,
                            knowledge_base_id,
                            chunk_index,
                            chunk["content"],
                            Jsonb(metadata),
                            chunk["embedding"],
                            created_at,
                        ),
                    )

            counts = {
                "users": len(auth["users"]),
                "knowledge_bases": len(registry["knowledge_bases"]),
                "memberships": len(auth["memberships"]),
                "sessions": 0,
                "documents": len(grouped),
                "document_versions": len(grouped),
                "chunks": len(chunks),
            }
            connection.execute(
                """INSERT INTO legacy_migration_runs
                (migration_run_id, source_fingerprint, source_manifest, imported_counts, status)
                VALUES (%s, %s, %s, %s, 'completed')""",
                (
                    stable_id("mig", source_fingerprint),
                    source_fingerprint,
                    Jsonb(manifest),
                    Jsonb(counts),
                ),
            )
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="将现有 JSON、上传文件与 Chroma 全量迁入 PostgreSQL")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--collection-name", default="rongrag_documents")
    args = parser.parse_args()
    if not args.database_url:
        raise SystemExit("必须通过 --database-url 或 DATABASE_URL 提供数据库连接")
    counts = migrate(args.data_root, args.database_url, args.collection_name)
    print(json.dumps({"status": "ok", "imported_counts": counts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
