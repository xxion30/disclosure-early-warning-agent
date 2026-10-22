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
