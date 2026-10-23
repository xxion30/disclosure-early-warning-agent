"""구조화 사실 저장과 분석 인계 테스트: 가짜 DART + 임시 SQLite + R4의 실제 store_facts·mark_ready·enqueue_ready.
정정 체인은 실제 금양 유상증자(4건)와 위니아 회생절차개시신청(원본+정정)의 접수번호·날짜를 쓴다."""
import asyncio
from datetime import date

import pytest
from sqlalchemy import select

from collector import jobs
from collector.jobs import collect_stock
from collector.watchlist import Target
from mcp_servers.business.tools.get_disclosure_facts import get_disclosure_facts
from shared.analysis import enqueue_ready
from shared.db import get_session
from shared.facts import StructuredFacts
from shared.models import AnalysisJob, Disclosure
from tests.collector.conftest import KY

TODAY = date(2025, 4, 1)
CORP = KY["corp_code"]
TARGET = Target(KY["stock_code"], CORP, KY["name"])

TITLE = "주요사항보고서(유상증자결정)"
CORR = "[기재정정]주요사항보고서(유상증자결정)"


def lrow(rcept, dt, title, corp=CORP):
    return {"corp_code": corp, "corp_name": "금양", "stock_code": "001570", "rcept_no": rcept, "rcept_dt": dt, "report_nm": title, "flr_nm": "금양", "rm": "유"}


# 실제 금양 유상증자결정 체인 (list.json). API 기준으로 묶으면 [9/27 → 10/18 → 1/17]이고 10/31은 별개의 재결정이다
ORIG_0927 = lrow("20240927000916", "20240927", TITLE)
CORR_1018 = lrow("20241018000341", "20241018", CORR)
NEW_1031 = lrow("20241031000530", "20241031", TITLE)
CORR_0117 = lrow("20250117000380", "20250117", CORR)
CHAIN = [ORIG_0927, CORR_1018, NEW_1031, CORR_0117]

RIGHTS_1031 = {"nstk_ostk_cnt": "5,917,159", "nstk_estk_cnt": "-", "fv_ps": "500", "bfic_tisstk_ostk": "58,050,037", "bfic_tisstk_estk": "-",
               "fdpp_fclt": "-", "fdpp_bsninh": "-", "fdpp_op": "-", "fdpp_dtrp": "-", "fdpp_ocsa": "-", "fdpp_etc": "299,999,961,300",
               "ic_mthn": "제3자배정증자", "rcept_no": "20241031000530"}
RIGHTS_0117 = {"nstk_ostk_cnt": "-", "nstk_estk_cnt": "-", "fv_ps": "500", "bfic_tisstk_ostk": "58,050,037", "ic_mthn": "주주배정후 실권주 일반공모",
               "rcept_no": "20250117000380"}
DOC_1018 = "1. 정정대상 공시서류 : 유상증자 결정 2. 정정대상 공시서류의 최초제출일 : 2024년 09월 27일 3. 정정사항"
DOC_0117 = "1. 정정대상 공시서류 : 유상증자 결정 2. 정정대상 공시서류의 최초제출일 : 2024년 09월 27일 3. 정정사항 유상증자 철회"


def run(coro):
    return asyncio.run(coro)


def setup_chain(fake):
    fake.list_rows[CORP] = [dict(r) for r in CHAIN]
    fake.docs["20241018000341"], fake.docs["20250117000380"] = DOC_1018, DOC_0117
    # 항목 API는 체인의 최초 접수일로 조회하고 최신 정정본만 돌려준다
    fake.items[("piicDecsn", CORP, "20240927")] = [dict(RIGHTS_0117)]
    fake.items[("piicDecsn", CORP, "20241031")] = [dict(RIGHTS_1031)]


def disclosure(rcept):
    with get_session() as s:
        d = s.get(Disclosure, rcept)
        return {"facts": d.structured_facts, "ready": d.analysis_ready_at, "version": d.input_version, "original": d.original_rcept_no, "event_id": d.event_id}


