"""确定性 Evidence Gate：判断证据是否足够、是否需要补 Web、最终应回答还是拒答。

两个门禁都是纯函数：不调用 LLM、不访问数据库、不读配置，同样的输入必然得到同样的结论。

- ``evaluate_preliminary_evidence``：KB 精排后执行，决定要不要打一次 Web；
- ``evaluate_final_evidence``：证据融合与统一精排后执行，决定回答 / 时效降级 / 拒答。

相关性判定读 ``RetrievedChunk.rerank_score``——``ranking.rank_candidates`` 写回的是原始
Reranker 分数（``ranking.py:139``），不是 Min-Max 归一化值。不能用归一化分数做阈值判定：
``ranking._minmax`` 在单候选或同分候选时把全部分数压成 0（``ranking.py:178-179``），
唯一一条高相关证据会被判成不相关。

ACL、有效期与 active 索引版本不在这里复核：检索链路已经在 ``service._filter_candidates``
（``service.py:52-61``）统一用 ``retrieval_access.can_retrieve_metadata`` 过滤过，
且本模块的签名不携带 ``RetrievalAccessContext``。门禁只负责相关性、引用完整性、证据数量
和时效四类判据。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .store import RetrievedChunk

GateOutcome = Literal["pass", "needs_web", "stale", "reject"]
"""门禁结论。取值来源：``needs_web`` 只可能来自初步门禁，``pass`` / ``stale`` / ``reject``
两个门禁都会返回——初步门禁的 ``stale`` 表示时效问题遇上 Web 不可用但 KB 证据已达标，
``pass`` 表示普通问题 KB 已达标、不必联网。两者都不是终局：最终裁决权始终在 Final Gate。
Final Gate 的 ``pass`` / ``stale`` / ``reject`` 依次对应
``answered`` / ``answered_stale`` / ``insufficient_evidence``。"""

ReasonCode = Literal[
    "kb_evidence_sufficient",
    "kb_evidence_below_minimum",
    "evidence_below_minimum",
    "no_kb_anchor",
    "relevance_below_threshold",
    "citation_incomplete",
    "web_supplement_available",
    "web_unavailable",
    "web_not_executed",
    "web_no_qualified_result",
    "freshness_required",
    "freshness_verified",
    "freshness_unverified",
]

ScoreComparison = Literal["gte", "gt"]

# 阈值表必须带比较符：两个模型的阈值都是 0.0，但 CrossEncoder 用 `>= 0.0`、
# demo overlap 用 `> 0.0`——分数恰为 0.0 时两者结论相反，裸 float 无法区分。
# 模型名不从 models.py 导入：那个模块顶层 import sentence_transformers，纯逻辑的门禁
# 不该因此加载 torch。一致性由 test_evidence_gate 的
# test_threshold_table_matches_configured_reranker_names 守着。
RERANKER_THRESHOLDS: dict[str, tuple[float, ScoreComparison]] = {
    # config.py:14 的默认 reranker_model。CrossEncoder 输出未归一化 logit，可为负。
    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1": (0.0, "gte"),
    # models.py:17 DEMO_LEXICAL_RERANKER。词重叠比值，0 表示问题 token 一个都没命中。
    "demo/lexical-overlap-v1": (0.0, "gt"),
}

# KB 引用必须能定位到"哪个文档的哪个版本的哪一段"，缺一项就无法核验。
_KB_CITATION_FIELDS = ("document_id", "document_version_id", "content_sha256", "filename")
# 段号与 Chunk 序号合法值包含 0，只能判"存在"，不能判真值。
_KB_LOCATOR_FIELDS = ("paragraph", "chunk_index")
# Web 引用字段对应 spec 第 190 行：URL、标题、抓取时间、内容哈希。
_WEB_CITATION_FIELDS = ("source_url", "filename", "retrieved_at", "content_sha256")


class UnsupportedRerankerScoreSemantics(ValueError):
    """Reranker 的分数语义未登记在 ``RERANKER_THRESHOLDS``。

    门禁不能因为读不懂分数就跳过相关性检查，所以这里抛错而不是放行。
    服务层把它映射成 ``RAG_PROFILE_INCOMPATIBLE``。
    """

    def __init__(self, reranker_model: str):
        self.reranker_model = reranker_model
        super().__init__(f"未登记 Reranker 分数语义：{reranker_model}")


@dataclass(frozen=True)
class EvidenceGateResult:
    outcome: GateOutcome
    # 通过门禁、可以交给生成模型的证据。``reject`` 时恒为空元组：Web 不能独立支撑答案，
    # 留着它只会诱导调用方把不该展示的候选当证据渲染。
    selected: tuple[RetrievedChunk, ...]
    # 合格证据数量，与 outcome 无关，``reject`` 时也如实上报，供技术抽屉解释原因。
    kb_count: int
    web_count: int
    freshness_verified: bool
    reason_codes: tuple[ReasonCode, ...]

    @property
    def sufficient(self) -> bool:
        return self.outcome in {"pass", "stale"}

    @property
    def evidence_count(self) -> int:
        return self.kb_count + self.web_count


def evaluate_preliminary_evidence(
    candidates: Sequence[RetrievedChunk],
    *,
    reranker_model: str,
    minimum_evidence_count: int,
    requires_freshness: bool,
    web_available: bool,
) -> EvidenceGateResult:
    """KB 精排后的初步门禁：直接进入 Final Gate、补一次 Web，还是当场拒答。"""

    _validate_minimum(minimum_evidence_count)
    selection = _select(candidates, reranker_model)
    reasons: list[ReasonCode] = []
    kb_anchor = selection.kb_count >= 1
    # spec 5.1 把 evidence_count 列为初步门禁的检查项，不只是 Final 的。
    kb_sufficient = kb_anchor and selection.kb_count >= minimum_evidence_count

    if requires_freshness:
        # 时效问题无论 KB 是否通过都尝试一次 Web（spec 第 172 行）。
        reasons.append("freshness_required")
        if not kb_anchor:
            # 但没有 KB anchor 时 Final Gate 必拒（spec 第 217 行）。原因落在这里，
            # 技术抽屉才说得出"Web 已检索却仍然拒答"是为什么。
            reasons.append("no_kb_anchor")
            reasons.extend(_diagnostics(selection))
        elif not kb_sufficient:
            reasons.append("kb_evidence_below_minimum")
            reasons.extend(_diagnostics(selection))
        if web_available:
            return _build(selection, "needs_web", requires_freshness, reasons)
        # Web 不可用意味着 KB 现有的量就是最终的量，数量判据必须与 Final Gate 同口径：
        # 少判一次 minimum_evidence_count，同一份输入会出现初步 stale、最终 reject 的分歧。
        reasons.append("web_unavailable")
        if not kb_sufficient:
            return _build(selection, "reject", requires_freshness, reasons)
        reasons.append("freshness_unverified")
        return _build(selection, "stale", requires_freshness, reasons)

    if kb_sufficient:
        reasons.append("kb_evidence_sufficient")
        return _build(selection, "pass", requires_freshness, reasons)

    reasons.append("no_kb_anchor" if not kb_anchor else "kb_evidence_below_minimum")
    reasons.extend(_diagnostics(selection))
    if web_available:
        reasons.append("web_supplement_available")
        return _build(selection, "needs_web", requires_freshness, reasons)
    reasons.append("web_unavailable")
    return _build(selection, "reject", requires_freshness, reasons)


def evaluate_final_evidence(
    candidates: Sequence[RetrievedChunk],
    *,
    reranker_model: str,
    minimum_evidence_count: int,
    requires_freshness: bool,
    web_executed: bool,
) -> EvidenceGateResult:
    """统一精排后的最终门禁：回答、时效降级还是拒答。"""

    _validate_minimum(minimum_evidence_count)
    selection = _select(candidates, reranker_model)
    reasons: list[ReasonCode] = []
    if requires_freshness:
        reasons.append("freshness_required")

    if selection.kb_count < 1:
        # Web 只能补充 KB，不能独立支撑答案；时效问题同样适用。
        reasons.append("no_kb_anchor")
        reasons.extend(_diagnostics(selection))
        return _build(selection, "reject", requires_freshness, reasons)

    if selection.kb_count + selection.web_count < minimum_evidence_count:
        reasons.append("evidence_below_minimum")
        reasons.extend(_diagnostics(selection))
        return _build(selection, "reject", requires_freshness, reasons)

    if not requires_freshness:
        reasons.append("kb_evidence_sufficient")
        return _build(selection, "pass", requires_freshness, reasons)

    if selection.web_count >= 1:
        reasons.append("freshness_verified")
        return _build(selection, "pass", requires_freshness, reasons)

    # 有 KB 历史证据但拿不到合格 Web 证据：降级而不是拒答，并说明 Web 到底怎么了。
    reasons.append("freshness_unverified")
    reasons.append("web_no_qualified_result" if web_executed else "web_not_executed")
    return _build(selection, "stale", requires_freshness, reasons)


def is_web_evidence(candidate: RetrievedChunk) -> bool:
    """判别 Web 候选。判据与 ``service._source`` 一致，只认 metadata 里的显式标记。"""

    return candidate.metadata.get("evidence_source_type") == "web"


def is_citation_ready(candidate: RetrievedChunk) -> bool:
    """引用字段是否齐全到可以在答案里标注 ``[来源 N]`` 并回溯原文。"""

    metadata = candidate.metadata
    if is_web_evidence(candidate):
        return all(_has_text(metadata, name) for name in _WEB_CITATION_FIELDS)
    return all(_has_text(metadata, name) for name in _KB_CITATION_FIELDS) and all(
        metadata.get(name) is not None for name in _KB_LOCATOR_FIELDS
    )


@dataclass(frozen=True)
class _Selection:
    qualified: tuple[RetrievedChunk, ...]
    kb_count: int
    web_count: int
    relevance_rejected: int
    citation_rejected: int


def _select(candidates: Sequence[RetrievedChunk], reranker_model: str) -> _Selection:
    # 阈值先取：候选为空也要对未登记的 Reranker 报错，不能"没候选就顺利跳过门禁"。
    threshold, comparison = _score_semantics(reranker_model)
    qualified: list[RetrievedChunk] = []
    kb_count = 0
    web_count = 0
    relevance_rejected = 0
    citation_rejected = 0
    for candidate in candidates:
        if not _compare(candidate.rerank_score, threshold, comparison):
            relevance_rejected += 1
            continue
        if not is_citation_ready(candidate):
            citation_rejected += 1
            continue
        qualified.append(candidate)
        if is_web_evidence(candidate):
            web_count += 1
        else:
            kb_count += 1
    return _Selection(
        qualified=tuple(qualified),
        kb_count=kb_count,
        web_count=web_count,
        relevance_rejected=relevance_rejected,
        citation_rejected=citation_rejected,
    )


def _build(
    selection: _Selection,
    outcome: GateOutcome,
    requires_freshness: bool,
    reasons: list[ReasonCode],
) -> EvidenceGateResult:
    return EvidenceGateResult(
        outcome=outcome,
        selected=() if outcome == "reject" else selection.qualified,
        kb_count=selection.kb_count,
        web_count=selection.web_count,
        freshness_verified=requires_freshness and selection.web_count >= 1,
        reason_codes=tuple(reasons),
    )


def _diagnostics(selection: _Selection) -> list[ReasonCode]:
    """证据不够时补上"候选是怎么被淘汰的"，顺序固定，便于断言与展示。"""

    reasons: list[ReasonCode] = []
    if selection.relevance_rejected:
        reasons.append("relevance_below_threshold")
    if selection.citation_rejected:
        reasons.append("citation_incomplete")
    return reasons


def _score_semantics(reranker_model: str) -> tuple[float, ScoreComparison]:
    semantics = RERANKER_THRESHOLDS.get(reranker_model)
    if semantics is None:
        raise UnsupportedRerankerScoreSemantics(reranker_model)
    return semantics


def _compare(score: float, threshold: float, comparison: ScoreComparison) -> bool:
    value = float(score)
    return value >= threshold if comparison == "gte" else value > threshold


def _has_text(metadata: dict[str, Any], field: str) -> bool:
    value = metadata.get(field)
    return value is not None and str(value).strip() != ""


def _validate_minimum(minimum_evidence_count: int) -> None:
    if minimum_evidence_count < 1:
        raise ValueError("minimum_evidence_count 必须大于等于 1")
