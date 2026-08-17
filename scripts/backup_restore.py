from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT_VERSION = 1
DATASETS = (
    "chroma",
    "uploads",
    "knowledge_bases",
    "conversations",
    "auth",
    "audit",
)
SECRET_NAMES = {".env", "credentials.json", "secrets.json"}
MANIFEST_NAME = "manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(data_root: Path) -> list[Path]:
    files: list[Path] = []
    for dataset in DATASETS:
        dataset_root = data_root / dataset
        if not dataset_root.exists():
            continue
        if not dataset_root.is_dir():
            raise ValueError(f"数据集路径不是目录：{dataset_root}")
        for path in dataset_root.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"备份不允许符号链接：{path}")
            if path.is_file():
                if path.name in SECRET_NAMES or path.suffix in {".key", ".pem"}:
                    raise ValueError(f"数据目录包含疑似密钥文件，已拒绝备份：{path}")
                files.append(path)
    return sorted(files, key=lambda item: item.relative_to(data_root).as_posix())


def create_backup(data_root: Path, output: Path) -> dict[str, Any]:
    data_root = data_root.resolve()
    output = output.resolve()
    files = collect_files(data_root)
    entries = [
        {
            "path": path.relative_to(data_root).as_posix(),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in files
    ]
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "datasets": list(DATASETS),
        "secrets_included": False,
        "files": entries,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, suffix=".tar.gz", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with tarfile.open(temporary, "w:gz") as archive:
            encoded = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(encoded)
            info.mode = 0o600
            info.mtime = int(time.time())
            archive.addfile(info, io.BytesIO(encoded))
            for path in files:
                archive.add(path, arcname=path.relative_to(data_root).as_posix(), recursive=False)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def _safe_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or ".." in pure.parts or member.issym() or member.islnk():
            raise ValueError(f"备份包包含不安全路径：{member.name}")
        if not member.isfile():
            raise ValueError(f"备份包只允许普通文件：{member.name}")
        if member.name in members:
            raise ValueError(f"备份包包含重复路径：{member.name}")
        members[member.name] = member
    return members


def verify_backup(backup: Path) -> dict[str, Any]:
    with tarfile.open(backup.resolve(), "r:gz") as archive:
        members = _safe_members(archive)
        manifest_member = members.pop(MANIFEST_NAME, None)
        if manifest_member is None:
            raise ValueError("备份包缺少 manifest.json")
        manifest_file = archive.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("无法读取备份清单")
        manifest = json.load(manifest_file)
        if manifest.get("format_version") != FORMAT_VERSION:
            raise ValueError("不支持的备份格式版本")
        if manifest.get("datasets") != list(DATASETS) or manifest.get("secrets_included") is not False:
            raise ValueError("备份数据集或密钥边界无效")
        expected = {entry["path"]: entry for entry in manifest.get("files", [])}
        if set(expected) != set(members):
            raise ValueError("备份清单与文件列表不一致")
        for name, entry in expected.items():
            if PurePosixPath(name).parts[0] not in DATASETS:
                raise ValueError(f"文件不属于允许的数据集：{name}")
            source = archive.extractfile(members[name])
            if source is None:
                raise ValueError(f"无法读取备份文件：{name}")
            content = source.read()
            if len(content) != entry["size"] or hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise ValueError(f"备份文件完整性校验失败：{name}")
    return manifest


def restore_backup(backup: Path, target: Path) -> dict[str, Any]:
    target = target.resolve()
    if target.exists() and any(target.iterdir()):
        raise ValueError("恢复目标必须不存在或为空目录")
    manifest = verify_backup(backup)
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(backup.resolve(), "r:gz") as archive:
        members = _safe_members(archive)
        members.pop(MANIFEST_NAME)
        for name, member in members.items():
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"无法读取备份文件：{name}")
            with destination.open("xb") as output:
                shutil.copyfileobj(source, output)
            destination.chmod(0o600)
    validate_restored_data(target)
    return manifest


def validate_restored_data(target: Path) -> None:
    json_stores = {
        "knowledge_bases/registry.json": ("version", "knowledge_bases"),
        "conversations/records.json": ("version", "conversations", "answers"),
        "auth/store.json": ("version", "users", "sessions", "memberships"),
        "audit/events.json": ("version", "events"),
    }
    for relative, keys in json_stores.items():
        path = target / relative
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or any(key not in payload for key in keys):
            raise ValueError(f"恢复后的数据结构无效：{relative}")


def main() -> None:
    parser = argparse.ArgumentParser(description="RongRAG Studio 备份、校验与隔离恢复工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("--data-root", type=Path, required=True)
    backup_parser.add_argument("--output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--backup", type=Path, required=True)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("--backup", type=Path, required=True)
    restore_parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        manifest = create_backup(args.data_root, args.output)
    elif args.command == "verify":
        manifest = verify_backup(args.backup)
    else:
        manifest = restore_backup(args.backup, args.target)
    print(json.dumps({"status": "ok", "files": len(manifest["files"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
