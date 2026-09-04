import json
import re
from dataclasses import dataclass
from functools import lru_cache
from threading import RLock
from collections.abc import Iterator
from typing import Any, Protocol
from urllib.request import Request, urlopen

from sentence_transformers import CrossEncoder, SentenceTransformer

from .config import get_settings
from .errors import AppError
from .generation_models import GenerationProviderState, PostgresGenerationProviderRepository

# Embedding、CrossEncoder、Gemini 封装
DEMO_LEXICAL_RERANKER = "demo/lexical-overlap-v1"


class EmbeddingModel:
    def __init__(self, model_name: str):
        self.model_name = model_name
        # 只读接口不应因为加载重量模型而失败。首次真正检索时再初始化，
        # 并关闭 transformers 的 meta tensor 低内存路径，兼容本地 CPU 环境。
        self._model: SentenceTransformer | None = None

    def _get_model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(
                self.model_name,
                device="cpu",
                model_kwargs={"low_cpu_mem_usage": False},
            )
        return self._model

    def encode(self, texts: list[str]) -> list[list[float]]:
        vectors = self._get_model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()


class Reranker:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model: CrossEncoder | None = None

    def _get_model(self) -> CrossEncoder:
        if self._model is None:
            self._model = CrossEncoder(
                self.model_name,
                device="cpu",
                model_kwargs={"low_cpu_mem_usage": False},
            )
        return self._model

    def score(self, question: str, chunks: list[str]) -> list[float]:
        if not chunks:
            return []
        if self.model_name == DEMO_LEXICAL_RERANKER:
            question_tokens = _lexical_tokens(question)
            if not question_tokens:
                return [0.0 for _ in chunks]
            return [
                len(question_tokens & _lexical_tokens(chunk)) / len(question_tokens)
                for chunk in chunks
            ]
        scores = self._get_model().predict([(question, chunk) for chunk in chunks])
        return [float(score) for score in scores]


def _lexical_tokens(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]|[a-z0-9]+", text.lower()))

# 生成器协议，用于统一不同模型提供者的接口。
class AnswerGenerator(Protocol):
    model_name: str
    provider_name: str

    @property
    def ready(self) -> bool: ...

    def generate(self, prompt: str) -> tuple[str, dict[str, Any]]: ...
    def generate_stream(self, prompt: str) -> Iterator[str]: ...

# Gemini 生成器
class GeminiGenerator:
    def __init__(self, api_key: str | None, model_name: str):
        self.api_key = api_key
        self.model_name = model_name
        self.provider_name = "gemini"

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
            raise _model_error("gemini", exc, 504) from exc
        except Exception as exc:
            raise _model_error("gemini", exc) from exc
        candidates = getattr(response, "candidates", None) or []
        content = getattr(candidates[0], "content", None) if candidates else None
        parts = getattr(content, "parts", None) or []
        text = "".join(part_text for part in parts if (part_text := getattr(part, "text", None)))
        return text or "模型没有返回文本答案。", response.model_dump(exclude_none=True)

    def generate_stream(self, prompt: str) -> Iterator[str]:
        if not self.api_key:
            yield "未配置 Gemini API Key。"
            return
        from google import genai
        try:
            for response in genai.Client(api_key=self.api_key).models.generate_content_stream(
                model=self.model_name,
                contents=prompt,
            ):
                if response.text:
                    yield response.text
        except TimeoutError as exc:
            raise _model_error("gemini", exc, 504) from exc
        except Exception as exc:
            raise _model_error("gemini", exc) from exc

