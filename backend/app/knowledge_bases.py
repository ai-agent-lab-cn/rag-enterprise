from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

# V2 的单知识库数据统一归入这个稳定 ID，后续多知识库功能不得改变它。
DEFAULT_KNOWLEDGE_BASE_ID = "kb_default"
_KNOWLEDGE_BASE_ID_PATTERN = re.compile(r"^kb_[a-z0-9][a-z0-9_-]{0,62}$")
DEFAULT_KNOWLEDGE_BASE_NAME = "默认知识库"


def validate_knowledge_base_id(knowledge_base_id: str) -> str:
    """校验可用于分块 metadata 和文件目录的知识库 ID。"""

    if not _KNOWLEDGE_BASE_ID_PATTERN.fullmatch(knowledge_base_id):
        raise ValueError("knowledge_base_id must start with 'kb_' and contain safe lowercase characters")
    return knowledge_base_id


@dataclass(frozen=True)
class KnowledgeBaseScope:
    """集中描述同一知识库在向量索引和原始文件中的数据边界。"""

    knowledge_base_id: str
    upload_root: Path

    def __post_init__(self) -> None:
        validate_knowledge_base_id(self.knowledge_base_id)

    @property
    def upload_path(self) -> Path:
        return self.upload_root / self.knowledge_base_id

    def migrate_legacy_uploads(self) -> None:
        """把 V2 直接位于 uploads 根目录的文件幂等迁入默认知识库。"""

        if self.knowledge_base_id != DEFAULT_KNOWLEDGE_BASE_ID or not self.upload_root.exists():
            return
        legacy_files = [path for path in self.upload_root.iterdir() if path.is_file()]
        if not legacy_files:
            return
        self.upload_path.mkdir(parents=True, exist_ok=True)
        for source in legacy_files:
            target = self.upload_path / source.name
            if target.exists():
                if source.read_bytes() == target.read_bytes():
                    source.unlink()
                    continue
                raise RuntimeError(f"旧上传文件与目标文件冲突，无法自动迁移：{source.name}")
            source.replace(target)


@dataclass(frozen=True)
class KnowledgeBaseRecord:
    knowledge_base_id: str
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    is_default: bool = False

    def to_json(self) -> dict[str, str | bool]:
        return {
            "knowledge_base_id": self.knowledge_base_id,
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "is_default": self.is_default,
        }

    @classmethod
    def from_json(cls, data: dict[str, object]) -> KnowledgeBaseRecord:
        return cls(
            knowledge_base_id=validate_knowledge_base_id(str(data["knowledge_base_id"])),
            name=str(data["name"]),
            description=str(data.get("description", "")),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            updated_at=datetime.fromisoformat(str(data["updated_at"])),
            is_default=bool(data.get("is_default", False)),
        )


class KnowledgeBaseRepository:
    """以原子 JSON 写入维护轻量知识库清单；向量和文件仍存放在各自作用域。"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()
        self._ensure_registry()

    def _ensure_registry(self) -> None:
        with self._lock:
            if self.path.exists():
                # 加载一次以便启动时尽早暴露损坏的清单，而不是延迟到首个请求。
                self._load()
                return
            now = datetime.now(UTC)
            self._save(
                [
                    KnowledgeBaseRecord(
                        knowledge_base_id=DEFAULT_KNOWLEDGE_BASE_ID,
                        name=DEFAULT_KNOWLEDGE_BASE_NAME,
                        description="V2 数据迁移和兼容使用的默认知识库。",
                        created_at=now,
                        updated_at=now,
                        is_default=True,
                    )
                ]
            )

    def list(self) -> list[KnowledgeBaseRecord]:
        with self._lock:
            records = self._load()
        return sorted(records, key=lambda item: (not item.is_default, item.created_at, item.name.casefold()))

    def get(self, knowledge_base_id: str) -> KnowledgeBaseRecord | None:
        validate_knowledge_base_id(knowledge_base_id)
        return next((item for item in self.list() if item.knowledge_base_id == knowledge_base_id), None)

    def create(
        self, name: str, description: str, apply_default_category_template: bool = False
    ) -> KnowledgeBaseRecord:
        if apply_default_category_template:
            raise RuntimeError("category templates require PostgreSQL")
        with self._lock:
            records = self._load()
            self._ensure_unique_name(records, name)
            now = datetime.now(UTC)
            record = KnowledgeBaseRecord(
                knowledge_base_id=f"kb_{uuid4().hex[:12]}",
                name=name,
                description=description,
                created_at=now,
                updated_at=now,
            )
            self._save([*records, record])
            return record

    def update(self, knowledge_base_id: str, name: str, description: str) -> KnowledgeBaseRecord | None:
        validate_knowledge_base_id(knowledge_base_id)
        with self._lock:
            records = self._load()
            existing = next((item for item in records if item.knowledge_base_id == knowledge_base_id), None)
            if existing is None:
                return None
            self._ensure_unique_name(records, name, exclude_id=knowledge_base_id)
            updated = KnowledgeBaseRecord(
                knowledge_base_id=existing.knowledge_base_id,
                name=name,
                description=description,
                created_at=existing.created_at,
                updated_at=datetime.now(UTC),
                is_default=existing.is_default,
            )
            self._save([updated if item.knowledge_base_id == knowledge_base_id else item for item in records])
            return updated

    def delete(self, knowledge_base_id: str) -> bool:
        validate_knowledge_base_id(knowledge_base_id)
        if knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID:
            raise ValueError("default knowledge base cannot be deleted")
        with self._lock:
            records = self._load()
            remaining = [item for item in records if item.knowledge_base_id != knowledge_base_id]
            if len(remaining) == len(records):
                return False
            self._save(remaining)
            return True

    def _load(self) -> list[KnowledgeBaseRecord]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("version") != 1 or not isinstance(payload.get("knowledge_bases"), list):
            raise ValueError("knowledge base registry format is invalid")
        records = [KnowledgeBaseRecord.from_json(item) for item in payload["knowledge_bases"]]
        if not any(item.knowledge_base_id == DEFAULT_KNOWLEDGE_BASE_ID for item in records):
            raise ValueError("default knowledge base is missing from registry")
        return records

    def _save(self, records: list[KnowledgeBaseRecord]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(".tmp")
        temporary_path.write_text(
            json.dumps(
                {"version": 1, "knowledge_bases": [item.to_json() for item in records]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.path)

    @staticmethod
    def _ensure_unique_name(
        records: list[KnowledgeBaseRecord],
        name: str,
        exclude_id: str | None = None,
    ) -> None:
        normalized = name.casefold()
        if any(
            item.knowledge_base_id != exclude_id and item.name.casefold() == normalized
            for item in records
        ):
            raise ValueError("knowledge base name already exists")
