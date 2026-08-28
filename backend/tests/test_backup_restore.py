import io
import json
import re
import tarfile
from pathlib import Path

import pytest
import yaml

from scripts.backup_restore import DATASETS, create_backup, restore_backup, verify_backup

ROOT = Path(__file__).resolve().parents[2]


def seed_data(root: Path) -> None:
    payloads = {
        "knowledge_bases/registry.json": {"version": 1, "knowledge_bases": []},
        "conversations/records.json": {"version": 1, "conversations": [], "answers": []},
        "auth/store.json": {"version": 1, "users": [], "sessions": [], "memberships": []},
        "audit/events.json": {"version": 1, "events": []},
        "uploads/kb_default/guide.md": "可恢复资料",
    }
    for relative, payload in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(payload, dict):
            path.write_text(json.dumps(payload), encoding="utf-8")
        else:
            path.write_text(payload, encoding="utf-8")


def test_ci_backup_fixture_matches_supported_datasets() -> None:
    """CI 演练不得创建备份器明确不覆盖的旧数据目录。"""

    workflow = yaml.safe_load((ROOT / ".github/workflows/pytest.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["backend"]["steps"]
    rehearsal = next(step["run"] for step in steps if step["name"] == "执行隔离备份恢复演练")
    fixture_line = next(line for line in rehearsal.splitlines() if "rongrag-source/{" in line)
    match = re.search(r"rongrag-source/\{([^}]+)\}", fixture_line)

    assert match is not None
    assert set(match.group(1).split(",")) == set(DATASETS)


def test_backup_verify_and_restore_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source"
    backup = tmp_path / "backup.tar.gz"
    restored = tmp_path / "restored"
    seed_data(source)

    created = create_backup(source, backup)
    verified = verify_backup(backup)
    restored_manifest = restore_backup(backup, restored)

    assert created == verified == restored_manifest
    assert all(not entry["path"].startswith(".") for entry in created["files"])
    for entry in created["files"]:
        assert (restored / entry["path"]).read_bytes() == (source / entry["path"]).read_bytes()
        assert (restored / entry["path"]).stat().st_mode & 0o777 == 0o600


def test_backup_rejects_secret_like_files(tmp_path: Path) -> None:
    secret = tmp_path / "auth" / "service.key"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret", encoding="utf-8")

    with pytest.raises(ValueError, match="疑似密钥"):
        create_backup(tmp_path, tmp_path / "backup.tar.gz")


def test_restore_refuses_non_empty_target(tmp_path: Path) -> None:
    source = tmp_path / "source"
    seed_data(source)
    backup = tmp_path / "backup.tar.gz"
    create_backup(source, backup)
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("do not overwrite", encoding="utf-8")

    with pytest.raises(ValueError, match="必须不存在或为空"):
        restore_backup(backup, target)

    assert (target / "keep.txt").read_text(encoding="utf-8") == "do not overwrite"


def test_verify_rejects_path_traversal(tmp_path: Path) -> None:
    backup = tmp_path / "unsafe.tar.gz"
    with tarfile.open(backup, "w:gz") as archive:
        content = b"unsafe"
        info = tarfile.TarInfo("../outside")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))

    with pytest.raises(ValueError, match="不安全路径"):
        verify_backup(backup)
