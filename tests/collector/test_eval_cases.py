"""eval/cases/r3_cases.json 검사 (오프라인). 접수번호가 DART에 실제로 있는지는 `scripts/data/verify_cases.py`가 확인한다.
여기서는 파일 자체의 일관성을 지킨다: 기대값 규칙, 모호한 판단의 기록, 형식, 타임라인 순서."""
import json
import re
from pathlib import Path

import pytest

DOC = json.loads((Path(__file__).resolve().parents[2] / "eval" / "cases" / "r3_cases.json").read_text(encoding="utf-8"))
CASES = DOC["cases"]
STAGES = set(DOC["stages"])
GROUPS = {"RED_EVENT", "NOT_RED", "RESOLUTION", "ORANGE", "YELLOW", "NORMAL", "TIMELINE"}
# risk_engine/rules.yaml의 위험 항목 이름 + 공시 한 건이 위험이 아닌 경우
ITEMS = {"DEFAULT", "REHABILITATION", "BANK_MANAGEMENT", "DISSOLUTION", "BUSINESS_SUSPENSION", "EMBEZZLEMENT", "TRADING_HALT", "DELISTING",
         "ADMIN_ISSUE", "RIGHTS_OFFERING", "CB_BW", "CAPITAL_REDUCTION", "LAWSUIT", "MERGER", "AUDIT_OPINION"}
ROLES = {"trigger", "context", "correction", "follow_up", "resolution"}
ids = [c["case_id"] for c in CASES]


def evidence(case):
    return [*case.get("evidence", []), *case.get("steps", [])]


def test_case_ids_are_unique_and_prefixed():
    assert len(ids) == len(set(ids)) and all(i.startswith("r3-") for i in ids)  # 파일 이름처럼 id에도 담당(r3)을 붙인다


def test_every_group_is_known_and_covers_the_design_stages():
    assert {c["group"] for c in CASES} <= GROUPS
    # 설계서 STEP 06: 🔴🟠🟡 단계별 사례와 정상 사례가 있어야 한다
    assert {"RED_EVENT", "ORANGE", "YELLOW", "NORMAL"} <= {c["group"] for c in CASES}


