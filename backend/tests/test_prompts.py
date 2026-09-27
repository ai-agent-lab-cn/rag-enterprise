import re

from backend.app.prompts import (
    _FRESHNESS_NOTICE,
    FRESHNESS_UNVERIFIED_CONSTRAINT,
    INSUFFICIENT_ANSWER,
    INVALID_OUTPUT_ANSWER,
    PROMPT_VERSION,
    build_prompt,
    parse_answer,
)
from backend.app.store import RetrievedChunk


def test_prompt_has_version_hash_and_evidence_contract() -> None:
    chunk = RetrievedChunk(
        chunk_id="chunk_1",
        text="系统只根据资料回答。",
        metadata={"filename": "guide.md", "paragraph": 0},
        retrieval_score=0.9,
    )

    prompt = build_prompt("系统如何回答？", [chunk])

    assert prompt.version == PROMPT_VERSION == "v5-stream-grounded-governance-2"
    assert len(prompt.sha256) == 64
    # 提示词在 51d95d4 里改成了「未提供的知识」，那次提交只动了 prompts.py 没同步这条断言，
    # 于是它一直红着。断言跟代码走，PROMPT_VERSION 不动。
    assert "禁止补充未提供的知识或猜测" in prompt.text
    assert "[STATUS: SOURCE_CONFLICT]" in prompt.text
    assert "[来源 1: guide.md / 第 1 段]" in prompt.text


def test_answered_output_requires_valid_citation() -> None:
    parsed = parse_answer("[STATUS: ANSWERED]\n结论成立。[来源 1]", 1)

    assert parsed.status == "answered"
    assert parsed.answer == "结论成立。[来源 1]"
    assert parsed.error_code is None


def test_insufficient_evidence_uses_canonical_answer() -> None:
    parsed = parse_answer("[STATUS: INSUFFICIENT_EVIDENCE]\n随意扩写", 2)

    assert parsed.status == "insufficient_evidence"
    assert parsed.answer == INSUFFICIENT_ANSWER


def test_source_conflict_requires_two_real_sources() -> None:
    valid = parse_answer(
        "[STATUS: SOURCE_CONFLICT]\n来源说法不同：[来源 1] 与 [来源 2]。",
        2,
    )
    invalid = parse_answer("[STATUS: SOURCE_CONFLICT]\n仅引用一个来源。[来源 1]", 2)

    assert valid.status == "source_conflict"
    assert invalid.status == "generation_failed"
    assert invalid.error_code == "MODEL_OUTPUT_INVALID"


def test_unknown_or_missing_citation_is_not_displayed_as_answer() -> None:
    for raw_answer in (
        "没有状态行。[来源 1]",
        "[STATUS: ANSWERED]\n没有引用。",
        "[STATUS: ANSWERED]\n引用越界。[来源 3]",
    ):
        parsed = parse_answer(raw_answer, 2)
        assert parsed.status == "generation_failed"
        assert parsed.answer == INVALID_OUTPUT_ANSWER


def test_each_factual_claim_requires_a_citation() -> None:
    parsed = parse_answer(
        "[STATUS: ANSWERED]\n系统支持审计。系统支持权限隔离。[来源 1]",
        1,
    )

    assert parsed.status == "generation_failed"
    assert parsed.error_code == "CLAIM_CITATION_MISSING"


def test_answer_reports_deduplicated_valid_citation_indices() -> None:
    parsed = parse_answer(
        "[STATUS: ANSWERED]\n系统支持审计[来源 2]，并保留审计事件[来源 2]。[来源 1]",
        2,
    )

    assert parsed.status == "answered"
    assert parsed.citation_indices == (1, 2)
    assert parsed.citation_valid is True
    assert parsed.claim_citation_coverage is True


# --------------------------------------------------------------------------------------
# 时效声明的引用豁免（freshness_unverified=True 才生效）
#
# 不做这个豁免，spec 7.2 第 5 行就是概率事件：模型只要把「时效未验证。」写成独立一句，
# 那句 6 个字就会被判成没有来源的事实声明，整条答案降级成 generation_failed。
# 提示词只能引导，判定必须在代码里做死。
# --------------------------------------------------------------------------------------

STALE_NOTICE_AS_ITS_OWN_SENTENCE = (
    "[STATUS: ANSWERED]\n时效未验证。知识库记载的默认索引版本是 v1[来源 1]。"
)
# 从提示词正文里现提「模型被要求写的那句声明」。不在测试里抄一份副本——抄一份只会变成
# 第三个需要同步的地方，而这条用例的全部意义就是发现同步没做。
_NOTICE_INSTRUCTION = re.compile(r"答案必须以“([^”]+)”开头")


