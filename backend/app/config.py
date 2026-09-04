from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "RongRAG Studio"
    embedding_model: str = "shibing624/text2vec-base-chinese"
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    generation_provider: Literal["gemini", "deepseek", "kimi"] | None = None
    generation_model: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: str | None = None
    deepseek_balance_limit: float | None = Field(default=None, gt=0)
    gemini_model: str = "gemini-3.6-flash"
    gemini_api_key: str | None = None
    kimi_model: str = "kimi-k2.6"
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_api_key: str | None = None
    kimi_balance_limit: float | None = Field(default=None, gt=0)
    upload_path: Path = Path("data/uploads")
    knowledge_bases_path: Path = Path("data/knowledge_bases/registry.json")
    conversations_path: Path = Path("data/conversations/records.json")
    auth_path: Path = Path("data/auth/store.json")
    audit_path: Path = Path("data/audit/events.json")
    database_url: str | None = None
    required_database_schema_version: int = Field(default=23, ge=1)
    index_worker_id: str = "worker-local"
    index_job_max_attempts: int = Field(default=3, ge=1, le=10)
    index_job_stale_seconds: int = Field(default=900, ge=60, le=86400)
    # 单次同步的删除比例超过该阈值即熔断中止，防止根目录配错被当成"全部删除"。
    sync_delete_threshold_percent: int = Field(default=30, ge=1, le=100)
    # 删除量不超过该绝对下限时不熔断：纯比例阈值在小知识库上会把日常删除全拦下。
    sync_delete_minimum: int = Field(default=3, ge=0, le=1000)
    evaluation_reports_path: Path = Path("backend/evaluation/reports")
    demo_seed_path: Path | None = None
    chunk_size: int = Field(default=700, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0, le=1000)
    # 默认保持纯向量召回；hybrid 的实测收益确认前不改默认值。
    retrieval_mode: Literal["vector", "hybrid"] = "vector"
    max_upload_mb: int = Field(default=15, ge=1, le=100)
    max_request_body_mb: int = Field(default=16, ge=1, le=101)
    max_filename_chars: int = Field(default=160, ge=32, le=255)
    login_rate_limit: int = Field(default=10, ge=1, le=100)
    expensive_rate_limit: int = Field(default=60, ge=1, le=1000)
    rate_limit_window_seconds: int = Field(default=60, ge=10, le=3600)
    max_concurrent_expensive_requests: int = Field(default=4, ge=1, le=32)
    session_ttl_hours: int = Field(default=12, ge=1, le=168)
    frontend_origin: str = "http://localhost:5173"
    app_environment: Literal["development", "test", "production"] = "development"

    @field_validator("demo_seed_path", mode="before")
    @classmethod
    def empty_demo_seed_is_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("deepseek_balance_limit", "kimi_balance_limit", mode="before")
    @classmethod
    def empty_balance_limit_is_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_security_boundaries(self) -> "Settings":
        origins = self.frontend_origins
        if not origins or any(origin == "*" for origin in origins):
            raise ValueError("FRONTEND_ORIGIN must contain explicit origins")
        if self.app_environment == "production" and any(
            not origin.startswith("https://") for origin in origins
        ):
            raise ValueError("production FRONTEND_ORIGIN values must use https")
        if self.max_request_body_mb <= self.max_upload_mb:
            raise ValueError("MAX_REQUEST_BODY_MB must be larger than MAX_UPLOAD_MB")
        return self

    @property
    def frontend_origins(self) -> list[str]:
        return [item.strip().rstrip("/") for item in self.frontend_origin.split(",") if item.strip()]

    @property
    def default_generation_provider(self) -> str:
        if self.generation_provider:
            return self.generation_provider
        if self.generation_model and self.generation_model.startswith("gemini"):
            return "gemini"
        return "deepseek"

    def generation_model_for(self, provider: str) -> str:
        configured = {
            "deepseek": self.deepseek_model,
            "gemini": self.gemini_model,
            "kimi": self.kimi_model,
        }[provider]
        if provider == self.default_generation_provider and self.generation_model:
            return self.generation_model
        return configured

    def generation_key_for(self, provider: str) -> str | None:
        return {
            "deepseek": self.deepseek_api_key,
            "gemini": self.gemini_api_key,
            "kimi": self.kimi_api_key,
        }[provider]

    def generation_balance_limit_for(self, provider: str) -> float | None:
        return {
            "deepseek": self.deepseek_balance_limit,
            "gemini": None,
            "kimi": self.kimi_balance_limit,
        }[provider]


@lru_cache
def get_settings() -> Settings:
    return Settings()
