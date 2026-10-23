"""facts 테스트: 항목 API 값(실제 DART에서 본 값) → R4의 구조화 사실. 모든 payload는 R4의 StructuredFacts 검증기를 통과해야 한다."""
import pytest

from collector.facts import (
    SPECS,
    build_ambiguous_payload,
    build_payload,
    item_for,
    parse_date,
    parse_original_date,
    split_title,
)
from shared.facts import StructuredFacts

AT = "2026-10-23T09:05:00+09:00"
CORP = "00106119"


def build(api, row, rcept="20241031000530"):
    payload = build_payload(api, row, rcept_no=rcept, corp_code=CORP, fetched_at=AT)
    StructuredFacts.model_validate(payload)  # R4의 검증을 통과해야 한다 (값·dtype·사유·출처 접수번호)
    return payload, {f["name"]: f for f in payload["facts"]}


# ---- 제목·날짜 ----
@pytest.mark.parametrize("title, expected", [
    ("주요사항보고서(유상증자결정)", (False, "주요사항보고서(유상증자결정)")),
    ("[기재정정]주요사항보고서(유상증자결정)", (True, "주요사항보고서(유상증자결정)")),
    ("[첨부정정]주요사항보고서(부도발생)", (True, "주요사항보고서(부도발생)")),
    ("주요사항보고서(채권은행등의관리절차개시)", (False, "주요사항보고서(채권은행등의관리절차개시)")),
])
def test_split_title(title, expected):
    assert split_title(title) == expected


def test_item_for_covers_the_ten_verified_items_and_ignores_the_rest():
    assert item_for("[기재정정]주요사항보고서(유상증자결정)") == ("RIGHTS_OFFERING", "piicDecsn")
    assert item_for("주요사항보고서(채권은행 등의 관리절차 개시)") == ("BANK_MANAGEMENT", "bnkMngtPcbg")  # 공백이 달라도
    assert item_for("주요사항보고서(회사분할결정)") is None  # 필드 값을 확인하지 못한 항목은 사실을 만들지 않는다
    assert item_for("사업보고서 (2024.12)") is None and item_for("횡령ㆍ배임혐의발생") is None
    assert len({api for _, api in __import__("collector.facts", fromlist=["ITEM_TITLES"]).ITEM_TITLES.values()}) == len(SPECS) == 10


@pytest.mark.parametrize("raw, iso", [("2024년 09월 27일", "2024-09-27"), ("2025.03.20", "2025-03-20"), ("2022. 3. 3.", "2022-03-03"),
                                      ("2024년11월03일", "2024-11-03"), ("2024-01-09", "2024-01-09")])
def test_date_formats_seen_in_real_filings(raw, iso):
    assert parse_date(raw) == iso


def test_original_date_is_read_from_correction_text_in_every_observed_format():
    # 실제 정정 본문의 형태: 금양 20250117000380(년월일), 삼영이엔씨(점), 하나은행(공백 점)
    assert parse_original_date("1. 정정대상 공시서류 : 유상증자 결정 2. 정정대상 공시서류의 최초제출일 : 2024년 09월 27일 3. 정정사항") == "2024-09-27"
    assert parse_original_date("2. 정정대상 공시서류의 최초제출일 : 2025.03.20 3.") == "2025-03-20"
    assert parse_original_date("2. 정정대상 공시서류의 최초제출일 : 2022. 3. 3. 3. 정정사항") == "2022-03-03"
    assert parse_original_date("정정대상 공시서류 최초 제출일 2024-01-12") == "2024-01-12"


def test_original_date_is_none_when_absent_or_impossible():
    assert parse_original_date("정정 내용 없음") is None and parse_original_date("") is None and parse_original_date(None) is None
    assert parse_original_date("정정대상 공시서류의 최초제출일 : 2024.13.40") is None  # 없는 날짜를 지어내지 않는다


# ---- 유상증자 (금양 20241031000530, 제3자배정) ----
RIGHTS = {"nstk_ostk_cnt": "5,917,159", "nstk_estk_cnt": "-", "fv_ps": "500", "bfic_tisstk_ostk": "58,050,037", "bfic_tisstk_estk": "-",
          "fdpp_fclt": "-", "fdpp_bsninh": "-", "fdpp_op": "-", "fdpp_dtrp": "-", "fdpp_ocsa": "-", "fdpp_etc": "299,999,961,300",
          "ic_mthn": "제3자배정증자", "ssl_at": "N", "ssl_bgd": "-", "ssl_edd": "-"}


def test_rights_offering_numbers_dashes_and_derived_ratio():
    payload, f = build("piicDecsn", RIGHTS)
    assert f["new_common_shares"]["value"] == 5917159 and f["new_common_shares"]["unit"] == "주" and f["new_common_shares"]["raw"] == "5,917,159"
    assert f["use_other"]["value"] == 299999961300
    assert f["issue_ratio_common_pct"]["value"] == pytest.approx(5917159 / 58050037 * 100, abs=1e-3)  # ≈ 10.19% (R4 규칙: 20% 미만이면 가산 없음)
    assert f["third_party_allocation"]["value"] is True and f["third_party_allocation"]["status"] == "PRESENT"
    # '-'는 0이 아니라 MISSING + 사유
    for name in ("new_other_shares", "other_shares_before", "use_facility", "use_operating"):
        assert f[name]["status"] == "MISSING" and f[name]["value"] is None and "-" in f[name]["reason"]
    assert "ssl_at" not in {x["source"]["field"] for x in payload["facts"]}  # 의미를 확인하지 못한 필드는 사실로 만들지 않는다


