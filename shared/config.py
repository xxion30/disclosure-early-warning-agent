"""환경 변수 읽기 (.env). 모든 설정은 여기서만 읽는다."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    ANTHROPIC_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    LLM_PRIMARY: str = "claude-haiku-5-5"
    LLM_FALLBACK: str = "gpt-5.4-mini"
    EMBEDDING_PROVIDER: str = "openai"  # openai | local
    EMBEDDING_MODEL: str = "text-embedding-3-small"

    LANGSMITH_TRACING: bool = True
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "disclosure-early-warning"

    DART_API_KEY: str = ""
    JWT_SECRET: str = "change-me"

    DB_URL: str = "sqlite:///data/app.db"
    CHECKPOINT_DB: str = "data/checkpoints.db"
    CHROMA_DIR: str = "data/chroma"

    BUSINESS_MCP_URL: str = "http://localhost:8001/mcp"
    RAG_MCP_URL: str = "http://localhost:8002/mcp"

    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    QUEUE_ENABLED: bool = False

    FAKE_LLM_ERROR: str = ""  # 예: "503" — 실패 시나리오용


@lru_cache
def get_settings() -> Settings:
    return Settings()
