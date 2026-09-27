"""Evidence Gate 的判定矩阵。纯内存，不连库、不发网络请求，因此不加 skipif。"""

from typing import Any

import pytest

from backend.app.config import Settings
from backend.app.evidence_gate import (
    RERANKER_THRESHOLDS,
    UnsupportedRerankerScoreSemantics,
    evaluate_final_evidence,
    evaluate_preliminary_evidence,
    is_citation_ready,
)
from backend.app.models import DEMO_LEXICAL_RERANKER
from backend.app.ranking import rank_candidates
from backend.app.store import RetrievedChunk

CROSS_ENCODER = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
DEMO_OVERLAP = "demo/lexical-overlap-v1"


def kb_chunk(
    chunk_id: str = "chunk_1",
    rerank_score: float = 1.5,
    **overrides: Any,
) -> RetrievedChunk:
    """KB 候选。字段形状对齐 PostgresVectorStore.query 拼出的 metadata。

    ``overrides`` 里给 ``None`` 等价于"该字段缺失"——门禁对两者判定相同。
    """

    metadata: dict[str, Any] = {
        "knowledge_base_id": "kb_default",
        "document_id": "doc_1",
        "document_version_id": "dv_1",
        "content_sha256": "a" * 64,
        "filename": "季度报告.pdf",
        "paragraph": 0,
        "chunk_index": 0,
    }
    metadata.update(overrides)
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="知识库正文",
        metadata=metadata,
        retrieval_score=0.82,
        rerank_score=rerank_score,
    )


def web_chunk(
    chunk_id: str = "web_1",
    rerank_score: float = 1.2,
    **overrides: Any,
) -> RetrievedChunk:
    """Web 候选。字段形状对齐 service._web_chunk。"""

    metadata: dict[str, Any] = {
        "knowledge_base_id": "kb_default",
        "document_id": chunk_id,
        "filename": "官方公告页",
        "paragraph": 0,
        "chunk_index": 0,
        "source_url": "https://example.com/notice",
        "content_sha256": "b" * 64,
        "evidence_source_type": "web",
        "retrieved_at": "2026-09-20T02:00:00+00:00",
    }
    metadata.update(overrides)
    return RetrievedChunk(
        chunk_id=chunk_id,
        text="网页正文",
        metadata=metadata,
        retrieval_score=0.0164,
        channels=("web",),
        rerank_score=rerank_score,
        retrieval_methods=[],
    )


def test_single_relevant_candidate_does_not_fail_due_to_minmax() -> None:
    only = kb_chunk(rerank_score=0.0)

    ranked = rank_candidates([only], [3.2], limit=1)

    # rank_candidates 写回的是原始分（ranking.py:139）。若改成写归一化值，单候选会被
    # _minmax 压成 0.0（ranking.py:178-179），demo overlap 的 `> 0.0` 当场把它判成不相关。
    assert ranked[0].rerank_score == pytest.approx(3.2)
    for model in (CROSS_ENCODER, DEMO_OVERLAP):
        result = evaluate_preliminary_evidence(
            ranked,
            reranker_model=model,
            minimum_evidence_count=1,
            requires_freshness=False,
            web_available=True,
        )
        assert result.outcome == "pass"
        assert result.kb_count == 1
        assert result.selected == (only,)
        assert result.reason_codes == ("kb_evidence_sufficient",)
        assert result.sufficient is True


def test_low_relevance_candidate_is_rejected() -> None:
    result = evaluate_preliminary_evidence(
        [kb_chunk(rerank_score=-1.5)],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=False,
        web_available=False,
    )

    assert result.outcome == "reject"
    assert result.kb_count == 0
    assert result.selected == ()
    assert result.sufficient is False
    assert result.reason_codes == ("no_kb_anchor", "relevance_below_threshold", "web_unavailable")


@pytest.mark.parametrize(
    "missing",
    ["document_id", "document_version_id", "content_sha256", "filename", "paragraph", "chunk_index"],
)
def test_kb_source_missing_document_version_is_not_citation_ready(missing: str) -> None:
    incomplete = kb_chunk(**{missing: None})
    assert is_citation_ready(kb_chunk()) is True
    assert is_citation_ready(incomplete) is False

    result = evaluate_final_evidence(
        [incomplete],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=False,
        web_executed=False,
    )

    assert result.outcome == "reject"
    assert result.kb_count == 0
    assert result.reason_codes == ("no_kb_anchor", "citation_incomplete")


@pytest.mark.parametrize("missing", ["source_url", "filename", "retrieved_at", "content_sha256"])
def test_web_source_missing_url_or_hash_is_not_citation_ready(missing: str) -> None:
    anchor = kb_chunk()
    broken = web_chunk(**{missing: None})
    assert is_citation_ready(web_chunk()) is True
    assert is_citation_ready(broken) is False

    result = evaluate_final_evidence(
        [anchor, broken],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=True,
        web_executed=True,
    )

    # 引用字段不全的 Web 结果不进证据集，时效因此仍算未验证。
    assert result.outcome == "stale"
    assert (result.kb_count, result.web_count) == (1, 0)
    assert result.freshness_verified is False
    assert result.selected == (anchor,)
    assert result.reason_codes == (
        "freshness_required",
        "freshness_unverified",
        "web_no_qualified_result",
    )


