"""Arithmetic/contract fixtures, not claims about any issuer or fabricated DART examples."""
from copy import deepcopy
import pytest
from risk_engine.loader import load_rules
from risk_engine.stages import calculate, judge
from risk_engine.risk_rules import RuleInputError

RCEPT = '20250321802001'  # Existing repository source; event IDs below are internal test keys.
AS_OF = '2025-03-21'


def sig(st, detail=None, event='event-a', filed=AS_OF):
    return {'signal_type': st, 'detail': detail or {}, 'event_id': event, 'rcept_no': RCEPT, 'filed_at': filed}


@pytest.mark.parametrize('orange,yellow,expected', [(0,0,'NONE'),(3,0,'WATCH'),(0,3.75,'WATCH'),(4,0,'ORANGE'),
                                                   (0,5,'YELLOW'),(3,4,'COMPOSITE')])
def test_judgment_boundaries(orange, yellow, expected):
    assert judge(orange,yellow)[0] == expected


@pytest.mark.parametrize('st', list(load_rules()['red']))
def test_each_red_has_no_amount_threshold(st):
    assert calculate(new_signals=[sig(st)], as_of=AS_OF)['stage'] == 'RED'


def test_both_groups_are_preserved_and_red_takes_precedence():
    items = [sig('RIGHTS_OFFERING', {'ratio_pct':50}), sig('AUDIT_OPINION', {'opinion':'거절'})]
    result = calculate(new_signals=items, as_of=AS_OF)
    assert result['active_stages'] == ['ORANGE','YELLOW']
    assert calculate(new_signals=[*items,sig('DEFAULT')], as_of=AS_OF)['stage'] == 'RED'


def test_same_event_replaces_instead_of_repeating_and_preserves_origin():
    old = sig('RIGHTS_OFFERING', {'ratio_pct':20}, filed='2024-08-01')
    new = sig('RIGHTS_OFFERING', {'ratio_pct':50,'is_correction':True})
    r = calculate(history=[old], new_signals=[new], as_of=AS_OF)
    assert len(r['breakdown']) == 1
    assert r['orange_score'] == 2  # raw 4, aged to 50%; correction does not reset the clock
    assert r['signals'][0]['event_started_at'] == '2024-08-01'


def test_separate_events_count_as_repeated_but_duplicate_delivery_does_not():
    one = sig('CB_BW', event='one')
    r = calculate(history=[one], new_signals=[one, sig('CB_BW', event='two')], as_of=AS_OF)
    assert len(r['breakdown']) == 2 and r['orange_score'] == 5


def test_unlinked_correction_is_not_an_independent_event():
    item = sig('CB_BW', {'is_correction':True}); item.pop('event_id')
    r = calculate(new_signals=[item], as_of=AS_OF)
    assert r['manual_check'] and r['orange_score'] == 0


def test_red_does_not_expire_while_orange_does():
    r=calculate(history=[sig('DEFAULT',filed='2022-01-01'),sig('CB_BW',filed='2022-01-01')],new_signals=[],as_of=AS_OF)
    assert r['stage']=='RED' and r['orange_score']==0


def test_yellow_uses_latest_report_only():
    r=calculate(history=[sig('AUDIT_OPINION',{'opinion':'거절'},event='old',filed='2024-03-01')],
                new_signals=[sig('AUDIT_OPINION',{'opinion':'적정','going_concern':False})],as_of=AS_OF)
    assert r['yellow_score']==0


def test_ai_points_and_unverified_flags_cannot_increase_score():
    item=sig('CB_BW',{'refixing':True}); item['points']=999
    r=calculate(new_signals=[item],as_of=AS_OF)
    assert r['orange_score']==2 and r['manual_check']


def test_resolved_red_needs_evidence():
    r=calculate(new_signals=[sig('TRADING_HALT',{'lifted':True})],as_of=AS_OF)
    assert r['stage']=='RED' and r['manual_check']


def test_future_input_rejected_and_yaml_threshold_change_is_effective():
    with pytest.raises(RuleInputError):
        calculate(new_signals=[sig('CB_BW',filed='2025-03-22')],as_of=AS_OF)
    rules=deepcopy(load_rules()); rules['thresholds']['orange']=2
    assert calculate(new_signals=[sig('CB_BW')],as_of=AS_OF,rules=rules)['stage']=='ORANGE'


def test_subsidiary_marker_exclusion_does_not_hide_parent_asset_sale():
    parent=sig('ASSET_TRANSFER',{'title':'자회사 주식 양도','ratio_to_equity_pct':30})
    subsidiary=sig('BUSINESS_SUSPENSION',{'title':'영업정지(종속회사의 주요경영사항)'})
    r=calculate(new_signals=[parent,subsidiary],as_of=AS_OF)
    assert r['orange_score']==3 and 'RED' not in r['active_stages']
