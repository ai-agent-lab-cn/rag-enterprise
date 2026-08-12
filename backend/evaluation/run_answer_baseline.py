"""使用真实 Gemini 生成与独立裁判运行固定 V3 回答质量基线。"""

import argparse
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from google import genai
from google.genai import types

from backend.app.config import get_settings
from backend.app.prompts import (
    GENERATION_FAILED_ANSWER,
    RETRIEVAL_ONLY_ANSWER,
    build_prompt,
    parse_answer,
)
from backend.app.store import RetrievedChunk

from .answer_quality import (
    AnswerEvaluationCase,
    AnswerEvaluationRun,
    AnswerObservation,
    EvaluatedSource,
    SemanticJudgement,
    evaluate_answers,
    load_answer_dataset,
    write_report,
)
from .dataset import load_dataset


def run_baseline(
    *,
    dataset_path: Path,
    corpus_path: Path,
    commit: str,
    judge_model: str,
    checkpoint_path: Path,
    request_interval_seconds: float = 7.0,
) -> tuple[AnswerEvaluationRun, object]:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("正式回答评测需要通过本地环境提供 GEMINI_API_KEY")
    if judge_model == settings.generation_model:
        raise ValueError("生成模型不能作为唯一裁判")

    dataset = load_answer_dataset(dataset_path)
    sources = _source_catalog(dataset, corpus_path)
    client = genai.Client(api_key=settings.gemini_api_key)
    completed, completed_hashes = _load_checkpoint(checkpoint_path)
    observations: list[AnswerObservation] = []
    prompt_hashes: list[str] = []
    last_request_at = [0.0]
    for case in dataset.cases:
        if case.case_id in completed:
            observations.append(completed[case.case_id])
            prompt_hashes.append(completed_hashes[case.case_id])
            continue
        case_sources = [sources[source_id] for source_id in case.acceptable_source_ids]
        observation, prompt_hash = _observe_case(
            client,
            case,
            case_sources,
            settings.generation_model,
            last_request_at,
            request_interval_seconds,
        )
        prompt_hashes.append(prompt_hash)
        if case.scenario in {"answerable", "source_conflict"}:
            observation.judgement = _judge_answer(
                client,
                case,
                observation,
                judge_model,
                last_request_at,
                request_interval_seconds,
            )
        observations.append(observation)
        completed[case.case_id] = observation
        completed_hashes[case.case_id] = prompt_hash
        _save_checkpoint(checkpoint_path, completed, completed_hashes)

    run = AnswerEvaluationRun(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.version,
        commit=commit,
        run_at=datetime.now(UTC),
        prompt_version="v3-grounded-answer-1",
        prompt_hash=_aggregate_hash(prompt_hashes),
        models={
            "generation": settings.generation_model,
            "judge": judge_model,
        },
        parameters={
            "temperature": 0,
            "evaluation_scope": "fixed_evidence_generation",
            "judge_rubric": "v1",
        },
        observations=observations,
    )
    return run, evaluate_answers(dataset, run, "formal")


def _source_catalog(dataset, corpus_path: Path) -> dict[str, EvaluatedSource]:
    corpus = load_dataset(corpus_path)
    catalog = {
        item.chunk_id: EvaluatedSource(
            chunk_id=item.chunk_id,
            filename=item.filename,
            paragraph=index,
            text=item.text,
        )
        for index, item in enumerate(corpus.chunks)
    }
    catalog.update(
        {
            item.chunk_id: EvaluatedSource(
                chunk_id=item.chunk_id,
                filename=item.filename,
                paragraph=index,
                text=item.text,
            )
            for index, item in enumerate(dataset.supplemental_evidence)
        }
    )
    return catalog


def _observe_case(
    client,
    case: AnswerEvaluationCase,
    sources: list[EvaluatedSource],
    generation_model: str,
    last_request_at: list[float],
    request_interval_seconds: float,
) -> tuple[AnswerObservation, str]:
    if case.scenario == "retrieval_empty":
        return _failure_observation(case, "知识库为空，请先上传文档。", [], "NO_DOCUMENTS"), _zero_hash()
    if case.scenario == "generation_unavailable":
        return _failure_observation(case, RETRIEVAL_ONLY_ANSWER, sources), _zero_hash()
    if case.scenario == "generation_timeout":
        return _failure_observation(case, GENERATION_FAILED_ANSWER, sources, "MODEL_TIMEOUT"), _zero_hash()

    prompt_sources = sources
    if case.scenario == "insufficient_evidence":
        # 固定加入一条与问题无关的真实资料，验证“召回有结果但证据不足”的拒答行为。
        prompt_sources = [
            EvaluatedSource(
                chunk_id="irrelevant:chunk:00000",
                filename="无关资料.md",
                paragraph=0,
                text="本资料只说明系统使用中文界面。",
            )
        ]
    prompt = build_prompt(case.question, [_retrieved(source) for source in prompt_sources])
    _wait_for_request_slot(last_request_at, request_interval_seconds)
    response = client.models.generate_content(
        model=generation_model,
        contents=prompt.text,
        config=types.GenerateContentConfig(temperature=0),
    )
    parsed = parse_answer(response.text or "", len(prompt_sources))
    return (
        AnswerObservation(
            case_id=case.case_id,
            answer_status=parsed.status,
            answer=parsed.answer,
            sources=prompt_sources,
            error_code=parsed.error_code,
        ),
        prompt.sha256,
    )


