from alembic import context
from sqlalchemy import create_engine
from shared.config import get_settings
from shared.models import Base


def run():
    from shared.db import _resolve_url
    url = _resolve_url(get_settings().DB_URL)
    if context.is_offline_mode():
        raise RuntimeError('기존 스키마를 검사하는 초기 마이그레이션은 온라인 실행이 필요합니다.')
    engine = create_engine(url)
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

run()
