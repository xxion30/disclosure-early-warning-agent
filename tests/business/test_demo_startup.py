"""Destructive initialization must stay within the dedicated demo database."""
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from scripts.ops.prepare_demo import main, require_demo_target, seed_demo


@pytest.mark.parametrize('url', [
    'sqlite:///data/app.db',
    'postgresql+psycopg://demo_owner:local-demo-only@postgres:5432/other',
    'postgresql+psycopg://demo_owner:local-demo-only@remote:5432/dartch_demo',
    'postgresql+psycopg://production:secret@postgres:5432/dartch_demo',
])
def test_rejects_other_databases_before_any_io(monkeypatch, url):
    import scripts.ops.prepare_demo as prepare
    monkeypatch.setattr(prepare, 'get_settings', lambda: SimpleNamespace(DB_URL=url, CHECKPOINT_DB='unused'))
    monkeypatch.setattr(prepare.psycopg, 'connect', lambda *a, **k: pytest.fail('DB access before target check'))
    with pytest.raises(RuntimeError, match='초기화는 거부'):
        main()


def test_rejects_external_checkpoint():
    with pytest.raises(RuntimeError):
        require_demo_target(SimpleNamespace(
            DB_URL='postgresql+psycopg://demo_owner:local-demo-only@postgres:5432/dartch_demo',
            CHECKPOINT_DB='data/checkpoints.db'))


@pytest.mark.parametrize('failure', ['provider', 'starter_filing'])
def test_failed_preflight_never_drops_database(monkeypatch, failure):
    import scripts.ops.prepare_demo as prepare
    import scripts.ops.check_providers as providers
    settings = SimpleNamespace(
        DB_URL='postgresql+psycopg://demo_owner:local-demo-only@postgres:5432/dartch_demo',
        CHECKPOINT_DB='postgresql://demo_owner:local-demo-only@postgres:5432/dartch_demo')
    monkeypatch.setattr(prepare, 'get_settings', lambda: settings)

    async def check():
        return [{'provider': 'openai', 'ok': failure != 'provider', 'error_code': '401'}]

    async def fetch():
        raise RuntimeError('starter API unavailable')

    monkeypatch.setattr(providers, 'check', check)
    monkeypatch.setattr(prepare, 'fetch_example', fetch)
    monkeypatch.setattr(prepare.psycopg, 'connect', lambda *a, **k: pytest.fail('preflight failed but reset attempted'))
    with pytest.raises((SystemExit, RuntimeError)):
        main()


def test_source_data_is_not_a_precomputed_judgment(tmp_path, monkeypatch):
    import shared.db as db
    from shared.config import get_settings
    from shared.models import AnalysisJob, Company, ContactTask, Customer, Disclosure, Holding, PB
    monkeypatch.setenv('DB_URL', f'sqlite:///{tmp_path / "demo.db"}')
    get_settings.cache_clear()
    old_engine, old_factory = db._engine, db._SessionLocal
    db._engine = db._SessionLocal = None
    try:
        db.init_db()
        seed_demo()
        with db.get_session() as session:
            assert session.scalar(select(func.count()).select_from(Customer)) == 20
            assert session.scalar(select(func.count()).select_from(Holding)) == 42
            assert session.get(PB, 'P01').role == 'ADMIN'
            assert session.get(PB, 'P02').role == 'PB'
            from api.auth import verify_password
            assert verify_password('demo1234', session.get(PB, 'P01').password_hash)
            assert session.scalar(select(func.count()).select_from(Disclosure)) == 594
            assert all(d.body_text is None and d.analysis_ready_at is None for d in session.scalars(select(Disclosure)))
            assert all(c.base_score == 0 and c.base_reasons == [] for c in session.scalars(select(Company)))
            assert session.scalar(select(func.count()).select_from(ContactTask)) == 0
            assert session.scalar(select(func.count()).select_from(AnalysisJob)) == 0
    finally:
        if db._engine is not None:
            db._engine.dispose()
        db._engine, db._SessionLocal = old_engine, old_factory
        get_settings.cache_clear()
