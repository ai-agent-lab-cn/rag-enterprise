from backend.app.prompts import (
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

    assert prompt.version == PROMPT_VERSION == "v3-grounded-answer-1"
    assert len(prompt.sha256) == 64
    assert "禁止补充外部知识或猜测" in prompt.text
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
