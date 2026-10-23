"""Financial selection contract fixtures, not additional real-company disclosures."""
from types import SimpleNamespace
from risk_engine.financials import select_snapshot_signals


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
    assert {s['signal_type']:s['detail'] for s in signals}=={'OPERATING_CASH_FLOW':{'negative_years':2},'CAPITAL_IMPAIRMENT':{'impairment_pct':50}}


def test_latest_correction_and_missing_values_are_not_replaced_with_old_good_data():
    old=snapshot('CONSOLIDATED','ANNUAL','2024',{'AUDIT_OPINION':{'status':'OK','detail':{'opinion':'적정'}}})
    correction=snapshot('CONSOLIDATED','ANNUAL','2024',{'AUDIT_OPINION':{'status':'MISSING'}},revision=2)
    assert select_snapshot_signals([old,correction],{'internal-report':'2025-03-21'})[0]['detail']=={}


def test_full_year_is_newer_than_earlier_quarter_of_same_year():
    annual=snapshot('SEPARATE','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}})
    third_quarter=snapshot('SEPARATE','YTD','2024-Q3',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}}},rcept='internal-quarter')
    result=select_snapshot_signals([annual,third_quarter],{'internal-report':'2025-03-21','internal-quarter':'2024-11-01'})
    assert result[0]['detail']['impairment_pct']==0


def test_capital_uses_ofs_and_does_not_fall_back_to_old_period():
    ofs=snapshot('SEPARATE','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':100}}})
    cfs=snapshot('CONSOLIDATED','ANNUAL','2024',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}})
    result=select_snapshot_signals([ofs,cfs],{'internal-report':'2025-03-21'})
    assert result[0]['detail']=={'impairment_pct':100}
    newer=snapshot('CONSOLIDATED','YTD','2025-Q1',{'CAPITAL_IMPAIRMENT':{'status':'OK','detail':{'impairment_pct':0}}},rcept='quarter')
    result=select_snapshot_signals([ofs,cfs,newer],{'internal-report':'2025-03-21','quarter':'2025-05-01'})
    assert result[0]['detail']=={} and result[0]['selection_reason']=='latest_period_ofs_missing'


def test_finance_cost_policy_is_not_scored_until_threshold_approved():
    from risk_engine.stages import calculate
    result=calculate(new_signals=[{'signal_type':'INTEREST_BURDEN','rcept_no':'internal-contract',
                                  'detail':{'interest_coverage':0.1},'filed_at':'2025-03-21'}],as_of='2025-03-21')
    assert result['yellow_score']==0 and not result['manual_check']
    assert result['breakdown'][0]['policy_pending'] and not result['breakdown'][0]['active']
