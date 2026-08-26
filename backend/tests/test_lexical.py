import pytest

from backend.app.lexical import BM25Index, LexicalIndexCache, tokenize
from backend.app.ranking import reciprocal_rank_fusion


def test_tokenize_keeps_identifiers_whole_and_splits_chinese_into_bigrams() -> None:
    """标识符不被切碎正是引入词法检索的目的，中文则用 bigram 覆盖。"""

    tokens = tokenize("NodePort 30080 仅供本机访问")

    assert "nodeport" in tokens
    assert "30080" in tokens
    assert "仅供" in tokens and "供本" in tokens and "本机" in tokens
    # 单个中文字不单独成词，否则高频虚词会拿到过高权重。
    assert "仅" not in tokens


def test_tokenize_preserves_dotted_and_slashed_identifiers() -> None:
    tokens = tokenize("执行 scripts/database_migrate.py check --required-version 3")

    assert "scripts/database_migrate.py" in tokens
    assert "check" in tokens
    assert "3" in tokens


def test_tokenize_falls_back_to_single_character_for_short_runs() -> None:
    assert tokenize("升 级") == ["升", "级"]


def test_bm25_ranks_exact_identifier_match_first() -> None:
    """向量召回最容易漏掉的编号类查询，BM25 必须能稳定命中。"""

    index = BM25Index(
        [
            ("c1", "备份写入 rag-backups PVC，并包含数据库自定义格式 dump。"),
            ("c2", "NodePort 30080 仅供本机访问；配置使用 APP_ENVIRONMENT=test。"),
            ("c3", "恢复目标必须是新建空数据库和空上传目录。"),
        ]
    )

    hits = index.search("30080 是做什么的", limit=3)

    assert hits[0].chunk_id == "c2"
    assert hits[0].score > 0


def test_bm25_scores_are_deterministic_and_ordered() -> None:
    documents = [(f"c{index}", f"文档 {index} 讲述索引重建与切分配置。") for index in range(1, 6)]
    index = BM25Index(documents)

    first = index.search("索引重建", limit=3)
    second = index.search("索引重建", limit=3)

    assert first == second
    assert [hit.score for hit in first] == sorted((hit.score for hit in first), reverse=True)


def test_bm25_returns_nothing_when_no_token_matches() -> None:
    index = BM25Index([("c1", "备份与恢复演练")])

    assert index.search("zzzzz", limit=5) == []


def test_bm25_handles_empty_index() -> None:
    assert BM25Index([]).search("任何问题", limit=5) == []


def test_bm25_rejects_invalid_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        BM25Index([("c1", "文本")]).search("文本", limit=0)


def test_rrf_promotes_documents_found_by_both_channels() -> None:
    """两路都召回的候选应当胜过任意单路的头名，这是混合检索的核心收益。"""

    vector = ["a", "b", "c"]
    lexical = ["c", "d", "a"]

    fused = reciprocal_rank_fusion([vector, lexical], limit=4)

    assert [chunk_id for chunk_id, _ in fused][:2] == ["a", "c"]


def test_rrf_ignores_score_magnitude_and_only_uses_rank() -> None:
    """RRF 不接受分数，因此 BM25 与余弦的量纲差异不会影响融合结果。"""

    fused = reciprocal_rank_fusion([["a", "b"], ["b", "a"]], limit=2)

    assert {chunk_id for chunk_id, _ in fused} == {"a", "b"}
    assert fused[0][1] == pytest.approx(fused[1][1])


def test_rrf_is_deterministic_on_ties() -> None:
    first = reciprocal_rank_fusion([["x", "y"], ["y", "x"]], limit=2)
    second = reciprocal_rank_fusion([["x", "y"], ["y", "x"]], limit=2)

    assert first == second == sorted(first, key=lambda item: (-item[1], item[0]))