def test_rights_offering_ratio_is_missing_not_zero_when_inputs_are_missing():
    _, f = build("piicDecsn", {**RIGHTS, "bfic_tisstk_ostk": "-"})
    assert f["issue_ratio_common_pct"]["status"] == "MISSING" and f["issue_ratio_common_pct"]["value"] is None
    _, f = build("piicDecsn", {**RIGHTS, "bfic_tisstk_ostk": "0"})
    assert f["issue_ratio_common_pct"]["status"] == "MISSING"  # 0으로 나누지 않는다


def test_shareholder_allocation_is_not_third_party():
    _, f = build("piicDecsn", {**RIGHTS, "ic_mthn": "주주배정후 실권주 일반공모"})  # CJ CGV 정정본
    assert f["third_party_allocation"]["value"] is False


# ---- 전환사채 (이엠앤아이 20251229000465) ----
CB = {"bd_tm": "15", "bd_knd": "무기명식 이권부 무보증 사모 전환사채", "bd_fta": "1,500,000,000", "bd_intr_ex": "0.0", "bd_intr_sf": "3.0",
      "bd_mtd": "2029년 01월 07일", "bdis_mthn": "사모", "cv_rt": "100", "cv_prc": "820", "cvisstk_cnt": "1,829,268",
      "cvisstk_tisstk_vs": "7.90", "cvrqpd_bgd": "2027년 01월 07일", "cvrqpd_edd": "2028년 12월 07일",
      "act_mktprcfl_cvprc_lwtrsprc": "574", "pymd": "2026년 01월 07일"}


def test_convertible_bond_values_dates_and_refixing_floor():
    _, f = build("cvbdIsDecsn", CB, "20251229000465")
    assert f["bond_face_amount"]["value"] == 1500000000 and f["conversion_price"]["value"] == 820 and f["conversion_price_floor"]["value"] == 574
    assert f["coupon_rate_pct"]["value"] == 0.0 and f["coupon_rate_pct"]["status"] == "PRESENT"  # 0.0은 값이다 (결측이 아니다)
    assert f["maturity_date"]["value"] == "2029-01-07" and f["conversion_shares_share_pct"]["value"] == 7.9
    assert f["refixing_floor_below_price"]["value"] is True  # 574 < 820


def test_refixing_is_missing_when_floor_is_blank_because_api_cannot_say_no_clause():
    _, f = build("cvbdIsDecsn", {**CB, "act_mktprcfl_cvprc_lwtrsprc": "-"})
    assert f["refixing_floor_below_price"]["status"] == "MISSING" and f["refixing_floor_below_price"]["value"] is None
    assert "알 수 없다" in f["refixing_floor_below_price"]["reason"]  # '조항 없음'(false)으로 단정하지 않는다
    _, f = build("cvbdIsDecsn", {**CB, "act_mktprcfl_cvprc_lwtrsprc": "820"})
    assert f["refixing_floor_below_price"]["value"] is False  # 하한이 전환가와 같으면 하향 조정 여지가 없다


# ---- 감자 · 영업정지 · 해산 · 소송 · 부도 · 합병 · 관리절차 · 회생 ----
def test_capital_reduction_ratio_and_long_text_is_not_structured():
    long_method = "2025년 11월03일 채권변제 출자전환된 보통주 " + "가" * 250
    _, f = build("crDecsn", {"cr_rt_ostk": "88.88", "cr_rt_estk": "-", "fv_ps": "500", "bfcr_cpt": "44,416,753,000", "atcr_cpt": "41,437,055,500",
                             "cr_std": "2025년 11월 04일", "cr_mth": long_method, "cr_rs": "재무구조개선(「채무자회생및파산에관한법률」에 따른 회생계획인가)"},
                 "20251020000310")
    assert f["reduction_ratio_common_pct"]["value"] == 88.88 and f["reduction_base_date"]["value"] == "2025-11-04"
    assert f["reduction_reason"]["status"] == "PRESENT" and "재무구조개선" in f["reduction_reason"]["value"]  # 짧은 사유는 사실로
    m = f["reduction_method"]  # 긴 서술은 구조화하지 않는다 → 원문 검색으로 확인
    assert m["status"] == "NEEDS_CHECK" and m["value"] is None and m["raw"] is None and "원문" in m["reason"]


