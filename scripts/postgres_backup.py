from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

ARCHIVE_VERSION = 1
MANIFEST_NAME = "manifest.json"
DATABASE_DUMP_NAME = "database.dump"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def create_backup(database_url: str, uploads_root: Path, output: Path) -> dict[str, Any]:
    uploads_root = uploads_root.resolve()
    output = output.resolve()
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)
        dump = temporary / DATABASE_DUMP_NAME
        _run(
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-privileges",
                "--file",
                str(dump),
                database_url,
            ]
        )
        entries = [{"path": DATABASE_DUMP_NAME, "size": dump.stat().st_size, "sha256": sha256_file(dump)}]
        upload_files: list[Path] = []
        if uploads_root.exists():
            for path in uploads_root.rglob("*"):
                if path.is_symlink():
                    raise ValueError(f"备份不允许符号链接：{path}")
                if path.is_file():
                    upload_files.append(path)
                    relative = f"uploads/{path.relative_to(uploads_root).as_posix()}"
                    entries.append(
                        {
                            "path": relative,
                            "size": path.stat().st_size,
                            "sha256": sha256_file(path),
                        }
                    )
        manifest = {
            "format_version": ARCHIVE_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "secrets_included": False,
            "files": sorted(entries, key=lambda item: item["path"]),
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(output, "w:gz") as archive:
            encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(encoded)
            info.mode = 0o600
            archive.addfile(info, io.BytesIO(encoded))
            archive.add(dump, arcname=DATABASE_DUMP_NAME, recursive=False)
            for path in upload_files:
                archive.add(
                    path,
                    arcname=f"uploads/{path.relative_to(uploads_root).as_posix()}",
                    recursive=False,
                )
    return manifest


def _members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    result: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        pure = PurePosixPath(member.name)
        unsafe = pure.is_absolute() or ".." in pure.parts
        if unsafe or not member.isfile() or member.issym() or member.islnk():
            raise ValueError(f"备份包含不安全成员：{member.name}")
        if member.name in result:
            raise ValueError(f"备份包含重复成员：{member.name}")
        result[member.name] = member
    return result


def verify_backup(backup: Path) -> dict[str, Any]:
    with tarfile.open(backup.resolve(), "r:gz") as archive:
        members = _members(archive)
        manifest_member = members.pop(MANIFEST_NAME, None)
        if manifest_member is None:
            raise ValueError("备份缺少 manifest.json")
        source = archive.extractfile(manifest_member)
        manifest = json.load(source) if source else None
        if not isinstance(manifest, dict) or manifest.get("format_version") != ARCHIVE_VERSION:
            raise ValueError("不支持的备份格式")
        if manifest.get("secrets_included") is not False:
            raise ValueError("备份密钥边界无效")
        expected = {item["path"]: item for item in manifest.get("files", [])}
        if DATABASE_DUMP_NAME not in expected or set(expected) != set(members):
            raise ValueError("备份清单与文件不一致")
        for name, item in expected.items():
            if name != DATABASE_DUMP_NAME and PurePosixPath(name).parts[0] != "uploads":
                raise ValueError(f"备份成员越界：{name}")
            source = archive.extractfile(members[name])
            content = source.read() if source else b""
            if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError(f"备份完整性校验失败：{name}")
    return manifest


def restore_backup(backup: Path, database_url: str, uploads_target: Path) -> dict[str, Any]:
    manifest = verify_backup(backup)
    uploads_target = uploads_target.resolve()
    if uploads_target.exists() and any(uploads_target.iterdir()):
        raise ValueError("上传文件恢复目标必须不存在或为空目录")
    with tempfile.TemporaryDirectory() as temporary_name:
        temporary = Path(temporary_name)
        with tarfile.open(backup.resolve(), "r:gz") as archive:
            for name, member in _members(archive).items():
                if name == MANIFEST_NAME:
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"无法读取备份成员：{name}")
                destination = temporary / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as target:
                    shutil.copyfileobj(source, target)
        _run(
            [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "--no-privileges",
                "--dbname",
                database_url,
                str(temporary / DATABASE_DUMP_NAME),
            ]
        )
        restored_uploads = temporary / "uploads"
        uploads_target.mkdir(parents=True, exist_ok=True)
        if restored_uploads.exists():
            for source in restored_uploads.rglob("*"):
                if source.is_file():
                    destination = uploads_target / source.relative_to(restored_uploads)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL 与原始文件备份、校验和隔离恢复")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    backup.add_argument("--uploads-root", type=Path, required=True)
    backup.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    restore.add_argument("--uploads-target", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        if not args.database_url:
            raise SystemExit("必须提供数据库连接")
        result = create_backup(args.database_url, args.uploads_root, args.output)
    elif args.command == "verify":
        result = verify_backup(args.backup)
    else:
        if not args.database_url:
            raise SystemExit("必须提供数据库连接")
        result = restore_backup(args.backup, args.database_url, args.uploads_target)
    print(json.dumps({"status": "ok", "files": len(result["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
