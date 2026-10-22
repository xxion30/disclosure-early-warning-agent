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
            connect_args={"check_same_thread": False} if get_settings().DB_URL.startswith("sqlite") else {},
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


def _ensure_columns(engine: Engine) -> None:
    """이미 만들어진 DB(data/app.db)에 나중에 모델에 추가한 칸(nullable)을 채워 넣는다. 데이터는 건드리지 않는다."""
    from sqlalchemy import inspect, text

    from shared.models import Base

    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have and not col.primary_key and (col.nullable or col.server_default is not None):
                    default = ""
                    if col.server_default is not None:
                        arg = col.server_default.arg
                        value = "'" + arg.replace("'", "''") + "'" if isinstance(arg, str) else str(arg.compile(dialect=engine.dialect))
                        default = f" DEFAULT {value}"
                    nullable = "" if col.nullable else " NOT NULL"
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}{default}{nullable}'))



# 영향도(P1~P3) 폐지로 모델에서 뺀 옛 칸. 기존 DB에 NOT NULL로 남아 있으면 새 업무를 만들 수 없으므로 지운다.
LEGACY_COLUMNS = {"contact_tasks": ("priority", "impact_score")}


def _drop_legacy_columns(engine: Engine) -> None:
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in LEGACY_COLUMNS.items():
            if not insp.has_table(table):
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for col in cols:
                if col in have:
                    conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{col}"'))


def init_db() -> None:
    """테이블이 없으면 만들고, 모델에 새로 생긴 칸이 기존 DB에 없으면 추가한다."""
    from shared.models import Base

    engine = get_engine()
    if engine.dialect.name == 'postgresql':
        from sqlalchemy import inspect
        if 'alembic_version' not in inspect(engine).get_table_names():
            raise RuntimeError('PostgreSQL은 먼저 uv run alembic upgrade head를 실행하세요.')
        return
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    # Preserve the pre-existing SQLite cleanup for removed impact columns.
    _drop_legacy_columns(engine)
    from sqlalchemy import text
    with engine.begin() as conn:
        if engine.dialect.name == 'sqlite':
            conn.execute(text("INSERT OR IGNORE INTO tenants (tenant_id,name) VALUES ('default','Default')"))
        else:
            conn.execute(text("INSERT INTO tenants (tenant_id,name) VALUES ('default','Default') ON CONFLICT DO NOTHING"))
