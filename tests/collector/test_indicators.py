"""indicators 테스트: 금액·계정은 실제 DART 재무제표(금양 2024, 위니아 2023, 오스템임플란트 2021, SK하이닉스 2024)에서 본 값이다.
'fixture'라고 적은 값만 경계 조건을 만들려고 직접 정했다."""
import pytest

from collector.indicators import (
    AUDIT_OPINION,
    CAPITAL_IMPAIRMENT,
    INTEREST_BURDEN,
    OPERATING_CASH_FLOW,
    SHORT_TERM_DEBT,
    capital_impairment,
    compute_audit_opinion,
    compute_financial_indicators,
    interest_burden,
    normalize_opinion,
    operating_cash_flow,
    parse_amount,
    short_term_debt,
)

NO_STD = "-표준계정코드 미사용-"


def row(sj, acc_id, nm, cur, prev=None):
    return {"sj_div": sj, "account_id": acc_id, "account_nm": nm, "thstrm_amount": cur, "frmtrm_amount": prev}


def test_parse_amount_never_turns_missing_into_zero():
    assert parse_amount("1,234") == 1234 and parse_amount("-20344818738") == -20344818738
    assert parse_amount("-") is None and parse_amount("") is None and parse_amount(None) is None and parse_amount("n/a") is None


# ---- 자본잠식 ----
KY_EQ = row("BS", "ifrs-full_Equity", "자본총계", "494536343970")
KY_CAP = row("BS", "ifrs-full_IssuedCapital", "자본금", "31983598000")


def test_capital_not_impaired_when_equity_exceeds_capital():  # 금양 2024
    ind, used = capital_impairment([KY_CAP, KY_EQ])
    assert ind["status"] == "OK" and ind["detail"]["impairment_pct"] == 0.0 and len(used) == 2


def test_capital_impairment_partial_fixture():
    ind, _ = capital_impairment([row("BS", "ifrs-full_IssuedCapital", "자본금", "1000"), row("BS", "ifrs-full_Equity", "자본총계", "400")])
    assert ind["detail"]["impairment_pct"] == pytest.approx(60.0)


def test_nonpositive_equity_is_full_impairment_even_without_capital_row():  # R1: 자기자본 ≤ 0이면 전액
    for equity in ("0", "-5000"):
        ind, _ = capital_impairment([row("BS", "ifrs-full_Equity", "자본총계", equity)])
        assert ind["status"] == "OK" and ind["detail"]["impairment_pct"] == 100.0


def test_capital_uses_balance_sheet_not_equity_statement():
    sce = row("SCE", "ifrs-full_Equity", "자본총계", "999")  # 자본변동표에도 같은 계정이 여러 줄 있다(확인됨)
    ind, _ = capital_impairment([sce, KY_CAP, KY_EQ])
    assert ind["detail"]["total_equity"] == 494536343970


def test_capital_missing_when_rows_absent():
    assert capital_impairment([KY_CAP])[0]["status"] == "MISSING"  # 자본총계 없음
    assert capital_impairment([KY_EQ])[0]["status"] == "MISSING"  # 자본금 없음 (자본총계 > 0)


# ---- 영업현금흐름 ----
CFO = "ifrs-full_CashFlowsFromUsedInOperatingActivities"


def test_operating_cash_flow_one_year_negative():  # 금양 2024: 당기 -203억, 전기 +149억
    ind, _ = operating_cash_flow([row("CF", CFO, "영업활동현금흐름", "-20344818738", "14930707843")])
    assert ind["status"] == "OK" and ind["detail"]["negative_years"] == 1


def test_operating_cash_flow_two_years_negative():  # 위니아 2023: 당기 -405억, 전기 -526억
    ind, _ = operating_cash_flow([row("CF", CFO, "영업활동현금흐름", "-40454949412", "-52594830578")])
    assert ind["detail"]["negative_years"] == 2


def test_operating_cash_flow_positive_is_zero_even_if_previous_missing():
    ind, _ = operating_cash_flow([row("CF", CFO, "영업활동현금흐름", "100", "-")])
    assert ind["status"] == "OK" and ind["detail"]["negative_years"] == 0


def test_operating_cash_flow_exactly_zero_is_not_a_loss():  # 경계: 0은 적자가 아니다 (fixture)
    ind, _ = operating_cash_flow([row("CF", CFO, "영업활동현금흐름", "0", "-100")])
    assert ind["status"] == "OK" and ind["detail"]["negative_years"] == 0


def test_operating_cash_flow_missing_previous_when_negative_cannot_tell_one_from_two():
    ind, _ = operating_cash_flow([row("CF", CFO, "영업활동현금흐름", "-100", "-")])
    assert ind["status"] == "MISSING"
    assert operating_cash_flow([])[0]["status"] == "MISSING"


