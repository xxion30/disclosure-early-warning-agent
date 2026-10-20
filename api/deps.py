"""라우트들이 같이 쓰는 FastAPI 의존성."""
from collections.abc import Iterator

from sqlalchemy.orm import Session

from shared.db import get_session


def get_db() -> Iterator[Session]:
    """요청 하나에 세션 하나. 정상 종료 시 commit, 예외 시 rollback (shared.db.get_session)."""
    with get_session() as session:
        yield session
