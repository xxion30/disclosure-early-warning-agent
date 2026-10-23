"""Policy/route fixtures only: no assertion of real unpaid debt or live DART data."""
import pytest
import json

from agent.adapters.phase1 import make_phase1_classifier
from shared.rule_catalog import load_catalog
from shared.config import ROOT_DIR
from risk_engine.stages import calculate


def signal(detail=None, evidence=None):
    return {'signal_type': 'PAYMENT_DEFAULT', 'rcept_no': '20250321802001',
            'event_id': 'payment-default-contract', 'filed_at': '2025-03-21',
            'detail': detail or {}, 'evidence': evidence or []}


def test_payment_default_classifies_and_is_allowed_as_red():
    classify = make_phase1_classifier()
    assert classify('원리금 미지급 발생') == 'PAYMENT_DEFAULT'
    assert load_catalog()['allowed']['PAYMENT_DEFAULT'] == ['PAYMENT_DEFAULT']
    result = calculate(new_signals=[signal()], as_of='2025-03-21')
    assert result['stage'] == 'RED'
    assert result['breakdown'][0]['active']


def test_actual_repository_payment_and_arrears_titles_route_to_red():
    rows = json.loads((ROOT_DIR/'data/disclosures/071460.json').read_text(encoding='utf-8'))
    selected = [row for row in rows if row['rcept_no'] in ('20231018900507','20231023900517')]
    assert len(selected) == 2
    for row in selected:
        st = make_phase1_classifier()(row['title'])
        assert st == 'PAYMENT_DEFAULT'
        result = calculate(new_signals=[{'signal_type': st, 'rcept_no': row['rcept_no'],
            'filed_at': row['filed_at'], 'detail': {'title': row['title']}}], as_of=row['filed_at'])
        assert result['stage'] == 'RED'


@pytest.mark.parametrize('title', ['회사채 미상환 잔액 현황', '차입금 만기 도래 안내'])
def test_outstanding_balance_and_maturity_are_not_payment_default(title):
    assert make_phase1_classifier()(title) != 'PAYMENT_DEFAULT'


def test_payment_default_resolution_requires_evidence():
    result = calculate(new_signals=[signal({'resolved': True})], as_of='2025-03-21')
    assert result['stage'] == 'RED' and result['manual_check']
    evidence = [{'source':'contract-fixture', 'location':'contract-fixture',
                 'rcept_no':'20250321802001', 'quote':'프로토콜 검증용 해소 근거'}]
    result = calculate(new_signals=[signal({'resolved': True}, evidence)], as_of='2025-03-21')
    assert result['stage'] == 'NONE' and not result['manual_check']
