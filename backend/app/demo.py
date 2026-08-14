from pathlib import Path

from .knowledge_bases import DEFAULT_KNOWLEDGE_BASE_ID
from .service import RAGServiceProtocol


def seed_demo_document(path: Path | None, service: RAGServiceProtocol) -> bool:
    """为空的默认知识库导入公开演示资料，已有数据时保持幂等。"""

    if path is None:
        return False
    if not path.is_file():
        raise RuntimeError(f"演示资料不存在：{path}")
    if service.list_documents(DEFAULT_KNOWLEDGE_BASE_ID):
        return False

    service.index_document(
        path.name,
        path.read_bytes(),
        DEFAULT_KNOWLEDGE_BASE_ID,
    )
    return True