def test_final_gate_requires_one_qualified_kb_anchor() -> None:
    web_only = evaluate_final_evidence(
        [web_chunk(), web_chunk(chunk_id="web_2")],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=False,
        web_executed=True,
    )

    assert web_only.outcome == "reject"
    assert (web_only.kb_count, web_only.web_count, web_only.evidence_count) == (0, 2, 2)
    # 合格的 Web 证据也不交出去：Web 不能独立支撑答案。
    assert web_only.selected == ()
    assert web_only.reason_codes == ("no_kb_anchor",)

    anchor = kb_chunk()
    supplement = web_chunk()
    with_anchor = evaluate_final_evidence(
        [anchor, supplement],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=False,
        web_executed=True,
    )

    assert with_anchor.outcome == "pass"
    assert with_anchor.selected == (anchor, supplement)
    assert with_anchor.reason_codes == ("kb_evidence_sufficient",)


def test_freshness_without_web_returns_stale_only_with_kb_anchor() -> None:
    anchor = kb_chunk()

    preliminary = evaluate_preliminary_evidence(
        [anchor],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=True,
        web_available=False,
    )
    assert preliminary.outcome == "stale"
    assert preliminary.selected == (anchor,)
    assert preliminary.reason_codes == ("freshness_required", "web_unavailable", "freshness_unverified")

    stale = evaluate_final_evidence(
        [anchor],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=True,
        web_executed=False,
    )
    assert stale.outcome == "stale"
    assert stale.sufficient is True
    assert stale.freshness_verified is False
    assert stale.selected == (anchor,)
    assert stale.reason_codes == ("freshness_required", "freshness_unverified", "web_not_executed")

    without_anchor = evaluate_final_evidence(
        [kb_chunk(rerank_score=-0.2)],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=True,
        web_executed=False,
    )
    assert without_anchor.outcome == "reject"
    assert without_anchor.sufficient is False
    assert without_anchor.reason_codes == (
        "freshness_required",
        "no_kb_anchor",
        "relevance_below_threshold",
    )


def test_freshness_stale_requires_minimum_evidence_count_in_both_gates() -> None:
    """``minimum_evidence_count > 1`` 时两个门禁必须同口径，否则同一份输入结论相反。"""

    anchor = kb_chunk()

    short_preliminary = evaluate_preliminary_evidence(
        [anchor],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=True,
        web_available=False,
    )
    short_final = evaluate_final_evidence(
        [anchor],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=True,
        web_executed=False,
    )

    # 一条合格 KB 证据撑不起 stale：Web 不可用时初步门禁也必须按最低证据数拒答。
    assert short_preliminary.outcome == short_final.outcome == "reject"
    assert short_preliminary.sufficient is False
    assert short_final.sufficient is False
    assert short_preliminary.selected == short_final.selected == ()
    assert short_preliminary.kb_count == short_final.kb_count == 1
    # 两处的数量判据同源，措辞按各自数得到的口径：初步只数了 KB，最终数的是 KB + Web。
    assert short_preliminary.reason_codes == (
        "freshness_required",
        "kb_evidence_below_minimum",
        "web_unavailable",
    )
    assert short_final.reason_codes == ("freshness_required", "evidence_below_minimum")

    enough = [anchor, kb_chunk(chunk_id="chunk_2")]
    stale_preliminary = evaluate_preliminary_evidence(
        enough,
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=True,
        web_available=False,
    )
    stale_final = evaluate_final_evidence(
        enough,
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=True,
        web_executed=False,
    )

    assert stale_preliminary.outcome == stale_final.outcome == "stale"
    assert stale_preliminary.sufficient is True
    assert stale_final.sufficient is True
    assert stale_preliminary.kb_count == stale_final.kb_count == 2
    assert stale_preliminary.freshness_verified is False
    assert stale_final.freshness_verified is False


def test_freshness_with_thin_kb_still_tries_web_and_records_the_shortfall() -> None:
    # 同样是 kb_count=1 < minimum=2，但 Web 可用：这时数量还有救，先去补检。
    anchor = kb_chunk()
    result = evaluate_preliminary_evidence(
        [anchor],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=True,
        web_available=True,
    )

    assert result.outcome == "needs_web"
    assert result.sufficient is False
    assert result.selected == (anchor,)
    assert result.reason_codes == ("freshness_required", "kb_evidence_below_minimum")


