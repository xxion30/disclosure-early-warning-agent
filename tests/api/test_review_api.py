from sqlalchemy import select, create_engine
from sqlalchemy.orm import Session
from shared.models import RiskAssessment, RiskState, AuditLog, ContactTask, StateRead, Base, Company, PB
from api.auth import CurrentPB
from api.routes.states import acknowledge, Acknowledge
from tests.api.helpers import read_events, RCEPT
from risk_engine.states import update_state
from risk_engine.stages import calculate


def pending(client,headers):
    assert client.post('/runs',headers=headers,json={'rcept_no':RCEPT}).status_code==202
    return read_events(client,headers)[-1][1]['assessment_id']


def test_resolved_risk_blocks_old_approval(client,auth_headers,session_factory):
    aid=pending(client,auth_headers)
    with session_factory() as db:
        db.add(RiskState(corp_code='TEST-CORP',stock_code='001570',stage='NONE',resolved=True,
                         state_version=2,alert_version=1,updated_at='2025-03-22',latest_filed_at='2025-03-22'))
        db.get(RiskAssessment,aid).risk_state_version=1
    response=client.post(f'/alerts/{aid}/decision',headers=auth_headers,
                         json={'decisions':[{'customer_id':'C001','action':'approve'}]})
    assert response.status_code==409
    with session_factory() as db:
        assert db.scalar(select(AuditLog.log_id)) is None
        assert db.scalar(select(ContactTask.task_id)) is None


def test_new_version_blocks_old_approval(client,auth_headers,session_factory):
    aid=pending(client,auth_headers)
    with session_factory() as db:
        db.add(RiskState(corp_code='TEST-CORP',stock_code='001570',stage='RED',state_version=2,
                         alert_version=1,updated_at='2025-03-22',latest_filed_at='2025-03-22'))
        db.get(RiskAssessment,aid).risk_state_version=1
    assert client.post(f'/alerts/{aid}/decision',headers=auth_headers,
                      json={'decisions':[{'customer_id':'C001','action':'approve'}]}).status_code==409


def test_explanation_score_guard_keeps_financial_amounts(client,auth_headers,session_factory):
    with session_factory() as db:
        db.add(RiskAssessment(corp_code='TEST-CORP',rcept_no=RCEPT,stage='RED',level='Critical',
                              status='DONE',created_at='2025-03-21',ai_explanation='[규칙 요약] 항목: 5점 (반영률 1).'))
        db.add(RiskState(corp_code='TEST-CORP',stock_code='001570',stage='RED',updated_at='2025-03-21',latest_filed_at='2025-03-21'))
    response=client.get('/states/001570',headers=auth_headers)
    assert response.status_code==200 and '5점' not in response.text
    from shared.presentation import public_explanation
    assert public_explanation('자기자본 대비 50%, 금액 100원')=='자기자본 대비 50%, 금액 100원'


def test_alerts_stage_priority_and_summary(client,auth_headers,session_factory):
    with session_factory() as db:
        for st,points in [('ORANGE',4),('YELLOW',5)]:
            db.add(RiskAssessment(corp_code='TEST-CORP',rcept_no=RCEPT,stage=st,level='High',total_score=points,
                                  status='DONE',created_at='2025-03-21',reasons=[{'group':st,'points':points,'item':'항목','rcept_no':RCEPT}]))
    rows=client.get('/alerts',headers=auth_headers).json()
    assert [r['stage'] for r in rows]==['ORANGE','YELLOW']
    response=client.get(f"/alerts/{rows[0]['assessment_id']}",headers=auth_headers)
    assert response.status_code==200 and response.json()['summary']['this_filing']['items']


def test_acknowledgment_stale_session_cannot_reduce_version(tmp_path,monkeypatch):
    engine=create_engine(f'sqlite:///{tmp_path / "race.db"}');Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(Company(corp_code='fixture',stock_code='001570',name='fixture'))
        db.add(PB(pb_id='fixture',name='fixture',email='fixture@example.invalid',password_hash='unused'))
        db.flush()
        db.add(RiskState(corp_code='fixture',stock_code='001570',stage='RED',alert_version=2,
                         updated_at='2025-03-21',latest_filed_at='2025-03-21'))
        db.add(StateRead(pb_id='fixture',corp_code='fixture',tenant_id='default',read_version=0));db.commit()
    import api.routes.states as endpoint
    monkeypatch.setattr(endpoint,'visible_state',lambda db,pb,stock: db.get(RiskState,'fixture'))
    with Session(engine,expire_on_commit=False) as first,Session(engine,expire_on_commit=False) as second:
        stale=second.get(StateRead,('fixture','fixture'))
        pb=CurrentPB(pb_id='fixture',name='fixture')
        acknowledge('001570',Acknowledge(version=2),pb,first);first.commit()
        acknowledge('001570',Acknowledge(version=1),pb,second);second.commit()
    with Session(engine) as db:
        assert db.get(StateRead,('fixture','fixture')).read_version==2
    engine.dispose()


def test_attached_korean_score_expressions_are_also_hidden():
    from shared.presentation import public_explanation, MANUAL_EXPLANATION
    for text in ('위험은 5점입니다.','총점은 10입니다.','반영률은 0.5입니다.'):
        assert public_explanation(text)==MANUAL_EXPLANATION


def test_outdated_graph_review_cannot_reuse_new_analysis_version(client,auth_headers,session_factory):
    aid=pending(client,auth_headers)
    with session_factory() as db:
        db.add(RiskState(corp_code='TEST-CORP',stock_code='001570',stage='RED',state_version=2,
                         alert_version=1,updated_at='2025-03-22',latest_filed_at='2025-03-22'))
        assessment=db.get(RiskAssessment,aid)
        assessment.risk_state_version=2
        assessment.score_fingerprint='new-analysis-fingerprint'
    assert client.post(f'/alerts/{aid}/decision',headers=auth_headers,
                      json={'decisions':[{'customer_id':'C001','action':'approve'}]}).status_code==409
