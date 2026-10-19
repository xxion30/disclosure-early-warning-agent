"""SQLAlchemy 엔진과 세션."""
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from shared.config import ROOT_DIR, get_settings


def _resolve_url(url: str) -> str:
    """sqlite 상대 경로는 프로젝트 루트 기준으로 바꾼다 (어디서 실행해도 같은 파일)."""
    prefix = "sqlite:///"
    if url.startswith(prefix) and not url.startswith("sqlite:////"):
        path = Path(url[len(prefix):])
        if not path.is_absolute():
            path = ROOT_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"{prefix}{path}"
    return url


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            _resolve_url(get_settings().DB_URL),
            connect_args={"check_same_thread": False},
        )
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


@contextmanager
def get_session():
    """with get_session() as s: ... — 정상 종료 시 commit, 예외 시 rollback."""
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """테이블이 없으면 만든다."""
    from shared.models import Base

    Base.metadata.create_all(get_engine())
