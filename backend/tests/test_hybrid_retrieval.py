"""验证 RAGService 的混合召回编排；用假存储层覆盖，不依赖 PostgreSQL。"""

import pytest
from pydantic import ValidationError

from backend.app.config import Settings
from backend.app.lexical import LexicalIndexCache
from backend.app.schemas import QueryMetadataFilter
from backend.app.service import RAGService
from backend.app.store import RetrievedChunk

CHUNKS = {
    "c1": "备份根目录固定覆盖 chroma、uploads、knowledge_bases 等六个目录。",
    "c2": "NodePort 30080 仅供本机访问，配置使用 APP_ENVIRONMENT=test。",
    "c3": "恢复目标必须是新建空数据库和空上传目录。",
    "c4": "Worker 使用 FOR UPDATE SKIP LOCKED 领取任务。",
}


def _chunk(chunk_id: str, retrieval_score: float) -> RetrievedChunk:
    governance = {
        "c1": {"category": "运维", "tags": ["备份"], "source_type": "file"},
        "c2": {"category": "部署", "tags": ["端口"], "source_type": "file"},
        "c3": {"category": "运维", "tags": ["恢复"], "source_type": "web"},
        "c4": {"category": "研发", "tags": ["任务"], "source_type": "connector"},
    }
    return RetrievedChunk(
        chunk_id=chunk_id,
        text=CHUNKS[chunk_id],
        metadata={"filename": "doc.md", "paragraph": 0, **governance[chunk_id]},
        retrieval_score=retrieval_score,
    )


class _FakeStore:
    def __init__(self, vector_order: list[str]):
        self.vector_order = vector_order
        self.query_calls = 0
        self.scored_ids: list[str] = []

    def query(self, embedding, limit, knowledge_base_id, query_text=None, filters=None, access=None):
        self.query_calls += 1
        return [
            _chunk(chunk_id, round(0.9 - 0.1 * index, 6))
            for index, chunk_id in enumerate(self.vector_order[:limit])
        ]

    def load_current_chunks(self, knowledge_base_id, access=None):
        return [_chunk(chunk_id, 0.0) for chunk_id in CHUNKS]

    def score_by_ids(self, chunk_ids, embedding, knowledge_base_id):
        self.scored_ids.extend(chunk_ids)
        return {chunk_id: 0.42 for chunk_id in chunk_ids}