# OpenAI 兼容生成器，适配 DeepSeek、Kimi 等提供商。
class OpenAICompatibleGenerator:
    def __init__(
        self,
        provider_name: str,
        api_key: str | None,
        model_name: str,
        base_url: str,
        *,
        disable_thinking: bool = False,
    ):
        self.provider_name = provider_name
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.disable_thinking = disable_thinking

    @property
    def ready(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str) -> tuple[str, dict[str, Any]]:
        if not self.api_key:
            return f"未配置 {self.provider_name} API Key，已完成检索但无法生成答案。请根据下方来源查看相关内容。", {}

        from openai import APITimeoutError, OpenAI

        try:
            request_options: dict[str, Any] = {}
            if self.disable_thinking:
                request_options["extra_body"] = {"thinking": {"type": "disabled"}}
            response = OpenAI(api_key=self.api_key, base_url=self.base_url).chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=False,
                **request_options,
            )
        except APITimeoutError as exc:
            raise _model_error(self.provider_name, exc, 504) from exc
        except Exception as exc:
            raise _model_error(self.provider_name, exc) from exc

        text = response.choices[0].message.content if response.choices else None
        return text or "模型没有返回文本答案。", response.model_dump(exclude_none=True)

    def generate_stream(self, prompt: str) -> Iterator[str]:
        if not self.api_key:
            yield f"未配置 {self.provider_name} API Key。"
            return
        from openai import APITimeoutError, OpenAI
        try:
            request_options: dict[str, Any] = {}
            if self.disable_thinking:
                request_options["extra_body"] = {"thinking": {"type": "disabled"}}
            stream = OpenAI(api_key=self.api_key, base_url=self.base_url).chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
                **request_options,
            )
            for event in stream:
                if event.choices and (content := event.choices[0].delta.content):
                    yield content
        except APITimeoutError as exc:
            raise _model_error(self.provider_name, exc, 504) from exc
        except Exception as exc:
            raise _model_error(self.provider_name, exc) from exc

# DeepSeek 生成器，继承自 OpenAICompatibleGenerator，并禁用思考模式。
class DeepSeekGenerator(OpenAICompatibleGenerator):
    def __init__(self, api_key: str | None, model_name: str, base_url: str):
        super().__init__("deepseek", api_key, model_name, base_url, disable_thinking=True)

# Kimi 生成器，继承自 OpenAICompatibleGenerator。
class KimiGenerator(OpenAICompatibleGenerator):
    def __init__(self, api_key: str | None, model_name: str, base_url: str):
        super().__init__("kimi", api_key, model_name, base_url)


@dataclass(frozen=True)
class ProviderBalance:
    status: str
    amount: float | None = None
    currency: str | None = None


def _fetch_balance(generator: AnswerGenerator) -> ProviderBalance:
    """读取供应商余额；Gemini API Key 暂无直接余额查询接口。"""
    if generator.provider_name == "gemini":
        return ProviderBalance(status="unsupported")
    if not isinstance(generator, OpenAICompatibleGenerator) or not generator.api_key:
        return ProviderBalance(status="error")

    endpoint = (
        f"{generator.base_url}/user/balance"
        if generator.provider_name == "deepseek"
        else f"{generator.base_url}/users/me/balance"
    )
    request = Request(endpoint, headers={"Authorization": f"Bearer {generator.api_key}"})
    with urlopen(request, timeout=10) as response:  # noqa: S310 - endpoint comes from administrator config
        payload = json.loads(response.read())

    if generator.provider_name == "deepseek":
        balances = payload.get("balance_infos") or []
        selected = next((item for item in balances if item.get("currency") == "CNY"), None)
        selected = selected or (balances[0] if balances else None)
        if not selected:
            return ProviderBalance(status="error")
        return ProviderBalance(
            status="available",
            amount=float(selected["total_balance"]),
            currency=str(selected["currency"]),
        )

    data = payload.get("data") or {}
    if "available_balance" not in data:
        return ProviderBalance(status="error")
    return ProviderBalance(
        status="available",
        amount=float(data["available_balance"]),
        currency="CNY",
    )