def test_unknown_reranker_score_semantics_is_configuration_error() -> None:
    assert issubclass(UnsupportedRerankerScoreSemantics, ValueError)

    # 候选为空也必须报错：读不懂分数语义时不能"没候选就顺利跳过相关性门禁"。
    for candidates in ([], [kb_chunk()]):
        with pytest.raises(UnsupportedRerankerScoreSemantics) as preliminary:
            evaluate_preliminary_evidence(
                candidates,
                reranker_model="test/reranker",
                minimum_evidence_count=1,
                requires_freshness=False,
                web_available=True,
            )
        assert preliminary.value.reranker_model == "test/reranker"

        with pytest.raises(UnsupportedRerankerScoreSemantics) as final:
            evaluate_final_evidence(
                candidates,
                reranker_model="test/reranker",
                minimum_evidence_count=1,
                requires_freshness=False,
                web_executed=False,
            )
        assert final.value.reranker_model == "test/reranker"


def test_score_exactly_zero_differs_by_model() -> None:
    assert RERANKER_THRESHOLDS[CROSS_ENCODER] == (0.0, "gte")
    assert RERANKER_THRESHOLDS[DEMO_OVERLAP] == (0.0, "gt")

    boundary = kb_chunk(rerank_score=0.0)

    # CrossEncoder 的 0.0 是 logit 中位，算相关。
    cross = evaluate_final_evidence(
        [boundary],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=False,
        web_executed=False,
    )
    assert cross.outcome == "pass"
    assert cross.kb_count == 1

    # 词重叠的 0.0 表示问题 token 一个都没命中，算不相关。
    overlap = evaluate_final_evidence(
        [boundary],
        reranker_model=DEMO_OVERLAP,
        minimum_evidence_count=1,
        requires_freshness=False,
        web_executed=False,
    )
    assert overlap.outcome == "reject"
    assert overlap.kb_count == 0
    assert overlap.reason_codes == ("no_kb_anchor", "relevance_below_threshold")


def test_preliminary_rejects_when_web_unavailable() -> None:
    thin = [kb_chunk()]

    blocked = evaluate_preliminary_evidence(
        thin,
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=False,
        web_available=False,
    )
    assert blocked.outcome == "reject"
    assert blocked.kb_count == 1
    assert blocked.selected == ()
    assert blocked.reason_codes == ("kb_evidence_below_minimum", "web_unavailable")

    supplemented = evaluate_preliminary_evidence(
        thin,
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=False,
        web_available=True,
    )
    assert supplemented.outcome == "needs_web"
    assert supplemented.sufficient is False
    # 已合格的 KB 证据要带进融合，不能因为要补 Web 就丢掉。
    assert supplemented.selected == (thin[0],)
    assert supplemented.reason_codes == ("kb_evidence_below_minimum", "web_supplement_available")


def test_freshness_without_kb_anchor_still_tries_web_and_records_no_kb_anchor() -> None:
    unusable = kb_chunk(document_version_id=None)

    preliminary = evaluate_preliminary_evidence(
        [unusable],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=True,
        web_available=True,
    )
    # 时效问题照打一次 Web，但拒答的原因此刻就已确定，必须记下来——否则用户在技术抽屉
    # 看到 Web「已检索」却被拒答，说不出为什么。
    assert preliminary.outcome == "needs_web"
    assert preliminary.reason_codes == ("freshness_required", "no_kb_anchor", "citation_incomplete")

    final = evaluate_final_evidence(
        [unusable, web_chunk()],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=1,
        requires_freshness=True,
        web_executed=True,
    )
    assert final.outcome == "reject"
    assert final.web_count == 1
    assert final.selected == ()
    assert final.reason_codes == ("freshness_required", "no_kb_anchor", "citation_incomplete")


def test_final_gate_counts_web_evidence_toward_minimum() -> None:
    anchor = kb_chunk()

    fused = evaluate_final_evidence(
        [anchor, web_chunk()],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=False,
        web_executed=True,
    )
    assert fused.outcome == "pass"
    assert (fused.kb_count, fused.web_count, fused.evidence_count) == (1, 1, 2)
    assert fused.reason_codes == ("kb_evidence_sufficient",)

    short = evaluate_final_evidence(
        [anchor],
        reranker_model=CROSS_ENCODER,
        minimum_evidence_count=2,
        requires_freshness=False,
        web_executed=True,
    )
    assert short.outcome == "reject"
    assert short.selected == ()
    assert short.reason_codes == ("evidence_below_minimum",)


def test_minimum_evidence_count_must_be_positive() -> None:
    for gate, extra in (
        (evaluate_preliminary_evidence, {"web_available": True}),
        (evaluate_final_evidence, {"web_executed": False}),
    ):
        with pytest.raises(ValueError, match="minimum_evidence_count"):
            gate(
                [kb_chunk()],
                reranker_model=CROSS_ENCODER,
                minimum_evidence_count=0,
                requires_freshness=False,
                **extra,
            )


def test_threshold_table_matches_configured_reranker_names() -> None:
    """阈值表的键必须和实际会出现的两个模型名一致，改了模型名就要改阈值表。"""

    configured = Settings.model_fields["reranker_model"].default

    assert configured == CROSS_ENCODER
    assert DEMO_LEXICAL_RERANKER == DEMO_OVERLAP
    assert set(RERANKER_THRESHOLDS) == {configured, DEMO_LEXICAL_RERANKER}
