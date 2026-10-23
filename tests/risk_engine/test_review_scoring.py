"""Arithmetic and persistence regression fixtures; no new issuer facts or DART cases."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from shared.models import Base, Company
from risk_engine.stages import calculate
from risk_engine.states import update_state, StaleStateError


def observation(st, detail, event='one', filed='2025-03-21'):
    return {'signal_type':st,'detail':detail,'event_id':event,'filed_at':filed}


@pytest.fixture
def db():
    engine=create_engine('sqlite://');Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Company(corp_code='contract-fixture',stock_code='001570',name='contract fixture'));session.flush()
        yield session
    engine.dispose()


def test_zero_point_observation_does_not_notify(db):
    current=[observation('RIGHTS_OFFERING',{'ratio_pct':50})]
    row=update_state(db,'contract-fixture',calculate(new_signals=current,as_of='2025-03-21'),'2025-03-21')
    version=row.alert_version
    current.append(observation('AUDIT_OPINION',{'opinion':'적정','going_concern':False},event='benign'))
    result=calculate(new_signals=current,as_of='2025-03-21')
    assert not result['breakdown'][-1]['active']
    update_state(db,'contract-fixture',result,'2025-03-21')
    assert row.alert_version==version


def test_aged_result_cannot_be_replaced_by_old_analysis(db):
    data=[observation('RIGHTS_OFFERING',{'ratio_pct':50},filed='2024-09-21')]
    original=calculate(new_signals=data,as_of='2025-03-21')
    row=update_state(db,'contract-fixture',original,'2024-09-21')
    initial_version=row.state_version
    update_state(db,'contract-fixture',calculate(new_signals=data,as_of='2025-03-22'),'2024-09-21')
    assert row.orange_score==2 and row.state_version>initial_version
    with pytest.raises(StaleStateError):
        update_state(db,'contract-fixture',original,'2024-09-21')
    assert row.orange_score==2


def test_compare_and_swap_rejects_same_day_race(db):
    result=calculate(new_signals=[observation('CB_BW',{})],as_of='2025-03-21')
    row=update_state(db,'contract-fixture',{**result,'expected_state_version':0},'2025-03-21')
    with pytest.raises(StaleStateError):
        update_state(db,'contract-fixture',{**result,'expected_state_version':0},'2025-03-21')
    assert row.state_version==1


def test_calendar_refresh_without_changed_risk_does_not_invalidate_draft(db):
    data=[observation('CB_BW',{})]
    row=update_state(db,'contract-fixture',calculate(new_signals=data,as_of='2025-03-21'),'2025-03-21')
    version=row.state_version
    update_state(db,'contract-fixture',calculate(new_signals=data,as_of='2025-03-22'),'2025-03-21')
    assert row.state_version==version and row.as_of=='2025-03-22'