def test_facts_land_on_the_latest_chain_member_and_everything_is_handed_off(db, fake):
    setup_chain(fake)
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.new_disclosures == 4 and out.facts == 2 and out.handed_off == 4, out
    # 금양 4건: 10/31(별개의 재결정)과 1/17(9/27의 정정)에 사실이 붙고, 9/27·10/18은 최신본이 아니라 사실이 없다
    f1031, f0117 = disclosure("20241031000530"), disclosure("20250117000380")
    assert f1031["facts"] and f0117["facts"]
    assert disclosure("20240927000916")["facts"] is None and disclosure("20241018000341")["facts"] is None
    # 인계는 사실이 없는 공시까지 모두 (R4 enqueue는 analysis_ready_at이 있어야 한다)
    assert all(disclosure(r["rcept_no"])["ready"] and disclosure(r["rcept_no"])["version"] for r in CHAIN)
    names = {x["name"]: x for x in f1031["facts"]["facts"]}
    assert names["new_common_shares"]["value"] == 5917159 and names["third_party_allocation"]["value"] is True
    assert names["issue_ratio_common_pct"]["value"] == pytest.approx(5917159 / 58050037 * 100, abs=1e-3)
    assert StructuredFacts.model_validate(f0117["facts"]).rcept_no == "20250117000380"  # 사실의 출처 접수번호는 그 공시 자신
    # 철회 정정: 값이 '-'라 0이 아니라 MISSING
    withdrawn = {x["name"]: x for x in f0117["facts"]["facts"]}
    assert withdrawn["new_common_shares"]["status"] == "MISSING" and withdrawn["third_party_allocation"]["value"] is False


def test_correction_links_to_the_original_named_in_its_text_not_to_the_nearest_original(db, fake):
    setup_chain(fake)
    run(collect_stock(fake.client(), TARGET, today=TODAY))
    # 1/17 정정의 직전 최초 공시는 10/31이지만 본문은 최초제출일 2024-09-27이라고 한다 → 9/27이 맞다
    assert disclosure("20250117000380")["original"] == "20240927000916"
    assert disclosure("20241018000341")["original"] == "20240927000916"
    assert disclosure("20241031000530")["original"] is None and disclosure("20240927000916")["original"] is None
    # A unique same-title original on the explicitly quoted original date is confirmed.
    assert disclosure("20250117000380")["event_id"] == "20240927000916"
    assert disclosure("20241018000341")["event_id"] == "20240927000916"
    assert disclosure("20241031000530")["event_id"] is None
    with get_session() as session:
        correction = session.get(Disclosure, "20250117000380")
        assert correction.event_started_at[:10] == "2024-09-27"
        assert correction.event_link_evidence[0]['quote'] in DOC_0117
        assert correction.event_link_evidence[0]['rcept_no'] == correction.rcept_no


def test_second_run_does_not_touch_handed_off_disclosures_or_call_dart_again(db, fake):
    setup_chain(fake)
    run(collect_stock(fake.client(), TARGET, today=TODAY))
    before = {r["rcept_no"]: disclosure(r["rcept_no"]) for r in CHAIN}
    calls = (len(fake.paths("document.xml")), len(fake.paths("piicDecsn.json")))
    out = run(collect_stock(fake.client(), TARGET, today=date(2025, 4, 2)))
    assert out.ok and out.new_disclosures == 0 and out.facts == 0 and out.handed_off == 0
    assert (len(fake.paths("document.xml")), len(fake.paths("piicDecsn.json"))) == calls  # 본문도 항목 API도 다시 받지 않는다
    assert {r["rcept_no"]: disclosure(r["rcept_no"]) for r in CHAIN} == before


def test_transient_failure_leaves_disclosures_pending_and_next_run_finishes_them(db, fake):
    setup_chain(fake)
    fake.fail_docs.add("20250117000380")
    out = run(collect_stock(fake.client(max_retries=0), TARGET, today=TODAY))
    assert not out.ok  # 정정 본문 요청이 서버 오류 → 이 종목은 실패(커서 유지)
    assert all(disclosure(r["rcept_no"])["ready"] is None for r in CHAIN)  # 아무것도 인계되지 않았다 (반쯤 처리된 상태를 남기지 않는다)
    fake.fail_docs.clear()
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.new_disclosures == 0 and out.facts == 2 and out.handed_off == 4  # 공시는 이미 있어도 인계 전이면 다시 처리한다


