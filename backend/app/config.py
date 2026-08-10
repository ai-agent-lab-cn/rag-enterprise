from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "RongRAG Studio"
    embedding_model: str = "shibing624/text2vec-base-chinese"
    reranker_model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
    generation_model: str = "gemini-3.6-flash"
    gemini_api_key: str | None = None
    chroma_path: Path = Path("data/chroma")
    upload_path: Path = Path("data/uploads")
    evaluation_reports_path: Path = Path("backend/evaluation/reports")
    collection_name: str = "rongrag_documents"
    chunk_size: int = Field(default=700, ge=100, le=4000)
    chunk_overlap: int = Field(default=100, ge=0, le=1000)
    max_upload_mb: int = Field(default=15, ge=1, le=100)
    frontend_origin: str = "http://localhost:5173"


@lru_cache
def get_settings() -> Settings:
    return Settings()
