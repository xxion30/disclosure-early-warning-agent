"""R5 public response contracts. State transitions use internal fixtures only."""
from fastapi.testclient import TestClient
from sqlalchemy import select
import pytest
import shared.db as db_module
from shared.models import (Disclosure,Company,RiskAssessment,RiskState,RiskStateChange,OrganizationReview,Customer)
from api.main import app
from api.auth import CurrentPB,get_current_pb
from api.graph_adapter import get_graph_adapter
from risk_engine.stages import calculate
from risk_engine.states import update_state
from shared.display import STAGE_LABELS
from api.routes.alerts import impact_summary

RCEPT='20250321802001'


@pytest.fixture
def client(seeded_db):
    app.dependency_overrides[get_current_pb]=lambda:CurrentPB(pb_id='P01',name='fixture')
    class NoGraph:
        async def get_state(self,rcept_no):raise RuntimeError('not-connected')
    app.dependency_overrides[get_graph_adapter]=lambda:NoGraph()
    try:
        with TestClient(app) as client:yield client
    finally:app.dependency_overrides.clear()


def publish_stage(stage, score=0):
    with db_module.get_session() as db:
        corp=db.get(Disclosure,RCEPT).corp_code
        result={'stage':stage,'active_stages':[stage],'sort_value':score,'orange_score':0,'yellow_score':0,
                'breakdown':[],'manual_check':False}
        row=update_state(db,corp,result,'2025-03-21')
        return corp,row.state_version


@pytest.mark.parametrize('stage,label',list(STAGE_LABELS.items()))
def test_stage_headline_uses_korean(stage,label):
    assessment=RiskAssessment(stage=stage,level='High',rcept_no=RCEPT,reasons=[],risk_state_version=1)
    assert f'현재 단계는 {label}입니다.' in impact_summary(assessment,None,None)['headline']


def test_customer_top_stage_uses_current_alerts_and_watch_is_not_exposure(client):
    publish_stage('YELLOW')
    rows=client.get('/customers').json()['customers']
    exposed=[row for row in rows if row['exposure']['alert_count']]
    assert exposed and all(row['exposure']['top_stage']=='YELLOW' for row in exposed)
    publish_stage('WATCH')
    rows=client.get('/customers').json()['customers']
    assert all(row['exposure']['top_stage'] is None for row in rows)


def test_old_none_and_real_none_are_distinguishable(client):
    with db_module.get_session() as db:
        corp=db.get(Disclosure,RCEPT).corp_code
        db.add(RiskAssessment(corp_code=corp,rcept_no=RCEPT,stage='NONE',level='Critical',status='DONE',created_at='2025-03-21'))
        db.add(RiskAssessment(corp_code=corp,rcept_no=RCEPT,stage='NONE',level='Low',status='DONE',
                              risk_state_version=1,created_at='2025-03-22'))
    rows=client.get('/alerts').json()
    legacy=next(row for row in rows if row['level']=='Critical')
    modern=next(row for row in rows if row['level']=='Low')
    assert legacy['legacy'] and not legacy['stage_available'] and legacy['stage_label'] is None
    assert not modern['legacy'] and modern['stage_available'] and modern['stage_label']=='경보 없음'


def signal(detail,quote='contract evidence'):
    return {'signal_type':'RIGHTS_OFFERING','event_id':'contract-event','rcept_no':RCEPT,'filed_at':'2025-03-21',
            'detail':detail,'evidence':[{'source':'contract','quote':quote,'rcept_no':RCEPT,'location':'contract'}]}


def test_change_summary_has_no_score_and_same_state_does_not_add_history(client):
    first=calculate(new_signals=[signal({'ratio_pct':20,'third_party':False,'payment_delayed_or_failed':False})],as_of='2025-03-21')
    with db_module.get_session() as db:
        disc=db.get(Disclosure,RCEPT);stock=db.get(Company,disc.corp_code).stock_code
        update_state(db,disc.corp_code,first,'2025-03-21')
    detail=client.get('/states/'+stock).json()
    assert not detail['comparison_available'] and detail['changes']==[]
    second=calculate(new_signals=[signal({'ratio_pct':20,'third_party':True,'payment_delayed_or_failed':False})],as_of='2025-03-21')
    with db_module.get_session() as db:
        update_state(db,disc.corp_code,second,'2025-03-21')
        update_state(db,disc.corp_code,second,'2025-03-21')
        assert len(db.scalars(select(RiskStateChange)).all())==2
    detail=client.get('/states/'+stock).json()
    assert detail['comparison_available']
    assert {'kind':'stage_up','from':'WATCH','to':'ORANGE'} in detail['changes']
    assert not any(word in str(detail['changes']) for word in ('points','score','weight'))
    expired=calculate(new_signals=[signal({'ratio_pct':20,'third_party':True,'payment_delayed_or_failed':False})],as_of='2027-03-21')
    with db_module.get_session() as db:update_state(db,disc.corp_code,expired,'2025-03-21')
    detail=client.get('/states/'+stock).json()
    assert not detail['resolved'] and not any(c['kind']=='resolved' for c in detail['changes'])


def test_missing_labels_and_unknown_legacy_graph_status_are_explicit(client):
    corp,version=publish_stage('YELLOW')
    with db_module.get_session() as db:
        row=db.get(RiskState,corp)
        row.items=[{'signal_type':'AUDIT_OPINION','missing':['going_concern'],'evidence':[]}]
        db.add(RiskAssessment(corp_code=corp,rcept_no=RCEPT,stage='YELLOW',level='High',
            risk_state_version=version,status='WAITING_APPROVAL',created_at='2025-03-21'))
        stock=row.stock_code
    detail=client.get('/states/'+stock).json()
    assert detail['items'][0]['missing_labels']==[{'code':'going_concern','label':'계속기업 불확실성'}]
    assert detail['assessment_id'] and detail['my_approval_pending'] is None
    assert detail['can_review'] is None and detail['review_status']=='UNKNOWN'


def test_organization_review_link_uses_current_tenant_and_owned_pending_customers(client):
    from tests.business.test_premerge_execution import publish
    aid=publish()
    with db_module.get_session() as db:
        review=db.scalar(select(OrganizationReview).where(OrganizationReview.assessment_id==aid))
        cid=db.scalar(select(Customer.customer_id).where(Customer.pb_id=='P01'))
        review.status='WAITING_APPROVAL';review.drafts={cid:'공시를 확인해 주세요.'};rid=review.review_id
        state=db.scalar(select(RiskState));stock=state.stock_code
    detail=client.get('/states/'+stock).json()
    assert detail['public_assessment_id']==aid and detail['review_id']==rid
    assert detail['my_approval_pending'] is True and detail['can_review'] is True
    with db_module.get_session() as db:db.get(RiskState,state.corp_code).resolved=True
    detail=client.get('/states/'+stock).json()
    assert detail['can_review'] is False and detail['my_approval_pending'] is False