@pytest.mark.parametrize("case", CASES, ids=ids)
def test_required_fields_and_formats(case):
    # 종목코드는 숫자 6자리만이 아니다: 2026년 신규 상장 종목에서 영숫자 코드(0004V0 등)가 나온다
    assert re.fullmatch(r"[0-9A-Z]{6}", case["stock_code"]) and re.fullmatch(r"\d{8}", case["corp_code"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", case["as_of"]) and case["corp_name"] and case["title"]
    assert case["expected_stage"] in STAGES | {None} and case["expectation_source"]
    for ev in evidence(case):
        assert re.fullmatch(r"\d{14}", ev["rcept_no"]) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", ev["filed_at"]) and ev["title"].strip()
        assert ev["item"] in ITEMS and ev["role"] in ROLES
        assert ev["url"] == f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={ev['rcept_no']}"
        assert ev["filed_at"] <= case["as_of"] or case["group"] == "ORANGE"  # 판단 시점 이후의 공시를 근거로 쓰지 않는다


@pytest.mark.parametrize("case", [c for c in CASES if c["scope"] == "single_filing" and c["group"] not in ("NORMAL",)], ids=lambda c: c["case_id"])
def test_as_of_is_the_filing_date_not_the_receipt_number_prefix(case):
    """접수번호 앞 8자리와 접수일이 다를 수 있다(주말 등, 한국테크놀로지 20240920... → 2024-09-23). 판단 시점은 접수일이다."""
    ev = case["evidence"]
    assert case["as_of"] == (ev[-1] if case["group"] == "RESOLUTION" else ev[0])["filed_at"]


@pytest.mark.parametrize("case", [c for c in CASES if c["group"] == "RED_EVENT"], ids=lambda c: c["case_id"])
def test_red_events_expect_red(case):
    assert case["expected_stage"] == "RED" and any(e["role"] == "trigger" for e in case["evidence"])


@pytest.mark.parametrize("case", [c for c in CASES if c["group"] == "NOT_RED"], ids=lambda c: c["case_id"])
def test_not_red_cases_never_expect_red(case):
    assert case["expected_stage"] in ("NONE", "WATCH", None)


@pytest.mark.parametrize("case", [c for c in CASES if c["expected_stage"] is None and c["group"] != "TIMELINE"], ids=lambda c: c["case_id"])
def test_unknown_expectation_always_says_why(case):
    """기대값이 비어 있으면 이유를 적어야 한다: 판단이 모호한 것(ambiguity)이거나, 점수 계산이 필요한 것이거나, 경보 없음만 요구하는 정상 사례."""
    assert case.get("ambiguity") or "규칙 계산 필요" in case["expectation_source"] or case.get("expected_alert") is False


@pytest.mark.parametrize("case", [c for c in CASES if c["group"] == "NORMAL"], ids=lambda c: c["case_id"])
def test_normal_cases_expect_no_alert_and_have_no_red_titles(case):
    assert case["expected_alert"] is False and case["window_red_titles"] == []
    # 정상 사례의 재무 지표는 문제가 없어야 한다 (사례를 잘못 고르지 않았는지)
    f = case["facts"]
    assert f["AUDIT_OPINION"]["opinion"] == "적정" and f["CAPITAL_IMPAIRMENT"]["impairment_pct"] == 0
    assert f["OPERATING_CASH_FLOW"]["negative_years"] == 0 and f["INTEREST_BURDEN"]["interest_coverage"] >= 1


@pytest.mark.parametrize("case", [c for c in CASES if c["group"] == "YELLOW"], ids=lambda c: c["case_id"])
def test_yellow_cases_have_an_unfavorable_audit_opinion(case):
    assert case["expected_stage"] == "YELLOW" and case["facts"]["AUDIT_OPINION"]["opinion"] in ("의견거절", "부적정")


@pytest.mark.parametrize("case", [c for c in CASES if c.get("facts_meta")], ids=lambda c: c["case_id"])
def test_facts_are_present_or_explain_why_missing(case):
    meta = case["facts_meta"]
    assert meta["status"] in ("PRESENT", "MISSING")
    if meta["status"] == "PRESENT":
        assert case["facts"]
    else:
        assert case["facts"] is None and meta["reason"]  # 값이 없으면 0으로 채우지 않고 이유를 남긴다


@pytest.mark.parametrize("case", [c for c in CASES if c["group"] == "TIMELINE"], ids=lambda c: c["case_id"])
def test_timeline_steps_are_chronological_and_stage_never_drops_without_resolution(case):
    steps = case["steps"]
    assert [s["rcept_no"] for s in steps] == sorted((s["rcept_no"] for s in steps), key=lambda r: (next(x["filed_at"] for x in steps if x["rcept_no"] == r), r))
    assert all(s["expected_stage_after"] in STAGES and s["expected_new_alert"] in (True, False, None) and s["why"] for s in steps)
    assert steps[0]["role"] == "trigger"
    first_alert = next(i for i, s in enumerate(steps) if s["expected_new_alert"])
    # 새 알림은 이미 경보 단계(RED 등)가 된 뒤 같은 사건의 정정·후속에서는 나오지 않는다
    assert all(not s["expected_new_alert"] for s in steps if s["role"] in ("correction", "follow_up") and steps.index(s) > first_alert)


def test_legacy_expectations_are_labelled_with_their_source():
    """옛 프로젝트(Low~Critical) 기대값은 새 단계와 다르므로 출처를 밝혀 둔다."""
    for c in CASES:
        if "legacy" in c:
            assert "옛 등급" in c["legacy"]["source"]


def test_no_embedded_original_text():
    """사례 파일에는 공시 원문을 담지 않는다 (접수번호·제목·API 값만). 항목 API 값도 300자로 잘려 있다."""
    for c in CASES:
        for v in (c.get("facts") or {}).values():
            if isinstance(v, str):
                assert len(v) <= 300
    assert "body_text" not in json.dumps(DOC, ensure_ascii=False)
