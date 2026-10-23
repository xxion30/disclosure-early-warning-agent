"""Financial selection contract fixtures, not additional real-company disclosures."""
from types import SimpleNamespace
import pytest
from risk_engine.financials import select_snapshot_signals
from risk_engine.stages import calculate


def snapshot(basis,kind,period,indicators,revision=1,rcept='internal-report'):
    return SimpleNamespace(basis=basis,period_kind=kind,period=period,indicators=indicators,revision=revision,
                           rcept_no=rcept,corp_code='contract-fixture',sources=[])


def test_consolidated_precedes_later_separate_for_same_period():
    consolidated=snapshot('CONSOLIDATED','ANNUAL','2024',{'AUDIT_OPINION':{'status':'OK','detail':{'opinion':'거절'}}})
    separate=snapshot('SEPARATE','ANNUAL','2024',{'AUDIT_OPINION':{'status':'OK','detail':{'opinion':'적정'}}})
    for snapshots in ([consolidated,separate],[separate,consolidated]):
        assert select_snapshot_signals(snapshots,{'internal-report':'2025-03-21'})[0]['detail']['opinion']=='거절'


def test_quarter_does_not_remove_annual_cash_flow_indicator():
    annual=snapshot('CONSOLIDATED','ANNUAL','2024',{'OPERATING_CASH_FLOW':{'status':'OK','detail':{'negative_years':2}}})
    quarter=snapshot('SEPARATE','YTD','2025-Q1',{'OPERATING_CASH_FLOW':{'status':'OK','detail':{'negative_years':1}},
                                                       'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':50}}},rcept='internal-quarter')
    signals=select_snapshot_signals([annual,quarter],{'internal-report':'2025-03-21','internal-quarter':'2025-05-01'})
    details = {s['signal_type']: s['detail'] for s in signals}
    assert details['OPERATING_CASH_FLOW'] == {'negative_years': 2}
    assert details['CAPITAL_IMPAIRMENT']['impairment_pct'] == 50


def test_latest_correction_and_missing_values_are_not_replaced_with_old_good_data():
    old=snapshot('CONSOLIDATED','ANNUAL','2024',{'AUDIT_OPINION':{'status':'OK','detail':{'opinion':'적정'}}})
    correction=snapshot('CONSOLIDATED','ANNUAL','2024',{'AUDIT_OPINION':{'status':'MISSING'}},revision=2)
    assert select_snapshot_signals([old,correction],{'internal-report':'2025-03-21'})[0]['detail']=={}


def test_full_year_is_newer_than_earlier_quarter_of_same_year():
    annual=snapshot('SEPARATE','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}})
    third_quarter=snapshot('SEPARATE','YTD','2024-Q3',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}}},rcept='internal-quarter')
    result=select_snapshot_signals([annual,third_quarter],{'internal-report':'2025-03-21','internal-quarter':'2024-11-01'})
    assert result[0]['detail']['impairment_pct']==0


@pytest.mark.parametrize('ofs_value,cfs_value,expected,basis,points', [
    (100, 0, 100, 'OFS', 4), (0, 100, 100, 'CFS', 4),
    (50, 100, 100, 'CFS', 4), (100, 50, 100, 'OFS', 4),
    (49.9, 0, 49.9, 'OFS', 0), (0, 50, 50, 'CFS', 3),
])
def test_capital_uses_highest_basis_once(ofs_value,cfs_value,expected,basis,points):
    ofs=snapshot('SEPARATE','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':ofs_value}}})
    cfs=snapshot('CONSOLIDATED','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':cfs_value}}})
    for snapshots in ([ofs,cfs], [cfs,ofs]):
        signals=select_snapshot_signals(snapshots,{'internal-report':'2025-03-21'})
        assert len(signals) == 1
        assert signals[0]['detail']['impairment_pct'] == expected
        result = calculate(new_signals=signals, as_of='2025-03-21')
        assert result['yellow_score'] == points
        assert result['breakdown'][0]['fs_div'] == basis
        assert {v['fs_div']: v['impairment_pct'] for v in result['breakdown'][0]['basis_checks']} == {'OFS': ofs_value, 'CFS': cfs_value}


