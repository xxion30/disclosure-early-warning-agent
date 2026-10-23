from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine,inspect,text
from shared.config import ROOT_DIR,get_settings


def test_new_migrations_preserve_existing_data_and_are_repeatable(tmp_path,monkeypatch):
    url=f'sqlite:///{tmp_path / "migration.db"}'
    monkeypatch.setenv('DB_URL',url);get_settings.cache_clear()
    cfg=Config(str(ROOT_DIR/'alembic.ini'))
    command.upgrade(cfg,'20261022_02')
    engine=create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO pbs(pb_id,name,email,password_hash,tenant_id,role) VALUES('fixture','kept','fixture@example.invalid','unused','default','PB')"))
    command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with engine.connect() as db:
        assert db.execute(text("SELECT name FROM pbs WHERE pb_id='fixture'")).scalar_one()=='kept'
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='20261024_07'
    assert 'structured_facts' in {c['name'] for c in inspect(engine).get_columns('disclosures')}
    assert {'analysis_jobs','public_assessments','organization_reviews'} <= set(inspect(engine).get_table_names())
    for table in ('analysis_jobs','organization_reviews'):
        assert 'execution_budget' in {c['name'] for c in inspect(engine).get_columns(table)}
    engine.dispose();get_settings.cache_clear()