def test_business_suspension_share_of_revenue():
    _, f = build("bsnSp", {"bsnsp_rm": "토목건축공사업", "bsnsp_amt": "7,651,504,603,142", "rsl": "10,503,609,086,034", "sl_vs": "72.84",
                           "bsnsp_cn": "토목건축공사업에 대한 영업정지 2개월", "bsnspd": "2026년 01월 23일"}, "20251216000493")
    assert f["suspended_revenue_share_pct"]["value"] == 72.84 and f["suspended_revenue"]["value"] == 7651504603142
    assert f["suspension_date"]["value"] == "2026-01-23" and "2개월" in f["suspension_content"]["value"]


def test_lawsuit_states_that_claim_amount_cannot_be_computed():
    _, f = build("lwstLg", {"icnm": "신주발행금지 가처분", "cpct": "서울중앙지방법원", "cfd": "2025년 12월 16일",
                            "ac_ap": "주식회사 영풍, 주식회사 한국기업투자홀딩스, 유한회사 와이피씨", "rq_cn": "1. 채무자는 ..."}, "20251216000384")
    assert f["case_name"]["value"] == "신주발행금지 가처분" and f["filed_date"]["value"] == "2025-12-16"
    claim = f["claim_amount"]
    assert claim["status"] == "NEEDS_CHECK" and claim["value"] is None and claim["source"]["field"] == "rq_cn"  # 자기자본 대비 청구액을 만들 수 없다


def test_default_dissolution_rehabilitation_bank_management_and_merger():
    _, f = build("dfOcr", {"df_cn": "당사 기업어음 발행 부도", "df_amt": "6,000,000,000", "df_bnk": "신한은행 여의도중앙대기업금융센터", "dfd": "-", "df_rs": "- 부도사유 : 예금부족"}, "20240226006299")
    assert f["default_amount"]["value"] == 6000000000 and f["default_date"]["status"] == "MISSING"
    _, f = build("dsRsOcr", {"ds_rs": "회생절차폐지에 따른 법원의 직권파산", "ds_rsd": "2024년 09월 20일"}, "20240920000458")
    assert f["dissolution_date"]["value"] == "2024-09-20"
    _, f = build("ctrcvsBgrq", {"apcnt": "주식회사 위니아", "cpct": "서울회생법원", "rq_rs": "경영정상화 및 향후 계속기업으로의 가치 보존", "rqd": "2023년 10월 04일"}, "20231005000467")
    assert f["court"]["value"] == "서울회생법원" and f["request_date"]["value"] == "2023-10-04"
    _, f = build("bnkMngtPcbg", {"mngt_pcbg_dd": "2024년 01월 11일", "mngt_int": "(주)태영건설 금융채권자협의회 (주채권은행 : 한국산업은행)",
                                 "mngt_pd": "2024년 01월 11일 ~ 2027년 05월 30일", "mngt_rs": "경영정상화"}, "20250217001661")
    assert f["management_start_date"]["value"] == "2024-01-11" and f["management_reason"]["value"] == "경영정상화"
    _, f = build("cmpMgDecsn", {"mg_stn": "소규모합병", "mgptncmp_cmpnm": "탑플란 주식회사 (Toplan Co., Ltd)", "mgptncmp_rl_cmpn": "자회사",
                                "exevl_atn": "미해당", "rbsnfdtl_teqt": "1,359,281,828", "rbsnfdtl_nic": "-2,935,191,253", "mgsc_mgdt": "2025년 01월 01일"}, "20241031000317")
    assert f["merger_type"]["value"] == "소규모합병" and f["counterparty_net_income"]["value"] == -2935191253  # 음수 순이익


def test_unreadable_values_need_check_instead_of_guessing():
    _, f = build("dfOcr", {"df_amt": "약 60억", "dfd": "내일"})
    assert f["default_amount"]["status"] == "NEEDS_CHECK" and f["default_amount"]["value"] is None
    assert f["default_date"]["status"] == "NEEDS_CHECK"


def test_fields_missing_from_the_response_are_not_invented():
    _, f = build("dsRsOcr", {"ds_rs": "해산"})
    assert set(f) == {"dissolution_reason"}  # 응답에 없는 필드를 MISSING으로 꾸미지 않는다


def test_every_fact_name_is_unique_and_stable_snake_case():
    import re
    for api, specs in SPECS.items():
        names = [s.name for s in specs]
        assert len(names) == len(set(names)) and all(re.fullmatch(r"[a-z][a-z0-9_]*", n) for n in names), api


# ---- 같은 접수번호에 행이 여러 개 ----
@pytest.mark.parametrize("api", sorted(SPECS))
def test_ambiguous_rows_make_every_fact_needs_check_and_pass_the_r4_validator(api):
    payload = build_ambiguous_payload(api, 2, rcept_no="20241031000530", corp_code=CORP, fetched_at=AT)
    StructuredFacts.model_validate(payload)
    assert [f["name"] for f in payload["facts"]] == [spec.name for spec in SPECS[api]]
    for f in payload["facts"]:
        # 0·false로 채우지 않고, 값을 하나도 고르지 않는다
        assert f["status"] == "NEEDS_CHECK" and f["value"] is None and f["raw"] is None
        assert "2개" in f["reason"] and f["source"]["api"] == api
        assert f["source"].get("row_key") is None  # 안정된 구분 값을 확인하기 전에는 row_key를 만들지 않는다
