import pytest
from shared.models import Company, Holding, PB, Customer, RiskState, Tenant
from api.auth import hash_password
from tests.api.helpers import login
from risk_engine.stages import calculate
from risk_engine.states import update_state


@pytest.fixture
def states_world(session_factory):
    with session_factory() as s:
        s.add(Tenant(tenant_id='other',name='Other'))
        s.add(PB(pb_id='OTHER',name='Other',email='other@demo.local',tenant_id='other',password_hash=hash_password('test')))
        s.add(Customer(customer_id='OTHER-C',pb_id='OTHER',tenant_id='other',name='Other',risk_profile='test',total_assets=1))
        s.add(Company(corp_code='00303873',stock_code='079160',name='CJ CGV'))
        s.flush()
        s.add(Holding(customer_id='OTHER-C',stock_code='079160',eval_amount=1,tenant_id='other'))
        for corp,st in [('TEST-CORP','DEFAULT'),('00303873','AUDIT_OPINION')]:
            r=calculate(new_signals=[{'signal_type':st,'event_id':corp,'filed_at':'2025-03-21','detail':{'opinion':'거절'}}],as_of='2025-03-21')
            update_state(s,corp,r,'2025-03-21')


def test_states_hide_scores_and_enforce_pb_visibility(client,auth_headers,session_factory,states_world):
    response=client.get('/states?alert=true',headers=auth_headers)
    assert response.status_code==200
    body=response.json()
    assert body['items'][0]['stage']=='RED'
    assert not any('score' in key for row in body['items'] for key in row)
    with session_factory() as s:
        owned=list(s.query(Holding.stock_code).join(Customer,Customer.customer_id==Holding.customer_id).filter(Customer.pb_id=='P01'))
    assert {r['stock_code'] for r in body['items']} <= {r[0] for r in owned}
    detail=client.get('/states/001570',headers=auth_headers).json()
    assert 'points' not in str(detail) and 'sort_value' not in str(detail) and 'composite_ratio' not in str(detail)
    other=client.post('/auth/login',json={'email':'other@demo.local','password':'test'}).json()['access_token']
    headers={'Authorization':f'Bearer {other}'}
    assert client.get('/states/001570',headers=headers).status_code==404
    assert client.get('/states',headers=headers).json()['total']==1
    assert client.get('/customers',headers=headers).json()['customers'][0]['customer_id']=='OTHER-C'
    assert client.post('/runs',headers=headers,json={'rcept_no':'20250321802001'}).status_code==503


def test_ack_is_pb_and_version_specific(client,auth_headers,p02_headers,states_world):
    assert client.post('/states/001570/ack',headers=auth_headers,json={'version':1}).json()['new'] is False
    assert client.get('/states/001570',headers=p02_headers).json()['new'] is True
    assert client.post('/states/001570/ack',headers=auth_headers,json={'version':2}).status_code==409


def test_old_ack_cannot_clear_new_alert(client,auth_headers,session_factory,states_world):
    with session_factory() as s:
        s.get(RiskState,'TEST-CORP').alert_version=2
    assert client.post('/states/001570/ack',headers=auth_headers,json={'version':1}).json()['new'] is True


def test_collect_permissions_and_unwired_status(client,auth_headers,session_factory):
    assert client.post('/collect/run',headers=auth_headers).status_code==403
    with session_factory() as s:
        s.get(PB,'P01').role='ADMIN'
    response=client.post('/collect/run',headers=auth_headers)
    assert response.status_code==503 and response.json()['ok'] is False


def test_pagination_and_invalid_filter(client,auth_headers,states_world):
    assert client.get('/states?limit=1',headers=auth_headers).json()['limit']==1
    assert client.get('/states?stage=Critical',headers=auth_headers).status_code==422


def test_confirmed_resolution_and_time_decay_are_distinct(session_factory,states_world):
    with session_factory() as s:
        result={'stage':'NONE','breakdown':[]}
        row=update_state(s,'TEST-CORP',result,'2025-03-22')
        assert not row.resolved
        evidence=[{'source':'s','quote':'q','rcept_no':'20250321802001','location':'l'}]
        row=update_state(s,'TEST-CORP',result,'2025-03-22',resolution_evidence=evidence)
        assert row.resolved and row.resolution_evidence==evidence
        from risk_engine.states import StaleStateError
        with pytest.raises(StaleStateError):
            update_state(s,'TEST-CORP',{'stage':'RED','breakdown':[]},'2025-03-20')
        assert row.stage=='NONE'  # late historical save cannot overwrite current state


def test_additional_red_event_notifies_even_with_same_type(session_factory,states_world):
    with session_factory() as s:
        result=calculate(new_signals=[{'signal_type':'DEFAULT','event_id':'TEST-CORP','detail':{},'filed_at':'2025-03-21'},
                                     {'signal_type':'DEFAULT','event_id':'separate-event','detail':{},'filed_at':'2025-03-21'}],as_of='2025-03-21')
        row=update_state(s,'TEST-CORP',result,'2025-03-21')
        assert row.alert_version==2
        update_state(s,'TEST-CORP',result,'2025-03-21')
        assert row.alert_version==2
