from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from shared.models import Base, CollectCursor
from shared.collection import reserve_stocks, finish_stock
from risk_engine.financials import ratio, comparable


def test_collection_reservation_and_failed_cursor():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert reserve_stocks(s,'run-a',['001570'])==['001570']
        assert reserve_stocks(s,'run-b',['001570'])==[]
        finish_stock(s,'run-a','001570',success_at='2025-03-21T09:05:00+09:00')
        assert reserve_stocks(s,'run-b',['001570'])==['001570']
        finish_stock(s,'run-b','001570')
        assert s.get(CollectCursor,'001570').last_success_at=='2025-03-21T09:05:00+09:00'


def test_period_and_ratio_exceptions():
    annual={'basis':'CONSOLIDATED','period_kind':'ANNUAL','period':'2025'}
    quarter={'basis':'CONSOLIDATED','period_kind':'QUARTER','period_slot':'Q1'}
    assert comparable(annual,{**annual,'period':'2024'},annual=True)
    assert not comparable(annual,{**annual,'period':'2022'},annual=True)
    assert not comparable(annual,quarter,annual=True)
    assert comparable(quarter,quarter)
    assert not comparable(quarter,{**quarter,'period_slot':'Q2'})
    for denominator in (0,-1,None):
        assert ratio(10,denominator)=={'value':None,'status':'MANUAL_CHECK'}
    assert ratio(10,2)=={'value':5,'status':'OK'}


def test_expired_collection_releases_only_its_own_reservation():
    from datetime import datetime, timezone
    from shared.models import CollectRun
    from shared.collection import recover_expired_runs, heartbeat_run
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(CollectRun(run_id='old',stock_codes=['001570'],started_at='2025-03-21T00:00:00+00:00',
                          lease_expires_at='2025-03-21T00:10:00+00:00',status='PENDING'))
        db.flush();reserve_stocks(db,'old',['001570']);db.commit()
        # A late worker cannot advance the cursor even before recovery has run.
        assert finish_stock(db,'old','001570',success_at='2025-03-21T00:12:00+00:00',
                            now=datetime(2025,3,21,0,11,tzinfo=timezone.utc)) is False
        assert db.get(CollectCursor,'001570').last_success_at is None
        assert recover_expired_runs(db,now=datetime(2025,3,21,0,11,tzinfo=timezone.utc))==['old']
        assert not heartbeat_run(db,'old',now=datetime(2025,3,21,0,11,tzinfo=timezone.utc))
        assert reserve_stocks(db,'new',['001570'])==['001570']
        finish_stock(db,'old','001570',success_at='2025-03-21T00:12:00+00:00')
        cursor=db.get(CollectCursor,'001570')
        assert cursor.last_success_at is None and cursor.active_run_id=='new'
    engine.dispose()


def test_live_collection_lease_is_extended_and_preserved():
    from datetime import datetime, timezone
    from shared.models import CollectRun
    from shared.collection import recover_expired_runs, heartbeat_run
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(CollectRun(run_id='live',stock_codes=['001570'],started_at='2025-03-21T00:00:00+00:00',
                          lease_expires_at='2025-03-21T00:10:00+00:00',status='PENDING'));db.flush()
        reserve_stocks(db,'live',['001570'])
        now=datetime(2025,3,21,0,9,tzinfo=timezone.utc)
        assert heartbeat_run(db,'live',now=now)
        assert recover_expired_runs(db,now=datetime(2025,3,21,0,11,tzinfo=timezone.utc))==[]
        assert db.get(CollectCursor,'001570').active_run_id=='live'
    engine.dispose()
