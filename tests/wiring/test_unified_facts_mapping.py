"""Actual R3 API fields -> R4 mapper/calculator. Flags are protocol fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from collector.facts import build_payload
from agent.adapters.unified import approved_routes, prepare_signals
from agent.ports import AgentError
from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from shared.facts import store_facts
from shared.models import Disclosure
import shared.db as db_module


def inputs():
    case = next(c for c in json.loads((Path(__file__).resolve().parents[2] /
        'eval/cases/r3_cases.json').read_text())['cases'] if c['case_id'] == 'r3-orange-001')
    filing = case['evidence'][0]
    disclosure = {'rcept_no': filing['rcept_no'], 'title': filing['title'],
                  'corp_code': case['corp_code'], 'filed_at': filing['filed_at']}
    facts = build_payload('piicDecsn', case['facts'], rcept_no=filing['rcept_no'],
                          corp_code=case['corp_code'], fetched_at='2026-10-23T09:00:00+09:00')
    routes = approved_routes(disclosure['title'])
    return {'disclosure': disclosure, 'structured_facts': facts, 'routes': routes,
            'flags': [{'item': 'RIGHTS_OFFERING', 'flag': 'payment_delayed_or_failed', 'value': False, 'evidence': []}],
            'evidence': [], 'as_of': filing['filed_at']}


def test_r3_number_preserved_in_calculation_and_api_evidence_is_not_a_quote(seeded_db):
    data = inputs()
    with db_module.get_session() as db:
        receipt = data['disclosure']['rcept_no']
        if db.get(Disclosure, receipt) is None:
            db.add(Disclosure(**data['disclosure'])); db.flush()
        store_facts(db, receipt, data['structured_facts'])
    signals = prepare_signals(data)
    assert signals[0]['detail']['ratio_pct'] == 10.1932
    result = calculate_risk_score(data['disclosure']['corp_code'], signals, data['as_of'])
    assert result['ok'], result
    evidence = result['data']['breakdown'][0]['evidence']
    assert all(e['kind'] == 'api' and 'quote' not in e for e in evidence)
    forged = deepcopy(signals)
    forged[0]['evidence'][0]['value'] += 1
    assert not calculate_risk_score(data['disclosure']['corp_code'], forged, data['as_of'])['ok']


@pytest.mark.parametrize('problem', ['missing', 'unit', 'duplicate'])
def test_unconfirmed_ratio_never_becomes_zero_or_chooses_a_row(problem):
    data = inputs()
    fact = next(f for f in data['structured_facts']['facts'] if f['name'] == 'issue_ratio_common_pct')
    if problem == 'missing':
        fact.update(status='MISSING', value=None)
    elif problem == 'unit':
        fact['unit'] = '원'
    else:
        data['structured_facts']['facts'].append(deepcopy(fact))
    with pytest.raises(AgentError):
        prepare_signals(data)