class _FakeEmbedder:
    model_name = "test/embedding"

    def encode(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def _service(vector_order: list[str], mode: str, with_lexical: bool = True) -> RAGService:
    store = _FakeStore(vector_order)
    lexical = None
    if with_lexical:
        lexical = LexicalIndexCache(
            lambda knowledge_base_id: list(CHUNKS.items()),
            lambda knowledge_base_id: "static",
        )
    settings = Settings(frontend_origin="http://localhost:5173", retrieval_mode=mode)
    return RAGService(settings, store, _FakeEmbedder(), None, None, lexical)


def test_vector_mode_never_touches_the_lexical_index() -> None:
    service = _service(["c1", "c3"], mode="vector")

    candidates = service.retrieve_candidates("备份目录", [0.1, 0.2, 0.3], 5)

    assert [item.chunk_id for item in candidates] == ["c1", "c3"]
    assert service.store.scored_ids == []


def test_service_without_lexical_index_falls_back_to_vector() -> None:
    """单机 Chroma 运行时没有注入词法索引，即使配置成 hybrid 也必须退化为向量召回。"""

    service = _service(["c1", "c3"], mode="hybrid", with_lexical=False)

    candidates = service.retrieve_candidates("备份目录", [0.1, 0.2, 0.3], 5)

    assert [item.chunk_id for item in candidates] == ["c1", "c3"]


def test_hybrid_promotes_the_chunk_found_by_both_channels() -> None:
    """向量把 c2 排在末位，词法能精确命中 30080，融合后它应当显著上移。"""

    service = _service(["c1", "c3", "c2"], mode="hybrid")

    candidates = service.retrieve_candidates("30080 是什么", [0.1, 0.2, 0.3], 3)

    assert candidates[0].chunk_id == "c2"


def test_hybrid_backfills_vector_score_for_lexical_only_candidates() -> None:
    """词法独有候选若把 retrieval_score 留在 0，会被后续归一化无理由压到最低。"""

    service = _service(["c1"], mode="hybrid")

    candidates = service.retrieve_candidates("FOR UPDATE SKIP LOCKED", [0.1, 0.2, 0.3], 5)

    by_id = {item.chunk_id: item for item in candidates}
    assert "c4" in by_id
    assert by_id["c4"].retrieval_score == 0.42
    assert "c4" in service.store.scored_ids
    # 向量已经返回的候选不重复补算。
    assert "c1" not in service.store.scored_ids


def test_hybrid_respects_retrieve_k() -> None:
    service = _service(["c1", "c2", "c3", "c4"], mode="hybrid")

    candidates = service.retrieve_candidates("备份与任务", [0.1, 0.2, 0.3], 2)

    assert len(candidates) == 2


def test_hybrid_applies_one_metadata_boundary_to_vector_and_lexical() -> None:
    service = _service(["c1", "c2", "c3", "c4"], mode="hybrid")
    filters = QueryMetadataFilter(categories=["运维"], tags=["备份"])

    candidates = service.retrieve_candidates(
        "备份恢复", [0.1, 0.2, 0.3], 5, filters=filters
    )

    assert [item.chunk_id for item in candidates] == ["c1"]
    assert candidates[0].channels == ("vector", "lexical")


def test_explicit_mode_argument_overrides_settings() -> None:
    """评测需要在同一进程内切换模式，因此显式参数必须压过配置。"""

    service = _service(["c1", "c3"], mode="vector")

    candidates = service.retrieve_candidates(
        "FOR UPDATE SKIP LOCKED", [0.1, 0.2, 0.3], 5, retrieval_mode="lexical"
    )

    assert candidates[0].chunk_id == "c4"
    assert service.store.query_calls == 0


def test_lexical_mode_returns_nothing_when_no_token_matches() -> None:
    service = _service(["c1"], mode="vector")

    candidates = service.retrieve_candidates(
        "zzzzz", [0.1, 0.2, 0.3], 5, retrieval_mode="lexical"
    )

    assert candidates == []


def test_hybrid_labels_each_candidate_with_its_retrieval_channels() -> None:
    """页面要区分证据是靠语义还是靠关键词捞回来的，通路标记必须逐条准确。"""

    # 向量只返回 c1；词法能命中 c1（备份）与 c4（SKIP LOCKED）。
    service = _service(["c1"], mode="hybrid")

    candidates = service.retrieve_candidates(
        "备份目录与 FOR UPDATE SKIP LOCKED", [0.1, 0.2, 0.3], 5
    )
    by_id = {item.chunk_id: item for item in candidates}

    assert by_id["c1"].channels == ("vector", "lexical")
    assert by_id["c1"].lexical_score is not None
    assert by_id["c4"].channels == ("lexical",)
    assert by_id["c4"].lexical_score is not None


def test_vector_only_candidate_carries_no_lexical_score() -> None:
    service = _service(["c3"], mode="hybrid")

    candidates = service.retrieve_candidates("FOR UPDATE SKIP LOCKED", [0.1, 0.2, 0.3], 5)
    by_id = {item.chunk_id: item for item in candidates}

    assert by_id["c3"].channels == ("vector",)
    assert by_id["c3"].lexical_score is None


def test_vector_mode_keeps_the_default_channel_label() -> None:
    service = _service(["c1", "c3"], mode="vector")

    candidates = service.retrieve_candidates("备份目录", [0.1, 0.2, 0.3], 5)

    assert all(item.channels == ("vector",) for item in candidates)
    assert all(item.lexical_score is None for item in candidates)


def test_source_defaults_to_unknown_channels_for_legacy_records() -> None:
    """V5 之前保存的历史回答没有通路字段，反序列化后必须是"未知"而不是"向量"。"""

    from backend.app.schemas import Source

    legacy = Source.model_validate(
        {
            "chunk_id": "c1",
            "document_id": "doc",
            "filename": "doc.md",
            "paragraph": 0,
            "chunk_index": 0,
            "char_count": 10,
            "summary": "旧记录",
            "text": "旧记录正文",
            "retrieval_score": 0.8,
            "rerank_score": 0.9,
        }
    )

    assert legacy.retrieval_channels == []
    assert legacy.lexical_score is None


def test_settings_only_exposes_production_retrieval_modes() -> None:
    """纯词法只是评测用的诊断模式，不应作为生产配置暴露。"""

    with pytest.raises(ValidationError, match="retrieval_mode"):
        Settings(frontend_origin="http://localhost:5173", retrieval_mode="lexical")
