from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.app.modular_rag import QueryIntentRouter


@dataclass(frozen=True)
class IntentEvaluationResult:
    sample_count: int
    macro_f1: float
    per_intent_f1: dict[str, float]
    control_precision: float
    failures: list[dict[str, str]]

    @property
    def passed(self) -> bool:
        return self.macro_f1 >= 0.90 and self.control_precision >= 0.90


# 加载意图路由评测数据集
def load_intent_dataset(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset_id") != "intent_routing_v1" or not isinstance(payload.get("samples"), list):
        raise ValueError("intent routing dataset format is invalid")
    return [dict(item) for item in payload["samples"]]


# 评测意图路由器
def evaluate_intent_router(
    router: QueryIntentRouter,
    samples: list[dict[str, Any]],
) -> IntentEvaluationResult:
    labels = ("fact_lookup", "summarize", "compare", "procedure")
    truth: list[str] = []
    predicted: list[str] = []
    failures: list[dict[str, str]] = []
    control_true = control_predicted = control_correct = 0
    for sample in samples:
        expected = str(sample["expected"])
        decision = router.route(str(sample["question"]), list(sample.get("history") or []))
        actual = decision.intent or decision.control_outcome
        if expected in labels:
            truth.append(expected)
            predicted.append(actual)
        else:
            control_true += 1
        if actual in {"clarify", "out_of_scope"}:
            control_predicted += 1
            if actual == expected:
                control_correct += 1
        if actual != expected:
            failures.append({"sample_id": str(sample["sample_id"]), "expected": expected, "actual": actual})
    per_intent = {label: _f1(label, truth, predicted) for label in labels}
    macro_f1 = sum(per_intent.values()) / len(per_intent)
    control_precision = (
        control_correct / control_predicted if control_predicted else (1.0 if not control_true else 0.0)
    )
    return IntentEvaluationResult(
        len(samples),
        round(macro_f1, 6),
        {key: round(value, 6) for key, value in per_intent.items()},
        round(control_precision, 6),
        failures,
    )


# 计算 F1 分数
def _f1(label: str, truth: list[str], predicted: list[str]) -> float:
    true_positive = sum(a == label and b == label for a, b in zip(truth, predicted, strict=True))
    false_positive = sum(a != label and b == label for a, b in zip(truth, predicted, strict=True))
    false_negative = sum(a == label and b != label for a, b in zip(truth, predicted, strict=True))
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0
    return 2 * precision * recall / (precision + recall) if precision + recall else 0