def test_superseded_original_gets_no_facts_but_the_correction_does(db, fake):
    """위니아 회생절차개시신청: 최초 20231005000002, 정정 20231005000467. 항목 API는 최초 접수일로 조회하면 정정본만 준다."""
    corp = "00300557"
    rows = [lrow("20231005000002", "20231005", "주요사항보고서(회생절차개시신청)", corp), lrow("20231005000467", "20231005", "[기재정정]주요사항보고서(회생절차개시신청)", corp)]
    fake.list_rows[corp] = rows
    fake.docs["20231005000467"] = "2. 정정대상 공시서류의 최초제출일 : 2023.10.05 3. 정정사항"
    fake.items[("ctrcvsBgrq", corp, "20231005")] = [{"rcept_no": "20231005000467", "apcnt": "주식회사 위니아", "cpct": "서울회생법원", "rqd": "2023년 10월 04일", "rq_rs": "경영정상화"}]
    fake.corp_zip = fake.corp_zip
    out = run(collect_stock(fake.client(), Target("071460", corp, "위니아"), today=date(2023, 10, 31)))
    assert out.ok and out.facts == 1 and out.handed_off == 2 and not out.problems
    assert disclosure("20231005000002")["facts"] is None and disclosure("20231005000002")["ready"]
    facts = {x["name"]: x for x in disclosure("20231005000467")["facts"]["facts"]}
    assert facts["court"]["value"] == "서울회생법원" and facts["request_date"]["value"] == "2023-10-04"
    assert disclosure("20231005000467")["original"] == "20231005000002"


def test_unreadable_correction_and_orphan_chain_become_problems_not_failures(db, fake):
    fake.list_rows[CORP] = [lrow("20250117000380", "20250117", CORR), lrow("20250201000001", "20250201", CORR)]
    fake.docs["20250117000380"] = DOC_0117  # 최초제출일 2024-09-27: 이 수집 범위에 최초 공시가 없고 항목 API도 행이 없다
    fake.docs["20250201000001"] = "정정 내용만 있고 날짜가 없다"
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.facts == 0 and out.handed_off == 2
    assert any("항목 API" in p and "20250117000380" in p for p in out.problems) and any("최초제출일을 읽지 못함" in p for p in out.problems)
    assert disclosure("20250117000380")["facts"] is None and disclosure("20250117000380")["ready"]  # 사실 없이도 인계한다
    assert disclosure("20250117000380")["original"] is None  # 근거 없이 연결하지 않는다


def test_disclosures_without_an_item_api_are_handed_off_with_null_facts(db, fake):
    fake.list_rows[CORP] = [lrow("20250321802062", "20250321", "투자유의안내              (상장폐지 기준 해당)"), lrow("20250410801159", "20250410", "기타시장안내 (상장폐지 이의신청서 제출)")]
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.facts == 0 and out.handed_off == 2
    assert disclosure("20250321802062")["facts"] is None and disclosure("20250321802062")["ready"]
    assert get_disclosure_facts("20250321802062")["data"]["availability"] == "UNAVAILABLE"  # NULL은 '위험 없음'이 아니다 (R4 도구가 그렇게 돌려준다)


def test_invalid_payload_is_isolated_to_that_disclosure(db, fake, monkeypatch):
    setup_chain(fake)
    real = jobs.build_payload

    def sabotage(api, row, **kw):
        payload = real(api, row, **kw)
        if kw["rcept_no"] == "20250117000380":
            payload["facts"][0]["status"] = "PRESENT"  # PRESENT인데 value가 None → R4 검증기가 거절
            payload["facts"][0]["value"] = None
        return payload

    monkeypatch.setattr(jobs, "build_payload", sabotage)
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.facts == 1 and out.handed_off == 4  # 하나가 거절돼도 나머지는 저장하고 모두 인계
    assert any("20250117000380" in p and "검증 실패" in p for p in out.problems)
    assert disclosure("20250117000380")["facts"] is None and disclosure("20241031000530")["facts"]