def _failure_observation(
    case: AnswerEvaluationCase,
    answer: str,
    sources: list[EvaluatedSource],
    error_code: str | None = None,
) -> AnswerObservation:
    return AnswerObservation(
        case_id=case.case_id,
        answer_status=case.expected_status,
        answer=answer,
        sources=sources,
        error_code=error_code,
    )


def _judge_answer(
    client,
    case,
    observation,
    judge_model: str,
    last_request_at: list[float],
    request_interval_seconds: float,
) -> SemanticJudgement:
    source_text = "\n".join(
        f"[来源 {index}] {source.text}" for index, source in enumerate(observation.sources, 1)
    )
    prompt = f"""你是独立的 RAG 回答质量裁判。仅依据给定参考标注和来源评分，不使用外部知识。

问题：{case.question}
参考要点：{json.dumps(case.reference_points, ensure_ascii=False)}
禁止断言：{json.dumps(case.forbidden_claims, ensure_ascii=False)}
回答状态：{observation.answer_status}
回答：{observation.answer}
来源：
{source_text}

将回答拆分为最小事实声明。correctness 表示与参考要点一致程度，completeness 表示参考要点覆盖程度。
每个声明记录引用编号、是否被来源支持、是否与来源矛盾、归因是否正确，并给出简短理由和原文证据。
不得因为语言流畅而提高分数；未引用或来源不支持时必须如实标记。
"""
    _wait_for_request_slot(last_request_at, request_interval_seconds)
    response = client.models.generate_content(
        model=judge_model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
            response_schema=SemanticJudgement,
        ),
    )
    return SemanticJudgement.model_validate_json(response.text or "{}")


def _retrieved(source: EvaluatedSource) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=source.chunk_id,
        text=source.text,
        metadata={"filename": source.filename, "paragraph": source.paragraph},
        retrieval_score=1.0,
        rerank_score=1.0,
    )


def _aggregate_hash(prompt_hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(prompt_hashes).encode()).hexdigest()


def _zero_hash() -> str:
    return "0" * 64


def _wait_for_request_slot(last_request_at: list[float], interval_seconds: float) -> None:
    remaining = interval_seconds - (time.monotonic() - last_request_at[0])
    if remaining > 0:
        time.sleep(remaining)
    last_request_at[0] = time.monotonic()


def _load_checkpoint(
    path: Path,
) -> tuple[dict[str, AnswerObservation], dict[str, str]]:
    if not path.exists():
        return {}, {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    observations = {
        item["case_id"]: AnswerObservation.model_validate(item)
        for item in payload.get("observations", [])
    }
    hashes = payload.get("prompt_hashes", {})
    if set(observations) != set(hashes):
        raise ValueError("检查点中的回答与 Prompt 哈希不一致")
    return observations, hashes


def _save_checkpoint(
    path: Path,
    observations: dict[str, AnswerObservation],
    prompt_hashes: dict[str, str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "observations": [item.model_dump(mode="json") for item in observations.values()],
                "prompt_hashes": prompt_hashes,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--judge-model", default="gemini-3.1-flash-lite")
    parser.add_argument("--run-output", type=Path, required=True)
    parser.add_argument("--report-output", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--request-interval-seconds", type=float, default=7.0)
    args = parser.parse_args()
    run, report = run_baseline(
        dataset_path=args.dataset,
        corpus_path=args.corpus,
        commit=args.commit,
        judge_model=args.judge_model,
        checkpoint_path=args.checkpoint or args.run_output.with_suffix(".checkpoint.json"),
        request_interval_seconds=args.request_interval_seconds,
    )
    args.run_output.parent.mkdir(parents=True, exist_ok=True)
    args.run_output.write_text(
        json.dumps(run.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_report(args.report_output, report)
    print(args.run_output)
    print(args.report_output)
    print(f"passed={str(report.passed).lower()}")


if __name__ == "__main__":
    main()