def test_rrf_validates_arguments() -> None:
    with pytest.raises(ValueError, match="limit"):
        reciprocal_rank_fusion([["a"]], limit=0)
    with pytest.raises(ValueError, match="k"):
        reciprocal_rank_fusion([["a"]], limit=1, k=0)


def test_rrf_accepts_a_single_channel() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"]], limit=2)

    assert [chunk_id for chunk_id, _ in fused] == ["a", "b"]


class _FakeChunkSource:
    """模拟存储层：``load`` 返回当前分块，``fingerprint`` 复现真实实现的语义。"""

    def __init__(self, data: dict[str, list[tuple[str, str]]]):
        self.data = data
        self.loads: list[str] = []

    def load(self, knowledge_base_id: str) -> list[tuple[str, str]]:
        self.loads.append(knowledge_base_id)
        return self.data.get(knowledge_base_id, [])

    def fingerprint(self, knowledge_base_id: str) -> str:
        return repr(self.data.get(knowledge_base_id, []))


def _cache(source: _FakeChunkSource) -> LexicalIndexCache:
    return LexicalIndexCache(source.load, source.fingerprint)


def test_cache_builds_once_per_knowledge_base() -> None:
    source = _FakeChunkSource({"kb_a": [("c1", "备份与恢复")], "kb_b": [("c2", "索引重建")]})
    cache = _cache(source)

    first = cache.get("kb_a")
    second = cache.get("kb_a")
    cache.get("kb_b")

    assert first is second
    assert source.loads == ["kb_a", "kb_b"]


def test_cache_isolates_knowledge_bases() -> None:
    """词法召回必须和向量库一样守住知识库边界，不能跨库命中。"""

    source = _FakeChunkSource(
        {
            "kb_a": [("c1", "NodePort 30080 仅供本机访问")],
            "kb_b": [("c2", "备份写入 rag-backups PVC")],
        }
    )
    cache = _cache(source)

    assert [hit.chunk_id for hit in cache.get("kb_a").search("30080", limit=5)] == ["c1"]
    assert cache.get("kb_b").search("30080", limit=5) == []


def test_fingerprint_change_rebuilds_without_explicit_invalidation() -> None:
    """独立 Worker 进程写入的新分块无法通知 API 进程，只能靠指纹自动感知。"""

    source = _FakeChunkSource({"kb_a": [("c1", "旧的分块内容")]})
    cache = _cache(source)
    cache.get("kb_a")

    source.data["kb_a"] = [("c2", "重建之后的新分块内容")]

    hits = cache.get("kb_a").search("新分块", limit=5)
    assert [hit.chunk_id for hit in hits] == ["c2"]
    assert source.loads == ["kb_a", "kb_a"]


def test_unchanged_fingerprint_does_not_rebuild() -> None:
    source = _FakeChunkSource({"kb_a": [("c1", "内容未变")]})
    cache = _cache(source)

    for _ in range(5):
        cache.get("kb_a")

    assert source.loads == ["kb_a"]


def test_invalidate_forces_rebuild_even_when_fingerprint_is_unchanged() -> None:
    source = _FakeChunkSource({"kb_a": [("c1", "内容未变")]})
    cache = _cache(source)
    cache.get("kb_a")

    cache.invalidate("kb_a")
    cache.get("kb_a")

    assert source.loads == ["kb_a", "kb_a"]


def test_invalidate_is_safe_for_unknown_knowledge_base() -> None:
    cache = _cache(_FakeChunkSource({}))

    cache.invalidate("kb_never_loaded")

    assert cache.cached_knowledge_base_ids() == set()


def test_clear_drops_every_cached_index() -> None:
    source = _FakeChunkSource({"kb_a": [("c1", "一")], "kb_b": [("c2", "二")]})
    cache = _cache(source)
    cache.get("kb_a")
    cache.get("kb_b")

    assert cache.cached_knowledge_base_ids() == {"kb_a", "kb_b"}

    cache.clear()

    assert cache.cached_knowledge_base_ids() == set()
