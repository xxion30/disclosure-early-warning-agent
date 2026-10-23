"""Opt-in PostgreSQL tests. Each test uses its own generated schema and removes only that schema."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from shared.config import ROOT_DIR, get_settings
from shared.models import Base, Company, PB, Customer, Holding, RiskState, StateRead


@pytest.fixture
def postgres(monkeypatch):
    url=os.environ.get('R4_POSTGRES_TEST_URL')
    if not url:
        pytest.skip('R4_POSTGRES_TEST_URL is required for isolated PostgreSQL verification')
    root=create_engine(url)
    schema='r4_review_'+uuid4().hex
    with root.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped=make_url(url).update_query_dict({'options':f'-csearch_path={schema}'})
    engine=create_engine(scoped)
    monkeypatch.setenv('DB_URL',scoped.render_as_string(hide_password=False));get_settings.cache_clear()
    try:
        yield engine
    finally:
        engine.dispose()
        with root.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        root.dispose();get_settings.cache_clear()


def test_postgres_additive_review_migration(postgres):
    cfg=Config(str(ROOT_DIR/'alembic.ini'))
    command.upgrade(cfg,'20261022_01')
    with postgres.begin() as db:
        db.execute(text("INSERT INTO pbs (pb_id,name,email,password_hash) VALUES ('fixture','kept','kept@example.invalid','unused')"))
    command.upgrade(cfg,'head');command.upgrade(cfg,'head')
    with postgres.connect() as db:
        assert db.execute(text("SELECT name FROM pbs WHERE pb_id='fixture'")).scalar_one()=='kept'
        assert db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()=='20261022_02'
    assert 'state_version' in {c['name'] for c in inspect(postgres).get_columns('risk_states')}


def test_postgres_acknowledgments_are_monotonic(postgres):
    from api.auth import CurrentPB
    from api.routes.states import acknowledge, Acknowledge
    Base.metadata.create_all(postgres)
    with Session(postgres) as db:
        db.add(Company(corp_code='fixture',stock_code='001570',name='fixture'))
        db.add(PB(pb_id='fixture',name='fixture',email='fixture@example.invalid',password_hash='unused'));db.flush()
        db.add(Customer(customer_id='fixture',pb_id='fixture',name='fixture',risk_profile='fixture',total_assets=1));db.flush()
        db.add(Holding(customer_id='fixture',stock_code='001570',eval_amount=1))
        db.add(RiskState(corp_code='fixture',stock_code='001570',stage='RED',alert_version=2,
                         updated_at='2025-03-21',latest_filed_at='2025-03-21'))
        db.add(StateRead(pb_id='fixture',corp_code='fixture',tenant_id='default',read_version=0));db.commit()
    barrier=Barrier(2);high_done=Event();pb=CurrentPB(pb_id='fixture',name='fixture')
    def request(version):
        with Session(postgres,expire_on_commit=False) as db:
            stale=db.get(StateRead,('fixture','fixture'))
            barrier.wait(timeout=10)
            if version==1:
                assert high_done.wait(timeout=10)
            acknowledge('001570',Acknowledge(version=version),pb,db);db.commit()
            if version==2:high_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(request,version) for version in (1,2)]
        for future in futures:future.result(timeout=20)
    with Session(postgres) as db:
        assert db.get(StateRead,('fixture','fixture')).read_version==2


def test_postgres_state_compare_and_swap_serializes_publishers(postgres):
    from risk_engine.stages import calculate
    from risk_engine.states import update_state, StaleStateError
    Base.metadata.create_all(postgres)
    with Session(postgres) as db:
        db.add(Company(corp_code='fixture',stock_code='001570',name='fixture'));db.commit()
    barrier=Barrier(2)
    def publish(event):
        result=calculate(new_signals=[{'signal_type':'DEFAULT','event_id':event,'detail':{},'filed_at':'2025-03-21'}],as_of='2025-03-21')
        result['expected_state_version']=0
        with Session(postgres) as db:
            barrier.wait(timeout=10)
            try:
                update_state(db,'fixture',result,'2025-03-21');db.commit()
                return 'stored'
            except StaleStateError:
                db.rollback();return 'stale'
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(publish,event) for event in ('one','two')]
        outcomes=[future.result(timeout=20) for future in futures]
    assert sorted(outcomes)==['stale','stored']
    with Session(postgres) as db:
        assert db.get(RiskState,'fixture').state_version==1
