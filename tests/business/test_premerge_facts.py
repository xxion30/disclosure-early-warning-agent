from copy import deepcopy
import pytest
from pydantic import ValidationError
import shared.db as db_module
from shared.models import Disclosure
from shared.facts import StructuredFacts, store_facts
from mcp_servers.business.tools.get_disclosure_facts import get_disclosure_facts

RCEPT = '20250321802001'


def payload(corp):
    return {'schema_version':'1','rcept_no':RCEPT,'corp_code':corp,'collected_at':'2026-10-23T09:05:00+09:00',
            'facts':[{'name':'fixture_value','dtype':'integer','raw':'0','value':0,'unit':None,
                      'status':'PRESENT','reason':None,'source':{'api':'fixture','field':'value','rcept_no':RCEPT,
                                                             'fetched_at':'2026-10-23T09:05:00+09:00'}}]}


def test_null_is_unavailable_and_zero_false_are_not_missing(seeded_db):
    with db_module.get_session() as db:
        disc=db.get(Disclosure,RCEPT);corp=disc.corp_code
    assert get_disclosure_facts(RCEPT)['data']['availability']=='UNAVAILABLE'
    value=payload(corp)
    with db_module.get_session() as db: digest=store_facts(db,RCEPT,value)
    result=get_disclosure_facts(RCEPT)['data']
    assert result['facts_hash']==digest and result['structured_facts']['facts'][0]['value']==0
    boolean=deepcopy(value);boolean['facts'][0].update(dtype='boolean',value=False)
    assert StructuredFacts.model_validate(boolean).facts[0].value is False


def test_invalid_fact_and_identity_are_rejected(seeded_db):
    value=payload('fixture')
    value['facts'][0]['value']=False
    with pytest.raises(ValidationError):StructuredFacts.model_validate(value)
    value['facts'][0].update(status='MISSING',value=0,reason='missing')
    with pytest.raises(ValidationError):StructuredFacts.model_validate(value)
    with db_module.get_session() as db:
        with pytest.raises(ValueError):store_facts(db,RCEPT,payload('wrong-company'))
    assert get_disclosure_facts('not-a-receipt')['error']['code']=='INVALID_INPUT'
    assert get_disclosure_facts('00000000000000')['error']['code']=='NOT_FOUND'


@pytest.mark.parametrize('cfs,ofs,expected_basis', [(0,100,'OFS'),(100,0,'CFS'),(100,100,'OFS')])
def test_model_cannot_override_dual_basis_capital_policy(seeded_db,cfs,ofs,expected_basis):
    from shared.models import FinancialSnapshot
    from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
    with db_module.get_session() as db:
        corp=db.get(Disclosure,RCEPT).corp_code
        for basis,value in [('CONSOLIDATED',cfs),('SEPARATE',ofs)]:
            db.add(FinancialSnapshot(corp_code=corp,rcept_no=RCEPT,period='2024',period_kind='ANNUAL',basis=basis,
                indicators={'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':value}}},sources=[],revision=1,created_at='2025-03-21'))
    result=calculate_risk_score(corp,[{'signal_type':'CAPITAL_IMPAIRMENT','rcept_no':RCEPT,'detail':{'impairment_pct':0}}],'2025-03-21')
    assert result['ok'] and result['data']['yellow_score']==4
    row = next(v for v in result['data']['breakdown'] if v['signal_type'] == 'CAPITAL_IMPAIRMENT')
    assert row['fs_div'] == expected_basis and len(row['basis_checks']) == 2


def test_unconfirmed_original_candidate_does_not_merge_events(seeded_db):
    from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
    from shared.events import confirm_event_link
    correction='20250117000380'
    with db_module.get_session() as db:
        original=db.get(Disclosure,RCEPT)
        # Internal relationship fixture, not a claim about the real correction's contents.
        row=db.get(Disclosure,correction)
        row.corp_code=original.corp_code;row.title='[기재정정] 계약 fixture';row.filed_at='2025-03-22'
        row.original_rcept_no=RCEPT;row.event_id=None
        corp=original.corp_code
    signal={'signal_type':'CB_BW','rcept_no':correction,'detail':{'refixing':False,'early_redemption':False}}
    before=calculate_risk_score(corp,[signal],'2025-03-22')
    assert before['ok'] and before['data']['manual_check']
    with db_module.get_session() as db:
        confirm_event_link(db,correction,RCEPT,[{'source':'contract','quote':'relationship fixture','location':'contract','rcept_no':correction}])
    after=calculate_risk_score(corp,[signal],'2025-03-22')
    assert after['ok'] and not after['data']['manual_check']


@pytest.mark.parametrize('status,detail,expected,manual', [
    ('OK', {'interest_coverage':0.5,'operating_income':5,'finance_costs':10}, 2, False),
    ('OK', {'interest_coverage':1,'operating_income':10,'finance_costs':10}, 0, False),
    ('MISSING', {}, 0, True),
])
def test_finance_cost_uses_stored_ratio_not_model_override(seeded_db,status,detail,expected,manual):
    from shared.models import FinancialSnapshot
    from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
    with db_module.get_session() as db:
        corp = db.get(Disclosure,RCEPT).corp_code
        db.add(FinancialSnapshot(corp_code=corp,rcept_no=RCEPT,period='2024',period_kind='ANNUAL',
            basis='CONSOLIDATED',indicators={'INTEREST_BURDEN':{'status':status,'detail':detail}},
            sources=[],revision=1,created_at='2025-03-21'))
    result = calculate_risk_score(corp,[{'signal_type':'INTEREST_BURDEN','rcept_no':RCEPT,
                                       'detail':{'interest_coverage':100}}],'2025-03-21')
    assert result['ok']
    assert result['data']['yellow_score'] == expected
    assert result['data']['manual_check'] is manual