# ---- 이자보상배율 (금융비용 기준, R1 결정) ----
def test_interest_coverage_uses_finance_costs():  # 금양 2024: 영업손실 -560억, 금융원가 271억
    rows = [row("CIS", "dart_OperatingIncomeLoss", "영업이익(손실)", "-56002221720"),
            row("CIS", "ifrs-full_FinanceCosts", "금융원가", "27112738869")]
    ind, _ = interest_burden(rows)
    assert ind["status"] == "OK" and ind["detail"]["interest_coverage"] == pytest.approx(-56002221720 / 27112738869)
    assert any("이자 외 손익" in n for n in ind["notes"])  # 한계를 notes에 남긴다


def test_interest_coverage_falls_back_to_account_name_and_nonpositive_cost_is_missing():
    by_name = [row("IS", NO_STD, "영업이익", "100"), row("IS", NO_STD, "금융비용", "50")]
    assert interest_burden(by_name)[0]["detail"]["interest_coverage"] == 2.0
    zero_cost = [row("IS", NO_STD, "영업이익", "100"), row("IS", NO_STD, "금융비용", "0")]
    assert interest_burden(zero_cost)[0]["status"] == "MISSING"  # 0점이 아니라 산정 불가
    assert interest_burden([row("IS", NO_STD, "영업이익", "100")])[0]["status"] == "MISSING"


# ---- 단기채무 ----
CASH = "ifrs-full_CashAndCashEquivalents"


def test_short_term_debt_sums_listed_accounts():  # 금양 2024: 단기차입금 + 유동성장기차입금 + 사채(유동)
    rows = [row("BS", CASH, "현금및현금성자산", "19955152951"),
            row("BS", NO_STD, "단기차입금", "245369019460"),
            row("BS", NO_STD, "유동성장기차입금", "8437584000"),
            row("BS", "ifrs-full_CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued", "사채", "17891433139"),
            row("BS", NO_STD, "장기차입금", "28393540000"),  # 비유동은 세지 않는다
            row("BS", "ifrs-full_NoncurrentPortionOfNoncurrentBondsIssued", "비유동 사채의 비유동성 부분", "13976432317")]
    ind, _ = short_term_debt(rows)
    total = 245369019460 + 8437584000 + 17891433139
    assert ind["detail"]["short_term_debt"] == total
    assert ind["detail"]["debt_to_cash_pct"] == pytest.approx(total / 19955152951 * 100)
    assert ind["notes"] == []  # 비유동임이 분명한 계정은 notes에도 안 나온다


def test_short_term_debt_combined_account_and_ambiguous_bond_is_noted():  # 위니아 2023
    rows = [row("BS", CASH, "현금및현금성자산", "1483423397"),
            row("BS", "ifrs-full_ShorttermBorrowings", "단기차입금및유동성장기부채", "180406821489"),
            row("BS", NO_STD, "회사채", "26200000000"),  # 표준계정코드가 없어 유동/비유동 구분 불가
            row("BS", NO_STD, "회사채", "0")]
    ind, _ = short_term_debt(rows)
    assert ind["detail"]["short_term_debt"] == 180406821489
    assert ind["detail"]["components"] == ["단기차입금및유동성장기부채"]
    assert "회사채" in ind["notes"][0] and "유동 여부 불명" in ind["notes"][0]


def test_short_term_debt_does_not_double_count_combined_and_parts():  # fixture: 합친 줄과 개별 줄이 같이 있는 경우
    rows = [row("BS", CASH, "현금및현금성자산", "100"),
            row("BS", "ifrs-full_ShorttermBorrowings", "단기차입금및유동성장기부채", "500"),
            row("BS", "ifrs-full_ShorttermBorrowings", "단기차입금", "300"),
            row("BS", NO_STD, "유동성장기차입금", "200"),
            row("BS", "dart_CurrentPortionOfBonds", "유동성사채", "50")]  # 사채는 합친 줄에 안 들어 있으니 센다
    ind, _ = short_term_debt(rows)
    assert ind["detail"]["short_term_debt"] == 550


def test_short_term_debt_hynix_current_borrowings_only():  # SK하이닉스 2024
    rows = [row("BS", CASH, "현금및현금성자산", "11205117000000"),
            row("BS", "ifrs-full_CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings", "차입금", "5252238000000"),
            row("BS", "ifrs-full_LongtermBorrowings", "차입금", "17431495000000")]  # 같은 이름의 비유동 줄
    ind, _ = short_term_debt(rows)
    assert ind["detail"]["debt_to_cash_pct"] == pytest.approx(5252238000000 / 11205117000000 * 100)
    assert ind["notes"] == []


