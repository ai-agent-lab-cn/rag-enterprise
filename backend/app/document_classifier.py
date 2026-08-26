from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ClassificationResult:
    category_id: str | None
    confidence: float | None
    reason: str
    status: str


class ClassificationGenerator(Protocol):
    model_name: str

    @property
    def ready(self) -> bool: ...

    def generate(self, prompt: str) -> tuple[str, dict[str, object]]: ...


class DocumentClassifier:
    threshold = 0.80

    def __init__(self, generator: ClassificationGenerator):
        self.generator = generator

    def classify(
        self,
        filename: str,
        summary: str,
        categories: list[dict[str, object]],
    ) -> ClassificationResult:
        active = [item for item in categories if item.get("active") and not item.get("is_system")]
        if not active:
            return ClassificationResult(None, None, "没有可用的业务分类", "pending")
        if not self.generator.ready:
            return ClassificationResult(None, None, "分类模型不可用", "failed")
        choices = "\n".join(
            f"- {item['category_id']} | {item['name']} | {item.get('description', '')}"
            for item in active
        )
        prompt = f"""你是企业资料分类器。只能从候选分类中选择，不得创建分类。
仅输出 JSON：{{"category_id":"cat_xxx","confidence":0.0,"reason":"简短依据"}}

文件名：{filename}
解析摘要：{summary[:2000]}
候选分类：
{choices}
"""
        try:
            text, _ = self.generator.generate(prompt)
            payload = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
            category_id = str(payload["category_id"])
            confidence = float(payload["confidence"])
            reason = str(payload.get("reason", ""))[:300]
            if category_id not in {str(item["category_id"]) for item in active}:
                raise ValueError("unknown category")
            if not 0 <= confidence <= 1:
                raise ValueError("invalid confidence")
        except Exception:
            return ClassificationResult(None, None, "分类模型返回无效", "failed")
        return ClassificationResult(
            category_id,
            confidence,
            reason,
            "auto_assigned" if confidence >= self.threshold else "review_required",
        )
