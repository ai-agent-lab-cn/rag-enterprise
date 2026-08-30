from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from .schemas import RETRYABLE_CLASSIFICATION_FAILURES


@dataclass(frozen=True)
class ClassificationResult:
    """分类结果。

    ``category_id`` 在 ``review_required`` 时是**建议**而非归属：低置信的猜测不写进
    资料的分类字段，只留给人一键确认。猜错的分类比没有分类更难被发现——前者看起来
    一切正常，后者至少在列表里显示为空。
    """

    category_id: str | None
    confidence: float | None
    reason: str
    status: str
    failure_code: str | None = None

    @property
    def retryable(self) -> bool:
        """只有外部环境问题才值得重试。

        模型响应格式不对、分类不存在、分类被停用、没有可用分类——这四种重试多少次
        都是同样的结果，它们要的是人去改配置，不是机器再等一会儿。
        """

        return self.failure_code in RETRYABLE_CLASSIFICATION_FAILURES


class ClassificationGenerator(Protocol):
    model_name: str

    @property
    def ready(self) -> bool: ...

    def generate(self, prompt: str) -> tuple[str, dict[str, object]]: ...


def _failure(code: str, reason: str) -> ClassificationResult:
    return ClassificationResult(None, None, reason, "failed", code)


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
        known = {str(item["category_id"]): item for item in categories}
        active = [item for item in categories if item.get("active")]
        if not active:
            # 此前这里返回 pending，与「排队等分类」无法区分，于是「分类字典是空的」
            # 这件事永远不会有人发现，资料就一直待分类下去。
            return _failure("NO_ACTIVE_CATEGORY", "知识库没有可用的业务分类")
        if not self.generator.ready:
            return _failure("MODEL_UNAVAILABLE", "分类模型不可用")

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
        except TimeoutError as error:
            return _failure("MODEL_TIMEOUT", f"分类模型超时：{error}"[:300])
        except Exception as error:  # noqa: BLE001 — 未知异常按可重试处理，见 retryable
            return _failure("UNKNOWN_ERROR", f"分类模型调用失败：{error}"[:300])

        try:
            payload = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
            category_id = str(payload["category_id"])
            confidence = float(payload["confidence"])
            reason = str(payload.get("reason", ""))[:300]
            if not 0 <= confidence <= 1:
                raise ValueError("confidence out of range")
        except Exception:  # noqa: BLE001 — 任何解析问题都归为响应无效，不可重试
            return _failure("INVALID_RESPONSE", "分类模型返回无效")

        # 「编了一个不存在的分类」和「选中的分类被停用了」处置完全不同：前者要看模型和
        # 提示词，后者要么启用该分类、要么人工改归属。合并成一个码等于把线索丢掉。
        if (selected := known.get(category_id)) is None:
            return _failure("CATEGORY_NOT_FOUND", f"模型返回了不存在的分类：{category_id}")
        if not selected.get("active"):
            return _failure("CATEGORY_INACTIVE", f"模型选中了已停用的分类：{selected['name']}")

        return ClassificationResult(
            category_id,
            confidence,
            reason,
            "auto_assigned" if confidence >= self.threshold else "review_required",
        )
