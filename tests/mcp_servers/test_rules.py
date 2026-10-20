"""risk_rules / impact_rules 순수 계산 테스트 (DB 없음)."""
import pytest

from mcp_servers.business.rules import impact_rules, risk_rules
from mcp_servers.business.rules.loader import load_rules

AS_OF = "2025-03-01"


def calc(new, history=(), base=0, as_of=AS_OF, rules=None):
    return risk_rules.calculate(
        base_score=base, base_reasons=[], history=list(history), new_signals=new, as_of=as_of, rules=rules
    )


def sig(t, detail=None, filed=AS_OF, rcept=None):
    return {"signal_type": t, "detail": detail or {}, "filed_at": filed, "rcept_no": rcept}


def hist(t, filed, points, detail=None, rcept="H"):
    return {"signal_type": t, "filed_at": filed, "points": points, "detail": detail or {}, "rcept_no": rcept}


def test_rights_offering_cgv_alone_is_low():
    r = calc([sig("RIGHTS_OFFERING", {"ratio_pct": 156.5})])
    assert r["total_score"] == 3 and r["level"] == "Low"  # 기본 2 + 50% 이상 1
    assert r["breakdown"][0]["extras"] == ["50% 이상 또는 제3자배정"]


def test_rights_offering_under_20pct_is_zero():
    assert calc([sig("RIGHTS_OFFERING", {"ratio_pct": 10})])["total_score"] == 0


def test_rights_offering_ratio_missing_needs_manual_check():
    r = calc([sig("RIGHTS_OFFERING", {})])
    assert r["total_score"] == 0 and "수동 확인" in r["breakdown"][0]["note"]


def test_cb_bw_repeat_and_refixing():
    h = [hist("CB_BW", "2024-12-01", 2)]
    r = calc([sig("CB_BW", {"refixing": True})], history=h)
    new = r["breakdown"][0]
    assert new["raw_points"] == 4 and new["extras"] == ["리픽싱 조항", "12개월 내 2회 이상"]
    assert r["total_score"] == 4 + 2  # 과거 신호는 6개월 이내라 100%


def test_time_weights():
    r = calc([], history=[hist("FILING_DELAY", "2024-07-01", 4)])  # 8개월 전
    assert r["total_score"] == 2.0
    r = calc([], history=[hist("FILING_DELAY", "2024-02-01", 4)])  # 13개월 전
    assert r["total_score"] == 0 and r["breakdown"] == []
    r = calc([], history=[hist("FILING_DELAY", "2024-09-01", 4)])  # 정확히 6개월 전 → 100%
    assert r["total_score"] == 4.0


def test_base_score_added():
    assert calc([], base=2)["total_score"] == 2


def test_levels_boundaries():
    assert calc([], base=3)["level"] == "Low"
    assert calc([], base=4)["level"] == "Medium"
    assert calc([], base=6)["level"] == "Medium"
    assert calc([], base=7)["level"] == "High"
    assert calc([], base=10)["level"] == "Critical"


@pytest.mark.parametrize(
    "opinion,level", [("의견거절", "Critical"), ("부적정", "Critical"), ("한정", "High")]
)
def test_audit_opinion_fixes_level_regardless_of_score(opinion, level):
    r = calc([sig("AUDIT_OPINION", {"opinion": opinion, "quote": "감사의견 의견거절"})])
    assert r["total_score"] == 0 and r["level"] == level
    assert r["breakdown"][0]["quote"] == "감사의견 의견거절"


def test_audit_opinion_unqualified_has_no_effect():
    assert calc([sig("AUDIT_OPINION", {"opinion": "적정"})])["level"] == "Low"


def test_embezzlement_threshold_by_market():
    d = {"ratio_to_equity_pct": 3.5, "market": "KOSDAQ"}
    assert calc([sig("EMBEZZLEMENT", d)])["level"] == "Critical"
    assert calc([sig("EMBEZZLEMENT", {**d, "market": "KOSPI"})])["level"] == "High"
    r = calc([sig("EMBEZZLEMENT", {"market": "KOSDAQ"})])  # 비율 미확인 → High + 수동 확인
    assert r["level"] == "High" and "수동 확인" in r["breakdown"][0]["note"]


def test_capital_impairment():
    assert calc([sig("CAPITAL_IMPAIRMENT", {"impairment_pct": 100})])["level"] == "Critical"
    assert calc([sig("CAPITAL_IMPAIRMENT", {"impairment_pct": 60})])["level"] == "High"
    assert calc([sig("CAPITAL_IMPAIRMENT", {"impairment_pct": 20})])["level"] == "Low"


def test_trading_halt_lifted_is_ignored():
    assert calc([sig("TRADING_HALT", {"lifted": True})])["level"] == "Low"
    assert calc([sig("TRADING_HALT", {})])["level"] == "Critical"


def test_combo_upgrades_one_level():
    h = [hist("FILING_DELAY", "2024-12-01", 4, rcept="H1"), hist("PLEDGE", "2025-01-10", 2, rcept="H2")]
    r = calc([sig("CB_BW")], history=h)  # 4+2+2 = 8 → High → combo → Critical
    assert r["total_score"] == 8 and r["level"] == "Critical" and r["combo_upgraded"] is True


