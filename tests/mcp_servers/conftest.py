"""도구 테스트용: 임시 SQLite에 실제 데모 데이터(scripts/seed_db.py)를 적재한다."""
import pytest

import shared.db as db_module
from shared.config import get_settings


@pytest.fixture()
def seeded_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    get_settings.cache_clear()
    db_module._engine = None
    db_module._SessionLocal = None

    from scripts import seed_db

    db_module.init_db()
    with db_module.get_session() as s:
        seed_db.seed_people(s)
        corp_of = seed_db.seed_companies(s)
        seed_db.seed_disclosures(s, corp_of)
    yield
    db_module._engine = None
    db_module._SessionLocal = None
    get_settings.cache_clear()
