import re
from dataclasses import dataclass
from pathlib import Path

# V2 的单知识库数据统一归入这个稳定 ID，后续多知识库功能不得改变它。
DEFAULT_KNOWLEDGE_BASE_ID = "kb_default"
_KNOWLEDGE_BASE_ID_PATTERN = re.compile(r"^kb_[a-z0-9][a-z0-9_-]{0,62}$")


def validate_knowledge_base_id(knowledge_base_id: str) -> str:
    """校验可用于 Chroma metadata 和文件目录的知识库 ID。"""

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