def test_short_term_debt_includes_current_convertible_and_bonds():  # 오스템임플란트 2021
    rows = [row("BS", CASH, "현금및현금성자산", "266747436854"),
            row("BS", "ifrs-full_ShorttermBorrowings", "단기차입금", "168366861312"),
            row("BS", "ifrs-full_CurrentPortionOfLongtermBorrowings", "유동성장기차입금", "49578683784"),
            row("BS", "dart_CurrentPortionOfBonds", "유동성사채", "60000000000"),
            row("BS", "dart_CurrentPortionOfConvertibleBonds", "유동성전환사채", "26057640000"),
            row("BS", "dart_ConvertibleBonds", "전환사채", "1")]  # 유동 여부 불명 → 안 센다
    ind, _ = short_term_debt(rows)
    assert ind["detail"]["short_term_debt"] == 168366861312 + 49578683784 + 60000000000 + 26057640000
    assert "전환사채" in ind["notes"][0]


def test_short_term_debt_missing_cases():
    cash = row("BS", CASH, "현금및현금성자산", "100")
    assert short_term_debt([cash])[0]["status"] == "MISSING"  # 부채가 없는 건지 못 알아본 건지 구분 불가
    assert short_term_debt([row("BS", NO_STD, "단기차입금", "10")])[0]["status"] == "MISSING"  # 현금 없음
    assert short_term_debt([cash, row("BS", NO_STD, "단기차입금", "-")])[0]["status"] == "MISSING"  # 금액을 못 읽음
    assert short_term_debt([row("BS", CASH, "현금및현금성자산", "0"), row("BS", NO_STD, "단기차입금", "10")])[0]["status"] == "MISSING"


def test_quarterly_report_skips_flow_indicators():
    rows = [KY_CAP, KY_EQ, row("BS", CASH, "현금및현금성자산", "100"), row("BS", NO_STD, "단기차입금", "10"),
            row("CF", CFO, "영업활동현금흐름", "-1", "-1")]
    assert set(compute_financial_indicators(rows, "ANNUAL").indicators) == {CAPITAL_IMPAIRMENT, SHORT_TERM_DEBT, OPERATING_CASH_FLOW, INTEREST_BURDEN}
    assert set(compute_financial_indicators(rows, "QUARTER").indicators) == {CAPITAL_IMPAIRMENT, SHORT_TERM_DEBT}


# ---- 감사의견 ----
def opinion_row(year_label, opinion):
    return {"bsns_year": year_label, "adt_opinion": opinion, "rcept_no": "20250321001823"}


def test_normalize_opinion_matches_rule_vocabulary():
    # DART는 '적정의견'과 '적정'을 섞어 쓴다(확인됨). rules.yaml은 '적정'만 안다.
    assert {normalize_opinion(x) for x in ("적정의견", "적정", " 적정 ")} == {"적정"}
    assert normalize_opinion("한정의견") == "한정" and normalize_opinion("부적정의견") == "부적정"
    assert normalize_opinion("의견거절") == "의견거절" and normalize_opinion("거절") == "의견거절"
    assert normalize_opinion("해당사항 없음") is None and normalize_opinion(None) is None


def test_audit_opinion_picks_current_period_among_three_years_with_duplicates():  # 금양 2024 사업보고서
    rows = [opinion_row("제70기\n(당기)", "의견거절"), opinion_row("제70기\n(당기)", "의견거절"),
            opinion_row("제69기\n(전기)", "적정의견"), opinion_row("제68기\n(전전기)", "적정의견")]
    res = compute_audit_opinion(rows)
    assert res.indicators[AUDIT_OPINION]["status"] == "OK" and res.indicators[AUDIT_OPINION]["detail"] == {"opinion": "의견거절"}
    assert res.indicators[AUDIT_OPINION]["notes"] == []


def test_audit_opinion_conflicting_current_rows_use_worst():  # fixture
    res = compute_audit_opinion([opinion_row("제1기(당기)", "적정의견"), opinion_row("제1기(당기)", "한정")])
    assert res.indicators[AUDIT_OPINION]["detail"]["opinion"] == "한정" and res.indicators[AUDIT_OPINION]["notes"]


def test_audit_opinion_missing_cases():
    assert compute_audit_opinion([opinion_row("제69기(전기)", "적정")]).indicators[AUDIT_OPINION]["status"] == "MISSING"  # 당기 없음
    unknown = compute_audit_opinion([opinion_row("제1기(당기)", "해당사항 없음")]).indicators[AUDIT_OPINION]
    assert unknown["status"] == "MISSING" and "해당사항 없음" in unknown["notes"][1]  # 추측하지 않는다
