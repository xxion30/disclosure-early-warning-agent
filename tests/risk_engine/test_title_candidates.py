"""R3's verified filing titles, with R1 policy taking precedence over old labels."""
import json
from pathlib import Path
import pytest
from agent.adapters.phase1 import make_phase1_classifier
from risk_engine.stages import calculate

CASES = {row['case_id']: row for row in json.loads(
    (Path(__file__).resolve().parents[2] / 'eval/cases/r3_cases.json').read_text())['cases']}


@pytest.mark.parametrize('case_id,stage,manual', [
    ('r3-red-008', 'RED', False), ('r3-red-010', 'RED', False),
    ('r3-red-012', 'WATCH', True), ('r3-red-013', 'WATCH', True),
    ('r3-red-014', 'RED', False), ('r3-red-015', 'RED', False),
    ('r3-nonred-001', 'NONE', False), ('r3-nonred-002', 'NONE', False),
    ('r3-nonred-003', 'NONE', False), ('r3-nonred-004', 'WATCH', False),
    ('r3-nonred-005', 'WATCH', False), ('r3-nonred-006', 'NONE', False),
    ('r3-nonred-007', 'NONE', False), ('r3-nonred-010', 'NONE', False),
    ('r3-nonred-013', 'RED', False), ('r3-nonred-014', 'NONE', False),
])
def test_title_is_checked_at_calculation_boundary(case_id, stage, manual):
    row = CASES[case_id]
    filing = row['evidence'][0]
    signal = {'signal_type': filing['item'], 'rcept_no': filing['rcept_no'],
              'filed_at': filing['filed_at'], 'detail': {'title': filing['title']}}
    result = calculate(new_signals=[signal], as_of=row['as_of'])
    assert result['stage'] == stage
    assert result['manual_check'] is manual
    if stage == 'NONE' and not manual:
        assert make_phase1_classifier()(filing['title']) is None
    if case_id in ('r3-red-014', 'r3-red-015'):
        assert make_phase1_classifier()(filing['title']) == 'ADMIN_ISSUE'


def test_confirmed_correction_updates_event_without_resetting_origin():
    original = CASES['r3-red-008']['evidence'][0]
    correction = CASES['r3-nonred-008']['evidence'][0]
    history = [{'signal_type': 'EMBEZZLEMENT', 'event_id': original['rcept_no'],
                'rcept_no': original['rcept_no'], 'filed_at': original['filed_at'],
                'detail': {'title': original['title']}}]
    incoming = [{'signal_type': 'EMBEZZLEMENT', 'event_id': original['rcept_no'],
                 'rcept_no': correction['rcept_no'], 'filed_at': correction['filed_at'],
                 'detail': {'title': correction['title'], 'is_correction': True}}]
    result = calculate(history=history, new_signals=incoming, as_of=correction['filed_at'])
    assert result['stage'] == 'RED' and len(result['breakdown']) == 1
    assert result['signals'][0]['event_started_at'] == original['filed_at']
