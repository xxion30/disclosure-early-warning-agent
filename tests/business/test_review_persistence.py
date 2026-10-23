"""Use the repository's existing audit disclosure for persistence regressions."""
from shared.db import get_session
from shared.models import RiskState
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.save_assessment import save_assessment
from mcp_servers.business.tools.create_contact_task import create_contact_task
from risk_engine.states import update_state

CORP='00106119'
RCEPT='20250321802001'


def audit_score():
    result=calculate_risk_score(CORP,[{'signal_type':'AUDIT_OPINION','rcept_no':RCEPT,'detail':{'opinion':'의견거절'}}],'2025-03-21')
    assert result['ok']
    return result['data']


def test_approval_status_save_does_not_republish_old_score(seeded_db):
    original=audit_score()
    stored=save_assessment(CORP,RCEPT,original['signals'],original,status='WAITING_APPROVAL')
    assert stored['ok']
    refreshed=calculate_risk_score(CORP,[],'2025-03-22')['data']
    with get_session() as db:
        update_state(db,CORP,refreshed,'2025-03-21')
    again=save_assessment(CORP,RCEPT,original['signals'],original,status='DONE')
    assert again['ok']
    with get_session() as db:
        assert db.get(RiskState,CORP).as_of=='2025-03-22'


def test_contact_task_does_not_use_superseded_approval(seeded_db):
    original=audit_score()
    stored=save_assessment(CORP,RCEPT,original['signals'],original,status='WAITING_APPROVAL')
    with get_session() as db:
        state=db.get(RiskState,CORP)
        state.stage='NONE';state.resolved=True;state.state_version+=1
    result=create_contact_task(stored['data']['assessment_id'],'C001','{고객명}님, 공시를 확인해 주세요.')
    assert result['ok'] is False and result['error']['code']=='INVALID_INPUT'