def test_combo_off_in_rules_copy():
    rules = load_rules()
    off = {**rules, "combo": {**rules["combo"], "enabled": False}}
    h = [hist("FILING_DELAY", "2024-12-01", 4, rcept="H1"), hist("PLEDGE", "2025-01-10", 2, rcept="H2")]
    r = calc([sig("CB_BW")], history=h, rules=off)
    assert r["level"] == "High" and r["combo_upgraded"] is False


def test_combo_ignores_zero_point_signals():
    h = [hist("FILING_DELAY", "2024-12-01", 4, rcept="H1"), hist("PLEDGE", "2025-01-10", 0, rcept="H2")]
    r = calc([sig("AUDITOR_CHANGE", {"periodic_designation": True})], history=h)
    assert r["combo_upgraded"] is False and r["distinct_signal_types"] == ["FILING_DELAY"]


def test_combo_caps_at_critical():
    r = calc(
        [sig("AUDIT_OPINION", {"opinion": "거절"}), sig("FILING_DELAY"), sig("PLEDGE"), sig("CB_BW")]
    )
    assert r["level"] == "Critical" and r["combo_upgraded"] is False


def test_rerun_same_rcept_does_not_double_count():
    h = [hist("FILING_DELAY", AS_OF, 4, rcept="X1")]
    r = calc([sig("FILING_DELAY", rcept="X1")], history=h)
    assert r["total_score"] == 4


def test_unknown_signal_type_rejected():
    with pytest.raises(risk_rules.RuleInputError):
        calc([sig("MADE_UP")])


def test_bad_date_rejected():
    with pytest.raises(risk_rules.RuleInputError):
        calc([], as_of="내일")


def test_ai_supplied_points_are_ignored():
    r = calc([{"signal_type": "FILING_DELAY", "points": 99, "detail": {}, "filed_at": AS_OF}])
    assert r["total_score"] == 4


# ---------------------------------------------------------------- impact
def test_impact_examples_from_scenario_a():
    c001 = impact_rules.customer_impact(
        eval_amount=90_000_000, total_assets=300_000_000, risk_profile="안정형", on_credit=False, company_level="Critical"
    )
    assert (c001["impact_score"], c001["impact_level"], c001["priority"]) == (8, "높음", "P1")
    c002 = impact_rules.customer_impact(
        eval_amount=10_000_000, total_assets=500_000_000, risk_profile="공격투자형", on_credit=False, company_level="Critical"
    )
    assert (c002["impact_level"], c002["priority"]) == ("낮음", "P2")


def test_credit_forces_high_and_high_company_level_table():
    r = impact_rules.customer_impact(
        eval_amount=1_000_000, total_assets=900_000_000, risk_profile="공격투자형", on_credit=True, company_level="High"
    )
    assert r["impact_level"] == "높음" and r["priority"] == "P1"
    low = impact_rules.customer_impact(
        eval_amount=1_000_000, total_assets=900_000_000, risk_profile="공격투자형", on_credit=False, company_level="High"
    )
    assert low["priority"] == "P3"


def test_impact_rejects_low_levels():
    with pytest.raises(impact_rules.ImpactInputError):
        impact_rules.customer_impact(
            eval_amount=1, total_assets=1, risk_profile="안정형", on_credit=False, company_level="Medium"
        )


# ---------------------------------------------------------------- A(Agent)와 맞춘 계약
EV = {"source": "금양_감사보고서제출", "quote": "감사의견 의견거절", "rcept_no": "20250321802001", "location": "chunk-0"}


def test_breakdown_carries_evidence_for_agent():
    r = calc([{**sig("AUDIT_OPINION", {"opinion": "의견거절"}), "evidence": [EV, {"quote": "모양이 틀린 근거"}]}])
    item = r["breakdown"][0]
    assert item["evidence"] == [EV]  # 모양이 틀린 근거는 버린다
    assert r["signals"][0]["evidence"] == [EV] and r["signals"][0]["detail"]["evidence"] == [EV]


def test_history_evidence_gets_verifiable_source_chunks():
    h = [hist("FILING_DELAY", "2025-01-10", 4, detail={"evidence": [EV]})]
    item = calc([], history=h)["breakdown"][0]
    assert item["evidence"] == [EV]
    assert item["source_chunks"] == [{**EV, "text": EV["quote"]}]


def test_base_score_item_evidence_only_when_given():
    no_ev = calc([], base=2)["breakdown"][0]
    assert no_ev["evidence"] == [] and "근거" in no_ev["note"]
    reasons = [{"reason": "영업이익 3년 연속 감소", "evidence": EV}]
    with_ev = risk_rules.calculate(
        base_score=2, base_reasons=reasons, history=[], new_signals=[], as_of=AS_OF
    )["breakdown"][0]
    assert with_ev["evidence"] == [EV] and with_ev["source_chunks"][0]["text"] == EV["quote"]


def test_agent_classifier_reads_rules_yaml():
    from agent.rules import load_classifier
    from mcp_servers.business.rules.loader import DEFAULT_PATH

    classify = load_classifier(DEFAULT_PATH)
    assert classify("감사보고서제출") == "AUDIT_REPORT"
    assert classify("횡령ㆍ배임혐의발생") == "EMBEZZLEMENT"
    assert classify("유상증자결정") == "RIGHTS_OFFERING"
    assert classify("[기재정정]전환사채권발행결정") == "CB_BW"
    assert classify("주주총회소집공고") is None