_STATUS_BY_CODE = {
    "MODEL_KEY_MISSING": "unconfigured",
    "MODEL_REGION_UNSUPPORTED": "region_unsupported",
    "MODEL_QUOTA_EXHAUSTED": "quota_exhausted",
    "MODEL_AUTH_FAILED": "auth_failed",
    "MODEL_RATE_LIMITED": "rate_limited",
    "MODEL_TIMEOUT": "timeout",
    "MODEL_NOT_FOUND": "model_not_found",
    "MODEL_UNAVAILABLE": "unavailable",
}

# 根据异常信息和状态码，生成统一的 AppError 对象，便于前端处理。
def _model_error(provider: str, exc: Exception, status_code: int | None = None) -> AppError:
    raw = str(exc).casefold()
    provider_label = {"deepseek": "DeepSeek", "gemini": "Gemini", "kimi": "Kimi"}[provider]
    raw_status = status_code or getattr(exc, "status_code", 0) or getattr(exc, "code", 0) or 0
    try:
        response_status = int(raw_status)
    except (TypeError, ValueError):
        response_status = 0
    if "location is not supported" in raw or "region" in raw and "not supported" in raw:
        return AppError("MODEL_REGION_UNSUPPORTED", f"当前国家或地区不支持 {provider_label} API。", 409)
    if any(term in raw for term in ("insufficient balance", "quota", "resource_exhausted", "insufficient_quota")) or response_status == 402:
        return AppError("MODEL_QUOTA_EXHAUSTED", f"{provider_label} 模型额度不足，请检查 Billing 或配额。", 409)
    if response_status in {401, 403}:
        return AppError("MODEL_AUTH_FAILED", f"{provider_label} API Key 无效或无模型调用权限。", 409)
    if response_status == 404:
        return AppError("MODEL_NOT_FOUND", f"{provider_label} 模型不存在或尚未开放。", 409)
    if response_status == 429:
        return AppError("MODEL_RATE_LIMITED", f"{provider_label} 请求频率受限，请稍后重试。", 429)
    if isinstance(exc, TimeoutError) or response_status == 504:
        return AppError("MODEL_TIMEOUT", "模型响应超时，当前仍保留检索来源。", 504)
    return AppError("MODEL_UNAVAILABLE", f"{provider_label} 暂时不可用，当前仍保留检索来源。", 502)