def test_capital_does_not_fall_back_to_old_period():
    ofs=snapshot('SEPARATE','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}}})
    cfs=snapshot('CONSOLIDATED','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}})
    result=select_snapshot_signals([ofs,cfs],{'internal-report':'2025-03-21'})
    assert result[0]['detail']['impairment_pct']==100
    newer=snapshot('CONSOLIDATED','YTD','2025-Q1',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}},rcept='quarter')
    result=select_snapshot_signals([ofs,cfs,newer],{'internal-report':'2025-03-21','quarter':'2025-05-01'})
    assert result[0]['detail']['impairment_pct'] == 0
    assert result[0]['detail']['fs_div'] == 'CFS'
    assert result[0]['detail']['basis_checks'][0]['status'] == 'MISSING'


@pytest.mark.parametrize('value,expected', [(0.9999,2),(1,0),(1.0001,0),(0,2),(-1,2)])
def test_finance_cost_ratio_threshold_is_strictly_less_than_one(value,expected):
    result=calculate(new_signals=[{'signal_type':'INTEREST_BURDEN','rcept_no':'internal-contract',
                                  'detail':{'interest_coverage':value},'filed_at':'2025-03-21'}],as_of='2025-03-21')
    assert result['yellow_score']==expected and not result['manual_check']
    assert not result['breakdown'][0]['policy_pending']


def test_capital_latest_revision_is_selected_before_maximum():
    old=snapshot('CONSOLIDATED','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}}})
    corrected=snapshot('CONSOLIDATED','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}},revision=2)
    ofs=snapshot('SEPARATE','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':50}}})
    result = calculate(new_signals=select_snapshot_signals([old,corrected,ofs],{'internal-report':'2025-03-21'}),as_of='2025-03-21')
    assert result['yellow_score'] == 3 and result['breakdown'][0]['fs_div'] == 'OFS'


def test_capital_missing_basis_is_not_zero_or_old_value():
    rows=[snapshot(basis,'ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'MISSING'}})
          for basis in ('SEPARATE','CONSOLIDATED')]
    result = calculate(new_signals=select_snapshot_signals(rows,{'internal-report':'2025-03-21'}),as_of='2025-03-21')
    assert result['manual_check'] and result['yellow_score'] == 0
    assert all(v['impairment_pct'] is None for v in result['breakdown'][0]['basis_checks'])
    rows[1].indicators['CAPITAL_IMPAIRMENT'] = {'status':'OK','detail':{'impairment_pct':100}}
    result = calculate(new_signals=select_snapshot_signals(rows,{'internal-report':'2025-03-21'}),as_of='2025-03-21')
    assert result['yellow_score'] == 4 and result['breakdown'][0]['fs_div'] == 'CFS'


def test_missing_finance_cost_ratio_is_manual_check():
    result = calculate(new_signals=[{'signal_type':'INTEREST_BURDEN','rcept_no':'internal-contract',
                                    'detail':{},'filed_at':'2025-03-21'}],as_of='2025-03-21')
    assert result['manual_check'] and result['yellow_score'] == 0


def test_capital_and_finance_cost_combine_without_double_counting_basis():
    rows=[snapshot(basis,'ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}},
          'INTEREST_BURDEN':{'status':'OK','detail':{'interest_coverage':0.5}}})
          for basis in ('SEPARATE','CONSOLIDATED')]
    result=calculate(new_signals=select_snapshot_signals(rows,{'internal-report':'2025-03-21'}),as_of='2025-03-21')
    assert result['yellow_score'] == 6 and result['stage'] == 'YELLOW'
    assert len(result['breakdown']) == 2