def test_handoff_is_consumable_by_r4_analysis_queue_and_facts_by_the_mcp_tool(db, fake):
    setup_chain(fake)
    run(collect_stock(fake.client(), TARGET, today=TODAY))
    got = get_disclosure_facts("20241031000530")["data"]
    assert got["availability"] == "AVAILABLE" and got["structured_facts"]["facts"] and got["facts_hash"]
    with get_session() as s:
        queued = enqueue_ready(s, "2025-04-01", "rules-digest")  # R4: analysis_ready_at이 있는 공시만 분석 작업이 된다
    # A later manifest must include the preceding filing's published history.
    assert [j.rcept_no for j in queued] == [min(r["rcept_no"] for r in CHAIN)]
    assert queued[0].as_of == "2024-09-27"
    with get_session() as s:
        assert s.scalar(select(AnalysisJob).limit(1)).status == "PENDING"
        assert enqueue_ready(s, "2025-04-01", "rules-digest") == []


def test_rerun_after_new_correction_hands_off_only_the_new_one(db, fake):
    setup_chain(fake)
    run(collect_stock(fake.client(), TARGET, today=TODAY))
    first = disclosure("20250117000380")
    # 2025-03에 같은 사건(9/27)의 새 정정이 올라왔다 → 항목 API는 이제 이 정정본이 최신이다
    newer = lrow("20250320000111", "20250320", CORR)
    fake.list_rows[CORP] = [*CHAIN, newer]
    fake.docs["20250320000111"] = DOC_0117
    fake.items[("piicDecsn", CORP, "20240927")] = [{**RIGHTS_0117, "rcept_no": "20250320000111", "ic_mthn": "제3자배정증자"}]
    out = run(collect_stock(fake.client(), TARGET, today=date(2025, 4, 2)))
    assert out.new_disclosures == 1 and out.facts == 1 and out.handed_off == 1
    assert disclosure("20250117000380") == first  # 이미 인계된 이전 정정본은 그대로 (그때의 최신본이었다)
    assert disclosure("20250320000111")["facts"]["facts"] and disclosure("20250320000111")["original"] == "20240927000916"


def test_partial_write_before_an_error_is_rolled_back_for_that_disclosure_only(db, fake, monkeypatch):
    """store_facts가 값을 일부 쓴 뒤 ValueError를 내는 상황(예: 저장 도중 해시 계산 실패)에서도 그 공시의 반쯤 쓴 상태가 남지 않는다."""
    setup_chain(fake)
    real = jobs.store_facts

    def half_write_then_fail(session, rcept_no, payload):
        if rcept_no == "20250117000380":
            row = session.get(Disclosure, rcept_no)
            row.structured_facts = {"half": "written"}
            session.flush()
            raise ValueError("저장 도중 오류")
        return real(session, rcept_no, payload)

    monkeypatch.setattr(jobs, "store_facts", half_write_then_fail)
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.facts == 1 and out.handed_off == 4 and any("20250117000380" in p for p in out.problems)
    assert disclosure("20250117000380")["facts"] is None  # {"half": "written"}가 남지 않는다
    assert disclosure("20241031000530")["facts"]


def test_several_item_api_rows_for_one_receipt_are_not_resolved_by_picking_the_first(db, fake):
    """같은 접수번호에 항목 API 행이 둘이면 첫 행(또는 마지막 행)을 고르지 않고 모든 사실을 NEEDS_CHECK로 둔다 (collector.md 4-5)."""
    setup_chain(fake)
    fake.items[("piicDecsn", CORP, "20241031")] = [dict(RIGHTS_1031), {**RIGHTS_1031, "nstk_ostk_cnt": "1,000"}]
    out = run(collect_stock(fake.client(), TARGET, today=TODAY))
    assert out.ok and out.handed_off == 4
    assert any("20241031000530" in p and "행 2개" in p for p in out.problems), out.problems
    facts = disclosure("20241031000530")["facts"]["facts"]
    assert facts and all(f["status"] == "NEEDS_CHECK" and f["value"] is None and f["raw"] is None for f in facts)
    assert "행이 2개" in facts[0]["reason"]
    assert StructuredFacts.model_validate(disclosure("20241031000530")["facts"]).rcept_no == "20241031000530"
    # 다른 접수번호의 정상 사실은 영향이 없다
    assert {f["name"]: f for f in disclosure("20250117000380")["facts"]["facts"]}["new_common_shares"]["status"] == "MISSING"
