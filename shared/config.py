"""환경 변수 읽기 (.env). 모든 설정은 여기서만 읽는다."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    APP_ENV: str = 'development'

    DB_URL: str = "sqlite:///data/app.db"
    CHECKPOINT_DB: str = "data/checkpoints.db"
    CHROMA_DIR: str = "data/chroma"

    MCP_HOST: str = "127.0.0.1"
    BUSINESS_MCP_URL: str = "http://localhost:8001/mcp"
    RAG_MCP_URL: str = "http://localhost:8002/mcp"

    RABBITMQ_URL: str = "amqp://guest:guest@localhost:5672/"
    QUEUE_ENABLED: bool = False
    COLLECTION_LEASE_SECONDS: int = Field(default=600, ge=3)
    COLLECT_DISPATCH_ENABLED: bool = False
    COLLECT_TIME: str = Field(default='09:05', pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    COLLECT_BACKFILL_MONTHS: int = Field(default=12, ge=1)
    DART_TIMEOUT_SECONDS: float = Field(default=30, gt=0)
    DART_RETRY_MAX: int = Field(default=3, ge=1, le=10)
    DART_MAX_RPS: float = Field(default=4, gt=0)
    DART_MAX_CONCURRENCY: int = Field(default=3, ge=1)
    PHASE1_RUNTIME_FACTORY: str = ''
    ANALYSIS_LEASE_SECONDS: int = Field(default=120, ge=3)
    ANALYSIS_RETRY_DELAY_SECONDS: int = Field(default=30, ge=0)

    # 공시 원문 색인 방식. persistent(기본): rag_docs/를 미리 ingest해 둔다(데모·평가).
    # ephemeral(운영): 공시 1건을 처리할 때 그 원문만 임시로 색인하고, 처리가 끝나면 색인과 본문을 지운다.
    RAG_INDEX_MODE: str = "persistent"

    FAKE_LLM_ERROR: str = ""  # 예: "503" — 실패 시나리오용
    FAKE_UNSAFE_DRAFT: bool = False  # true면 첫 안내문 초안에 금지 표현을 섞는다 (가드레일 → 다시 쓰기 시연용)

    GRAPH_ADAPTER: str = "fake"  # fake | real — API가 쓰는 Graph (C 제안, CLAUDE.md 4-7장)
    FAKE_GRAPH_DELAY: float = 0.4


@lru_cache
def get_settings() -> Settings:
    return Settings()
