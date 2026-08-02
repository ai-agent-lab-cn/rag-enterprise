from functools import lru_cache
from typing import Any

from sentence_transformers import CrossEncoder, SentenceTransformer

from .config import get_settings
from .errors import AppError

# Embedding、CrossEncoder、Gemini 封装
class EmbeddingModel:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()


class Reranker:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = CrossEncoder(model_name)

    def score(self, question: str, chunks: list[str]) -> list[float]:
        if not chunks:
            return []
        scores = self._model.predict([(question, chunk) for chunk in chunks])
        return [float(score) for score in scores]


class GeminiGenerator:
    def __init__(self, api_key: str | None, model_name: str):
        self.api_key = api_key
        self.model_name = model_name

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str) -> tuple[str, dict[str, Any]]:
        if not self.api_key:
            return "未配置 Gemini API Key，已完成检索但无法生成答案。请根据下方来源查看相关内容。", {}

        from google import genai

        try:
            response = genai.Client(api_key=self.api_key).models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
        except TimeoutError as exc:
            raise AppError("MODEL_TIMEOUT", "生成模型响应超时，请稍后重试。", 504) from exc
        except Exception as exc:
            raise AppError("MODEL_UNAVAILABLE", "生成模型暂时不可用，检索结果未受影响。", 502) from exc
        candidates = getattr(response, "candidates", None) or []
        content = getattr(candidates[0], "content", None) if candidates else None
        parts = getattr(content, "parts", None) or []
        text = "".join(part_text for part in parts if (part_text := getattr(part, "text", None)))
        return text or "模型没有返回文本答案。", response.model_dump(exclude_none=True)


@lru_cache
def get_embedding_model() -> EmbeddingModel:
    settings = get_settings()
    return EmbeddingModel(settings.embedding_model)


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    return Reranker(settings.reranker_model)


@lru_cache
def get_generator() -> GeminiGenerator:
    settings = get_settings()
    return GeminiGenerator(settings.gemini_api_key, settings.generation_model)