def test_freshness_prompt_and_citation_exemption_cannot_drift_apart() -> None:
    """提示词教模型写的那句声明，必须正好是豁免正则认得的那一句。

    `FRESHNESS_UNVERIFIED_CONSTRAINT` 决定模型写什么，`_FRESHNESS_NOTICE` 决定什么能免检，
    两者是一对。改了一边不改另一边，表现是 stale 回答偶发 `generation_failed`——
    线上偶现，而所有既有用例照样全绿，因为它们用的是自己硬编码的那句话。
    """

    instruction = _NOTICE_INSTRUCTION.search(FRESHNESS_UNVERIFIED_CONSTRAINT)
    assert instruction, (
        "提示词不再用「答案必须以“X”开头」的句式规定固定声明，"
        "_FRESHNESS_NOTICE 的前提已经不成立，两边必须一起重新设计"
    )
    phrase = instruction.group(1)

    # 提示词说这句声明"可以单独成一句"，豁免正则就必须认得独立成句与独占一行两种收尾。
    assert _FRESHNESS_NOTICE.match(f"{phrase}。"), f"提示词要求的声明不在豁免范围内：{phrase}"
    assert _FRESHNESS_NOTICE.match(f"{phrase}\n"), f"提示词要求的声明独占一行时不被豁免：{phrase}"

    # 再走一遍完整的 parse_answer：提示词承诺的两种写法都必须真的能通过引用校验。
    for body in (
        f"{phrase}。知识库记载的默认索引版本是 v1[来源 1]。",
        f"{phrase}：知识库记载的默认索引版本是 v1[来源 1]。",
    ):
        parsed = parse_answer(f"[STATUS: ANSWERED]\n{body}", 1, freshness_unverified=True)
        assert parsed.status == "answered", f"提示词允许的写法被引用校验拒了：{body}"


def test_freshness_notice_sentence_needs_no_citation_in_stale_mode() -> None:
    parsed = parse_answer(STALE_NOTICE_AS_ITS_OWN_SENTENCE, 1, freshness_unverified=True)

    assert parsed.status == "answered"
    assert parsed.claim_citation_coverage is True
    assert parsed.citation_indices == (1,)


def test_freshness_exemption_does_not_leak_into_the_normal_path() -> None:
    """同一段文字在非 stale 模式下必须照旧判失败——豁免只属于时效降级那条分支。"""

    parsed = parse_answer(STALE_NOTICE_AS_ITS_OWN_SENTENCE, 1)

    assert parsed.status == "generation_failed"
    assert parsed.error_code == "CLAIM_CITATION_MISSING"


def test_freshness_notice_on_its_own_line_is_also_exempt() -> None:
    parsed = parse_answer(
        "[STATUS: ANSWERED]\n时效未验证\n知识库记载的默认索引版本是 v1[来源 1]。",
        1,
        freshness_unverified=True,
    )

    assert parsed.status == "answered"


def test_freshness_notice_in_the_same_sentence_still_passes() -> None:
    """提示词推荐的写法本来就合规，加了豁免也不能把它弄坏。"""

    raw = "[STATUS: ANSWERED]\n时效未验证：知识库记载的默认索引版本是 v1[来源 1]。"

    assert parse_answer(raw, 1, freshness_unverified=True).status == "answered"
    assert parse_answer(raw, 1).status == "answered"


def test_stale_mode_still_rejects_uncited_facts() -> None:
    """豁免只放行那一句声明，不是"stale 就不查引用"——这是最容易开成的后门。"""

    parsed = parse_answer(
        "[STATUS: ANSWERED]\n时效未验证。知识库记载的默认索引版本是 v1。"
        "索引策略另有说明[来源 1]。",
        1,
        freshness_unverified=True,
    )

    assert parsed.status == "generation_failed"
    assert parsed.error_code == "CLAIM_CITATION_MISSING"


def test_freshness_exemption_only_matches_the_fixed_notice() -> None:
    """开头不是时效声明而是一条无引用事实时，豁免不能认账。"""

    parsed = parse_answer(
        "[STATUS: ANSWERED]\n知识库记载的默认索引版本是 v1。索引策略另有说明[来源 1]。",
        1,
        freshness_unverified=True,
    )

    assert parsed.status == "generation_failed"
    assert parsed.error_code == "CLAIM_CITATION_MISSING"


def test_freshness_exemption_covers_the_notice_with_its_fixed_explanation() -> None:
    """声明后面接固定的"未取得可信 Web 证据"说明仍在豁免内；换成别的说法就不在。"""

    exempt = parse_answer(
        "[STATUS: ANSWERED]\n时效未验证，未取得可信的当前 Web 证据。"
        "知识库记载的默认索引版本是 v1[来源 1]。",
        1,
        freshness_unverified=True,
    )
    # "外部检索被管理员关闭"是一句自由发挥的说明，不在固定模式里，照常要求引用。
    not_exempt = parse_answer(
        "[STATUS: ANSWERED]\n时效未验证，外部检索被管理员关闭。"
        "知识库记载的默认索引版本是 v1[来源 1]。",
        1,
        freshness_unverified=True,
    )

    assert exempt.status == "answered"
    assert not_exempt.status == "generation_failed"
    assert not_exempt.error_code == "CLAIM_CITATION_MISSING"
