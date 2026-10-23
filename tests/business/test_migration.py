"""Migration preserves pre-DARTCH rows; run twice to check Alembic idempotence."""
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect
from shared.config import ROOT_DIR, get_settings


def test_additive_upgrade_preserves_people(tmp_path,monkeypatch):
    url=f'sqlite:///{tmp_path / "legacy.db"}'
    engine=create_engine(url)
    with engine.begin() as c:
        c.execute(text('CREATE TABLE pbs (pb_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, email VARCHAR NOT NULL, password_hash VARCHAR NOT NULL)'))
        c.execute(text("INSERT INTO pbs VALUES ('P01','kept','kept@example.invalid','hash')"))
    monkeypatch.setenv('DB_URL',url); get_settings.cache_clear()
    try:
        cfg=Config(str(ROOT_DIR/'alembic.ini'))
        command.upgrade(cfg,'head'); command.upgrade(cfg,'head')
        with engine.connect() as c:
            assert c.execute(text('SELECT name,tenant_id,role FROM pbs')).one()==('kept','default','PB')
        assert {'risk_states','state_reads','collect_runs','financial_snapshots'} <= set(inspect(engine).get_table_names())
    finally:
        get_settings.cache_clear()
        engine.dispose()