# 可切换生成器，用于在不同模型提供者之间进行切换。
class SwitchableGenerator:
    provider_name: str
    model_name: str

    def __init__(
        self,
        providers: dict[str, AnswerGenerator],
        repository: PostgresGenerationProviderRepository,
        default_provider: str,
    ):
        self.providers = providers
        self.repository = repository
        self._lock = RLock()
        repository.synchronize_catalog(
            {name: (generator.model_name, generator.ready) for name, generator in providers.items()},
            default_provider,
        )
        self._active_provider = repository.active_provider()
        self._sync_identity()

    def _sync_identity(self) -> None:
        self.provider_name = self._active_provider
        self.model_name = self.providers[self._active_provider].model_name

    @property
    def ready(self) -> bool:
        return self.providers[self._active_provider].ready

    def states(self) -> list[GenerationProviderState]:
        return self.repository.list()

    def check(self, provider: str, actor_id: str | None = None) -> GenerationProviderState:
        if provider not in self.providers:
            raise AppError("MODEL_PROVIDER_NOT_FOUND", "未找到该模型供应商。", 404)
        generator = self.providers[provider]
        if not generator.ready:
            state = self.repository.update_status(
                provider,
                status="unconfigured",
                status_code="MODEL_KEY_MISSING",
                status_message=f"{provider} API Key 未配置",
                updated_by=actor_id,
            )
            raise AppError("MODEL_KEY_MISSING", state.status_message, 409, details=_state_details(state))
        try:
            generator.generate('只回复 {"status":"ok"}')
        except AppError as exc:
            state = self._record_failure(provider, exc, actor_id)
            exc.details = _state_details(state)
            raise
        self.repository.update_status(
            provider,
            status="available",
            status_code=None,
            status_message="运行正常",
            updated_by=actor_id,
        )
        return self._refresh_balance(provider)

    def activate(self, provider: str, actor_id: str) -> list[GenerationProviderState]:
        with self._lock:
            self.check(provider, actor_id)
            try:
                self.repository.activate(provider, actor_id)
            except ValueError as exc:
                raise AppError("MODEL_UNAVAILABLE", "模型未通过可用性检测，未执行切换。", 409) from exc
            self._active_provider = provider
            self._sync_identity()
            return self.states()

    def generate(self, prompt: str) -> tuple[str, dict[str, Any]]:
        with self._lock:
            provider = self._active_provider
            generator = self.providers[provider]
            if not generator.ready:
                return generator.generate(prompt)
            try:
                text, metadata = generator.generate(prompt)
            except AppError as exc:
                self._record_failure(provider, exc)
                exc.details = {
                    "provider": provider,
                    "configured_model": generator.model_name,
                }
                raise
            self.repository.update_status(
                provider,
                status="available",
                status_code=None,
                status_message="运行正常",
            )
            return text, {
                **metadata,
                "provider": provider,
                "configured_model": generator.model_name,
            }

    def generate_stream(self, prompt: str) -> Iterator[str]:
        with self._lock:
            provider = self._active_provider
            generator = self.providers[provider]
        try:
            yield from generator.generate_stream(prompt)
        except AppError as exc:
            self._record_failure(provider, exc)
            raise
        self.repository.update_status(
            provider,
            status="available",
            status_code=None,
            status_message="运行正常",
        )

    def _record_failure(
        self,
        provider: str,
        exc: AppError,
        actor_id: str | None = None,
    ) -> GenerationProviderState:
        return self.repository.update_status(
            provider,
            status=_STATUS_BY_CODE.get(exc.code, "unavailable"),
            status_code=exc.code,
            status_message=exc.message,
            updated_by=actor_id,
        )

    def _refresh_balance(self, provider: str) -> GenerationProviderState:
        generator = self.providers[provider]
        settings = get_settings()
        try:
            balance = _fetch_balance(generator)
        except Exception:
            balance = ProviderBalance(status="error")
        return self.repository.update_balance(
            provider,
            balance_status=balance.status,
            balance_amount=balance.amount,
            balance_currency=balance.currency,
            balance_limit=settings.generation_balance_limit_for(provider),
        )


def _state_details(state: GenerationProviderState) -> dict[str, object]:
    return {
        "provider": state.provider,
        "status": state.status,
        "status_code": state.status_code,
        "status_message": state.status_message,
        "balance_status": state.balance_status,
        "balance_amount": float(state.balance_amount) if state.balance_amount is not None else None,
        "balance_currency": state.balance_currency,
        "balance_limit": float(state.balance_limit) if state.balance_limit is not None else None,
        "balance_checked_at": state.balance_checked_at.isoformat() if state.balance_checked_at else None,
    }


@lru_cache
def get_embedding_model() -> EmbeddingModel:
    settings = get_settings()
    return EmbeddingModel(settings.embedding_model)


@lru_cache
def get_reranker() -> Reranker:
    settings = get_settings()
    return Reranker(settings.reranker_model)


@lru_cache
def get_generator() -> AnswerGenerator:
    settings = get_settings()
    providers: dict[str, AnswerGenerator] = {
        "deepseek": DeepSeekGenerator(
            settings.deepseek_api_key,
            settings.generation_model_for("deepseek"),
            settings.deepseek_base_url,
        ),
        "gemini": GeminiGenerator(
            settings.gemini_api_key,
            settings.generation_model_for("gemini"),
        ),
        "kimi": KimiGenerator(
            settings.kimi_api_key,
            settings.generation_model_for("kimi"),
            settings.kimi_base_url,
        ),
    }
    if not settings.database_url:
        return providers[settings.default_generation_provider]
    return SwitchableGenerator(
        providers,
        PostgresGenerationProviderRepository(settings.database_url),
        settings.default_generation_provider,
    )
