"""business-mcp 도구 테스트 (임시 DB + 실제 데모 데이터)."""
import asyncio

import pytest

from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.create_contact_task import create_contact_task
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_holders import get_holders
from mcp_servers.business.tools.get_signal_history import get_signal_history
from mcp_servers.business.tools.list_new_disclosures import list_new_disclosures
from mcp_servers.business.tools.save_assessment import save_assessment
from mcp_servers.business.tools.update_task_status import update_task_status

pytestmark = pytest.mark.usefixtures("seeded_db")

KY_CORP, KY_STOCK, KY_AUDIT = "00106119", "001570", "20250321802001"  # 금양, 감사의견 거절 공시
OS_CORP = "00341916"  # 오스템임플란트 (기본 점수 0)


def assert_err(res, code):
    assert res["ok"] is False and res["error"]["code"] == code, res


def test_get_disclosure_ok_and_errors():
    res = get_disclosure(KY_AUDIT)
    assert res["ok"] and res["data"]["corp_name"] == "금양" and "의견거절" in res["data"]["body_text"]
    assert_err(get_disclosure("99999999999999"), "NOT_FOUND")
    assert_err(get_disclosure("abc"), "INVALID_INPUT")


def test_list_new_disclosures():
    res = list_new_disclosures("2025-03-01")
    assert res["ok"] and res["data"]["count"] > 0
    dates = [d["filed_at"] for d in res["data"]["disclosures"]]
    assert dates == sorted(dates) and dates[0] >= "2025-03-01"
    assert "body_text" not in res["data"]["disclosures"][0]
    assert_err(list_new_disclosures("어제"), "INVALID_INPUT")


def test_company_profile():
    res = get_company_profile(KY_CORP)
    assert res["ok"] and res["data"]["stock_code"] == KY_STOCK
    assert_err(get_company_profile("nope"), "NOT_FOUND")


def test_holders_are_masked():
    res = get_holders(KY_STOCK)
    assert res["ok"] and res["data"]["count"] == 7
    assert all(h["name"] == f"고객 {h['customer_id']}" for h in res["data"]["holders"])
    assert "한지우" not in str(res)
    assert_err(get_holders("12"), "INVALID_INPUT")


def test_calculate_risk_score_audit_rejection_has_quote():
    res = calculate_risk_score(
        KY_CORP,
        [{"signal_type": "AUDIT_OPINION", "detail": {"opinion": "의견거절", "quote": "감사의견 의견거절"}, "rcept_no": KY_AUDIT, "filed_at": "2025-03-21"}],
        "2025-03-21",
    )
    d = res["data"]
    assert d["stage"] == "YELLOW" and d["yellow_score"] == 5
    assert any(b.get("quote") for b in d["breakdown"])


def test_calculate_risk_score_errors():
    assert_err(calculate_risk_score("nope", [], "2025-03-21"), "NOT_FOUND")
    assert_err(calculate_risk_score(KY_CORP, [{"signal_type": "X"}], "2025-03-21"), "INVALID_INPUT")
    assert_err(calculate_risk_score(KY_CORP, [], "bad"), "INVALID_INPUT")


def _score_and_save(status="WAITING_APPROVAL"):
    sc = calculate_risk_score(
        KY_CORP,
        [{"signal_type": "AUDIT_OPINION", "detail": {"opinion": "의견거절", "quote": "의견거절"}, "rcept_no": KY_AUDIT, "filed_at": "2025-03-21"}],
        "2025-03-21",
    )["data"]
    return save_assessment(KY_CORP, KY_AUDIT, sc["signals"], sc, "설명", status)


def test_phase2_signals_are_not_scored():
    # Former FILING_DELAY/PLEDGE rules are explicitly outside Phase 1.
    res = calculate_risk_score(KY_CORP, [{"signal_type": "PLEDGE", "rcept_no": KY_AUDIT}], "2025-03-21")
    assert_err(res, "INVALID_INPUT")


def test_save_assessment_upsert_and_validation():
    first = _score_and_save()
    assert first["ok"] and first["data"]["created"] is True
    aid = first["data"]["assessment_id"]
    # 상태만 바꾸기 (WAITING_APPROVAL → DONE): 행이 늘지 않고 기존 값은 유지
    again = save_assessment(KY_CORP, KY_AUDIT, status="DONE")
    assert again["ok"] and again["data"]["assessment_id"] == aid and again["data"]["created"] is False
    assert again["data"]["stage"] == "YELLOW"
    # 공시는 처리됨 표시
    assert get_disclosure(KY_AUDIT)["data"]["processed"] is True
    assert_err(save_assessment(KY_CORP, KY_AUDIT, status="BAD"), "INVALID_INPUT")
    assert_err(save_assessment("nope", KY_AUDIT, [], {"total_score": 0, "level": "Low"}, None, "DONE"), "NOT_FOUND")
    assert_err(save_assessment(KY_CORP, "99999999999999", [], {"total_score": 0, "level": "Low"}, None, "DONE"), "NOT_FOUND")


def test_save_assessment_first_save_requires_score():
    assert_err(save_assessment(KY_CORP, KY_AUDIT, status="WAITING_APPROVAL"), "INVALID_INPUT")


def test_create_task_and_status_flow():
    aid = _score_and_save()["data"]["assessment_id"]
    t = create_contact_task(aid, "C001", "안녕하세요 {고객명}님, 수정한 문장입니다.")
    assert t["ok"] and t["data"]["created"] and t["data"]["status"] == "승인"
    # 멱등: 같은 고객으로 다시 불러도 새로 만들지 않는다
    t2 = create_contact_task(aid, "C001", "다른 문장")
    assert t2["data"]["task_id"] == t["data"]["task_id"] and t2["data"]["created"] is False
    # 보유 고객이면 누구든 만들 수 있다(영향도 없음). 미보유 고객, 빈 문장은 거부
    assert create_contact_task(aid, "C002", "x")["data"]["created"]
    assert_err(create_contact_task(aid, "C020", "x"), "INVALID_INPUT")
    assert_err(create_contact_task(aid, "C001", "  "), "INVALID_INPUT")
    assert_err(create_contact_task(999, "C001", "x"), "NOT_FOUND")
    assert_err(create_contact_task(aid, "C999", "x"), "NOT_FOUND")

    tid = t["data"]["task_id"]
    done = update_task_status(tid, "연락완료", "통화함")
    assert done["ok"] and done["data"]["status"] == "연락완료" and done["data"]["result_note"] == "통화함"
    assert_err(update_task_status(tid, "보류"), "INVALID_INPUT")  # 연락완료 → 보류는 불가
    assert_err(update_task_status(tid, "승인"), "INVALID_INPUT")
    assert_err(update_task_status(tid, "엉뚱"), "INVALID_INPUT")
    assert_err(update_task_status(12345, "보류"), "NOT_FOUND")


def test_create_task_blocked_unless_waiting_or_done():
    aid = _score_and_save(status="RUNNING")["data"]["assessment_id"]
    assert_err(create_contact_task(aid, "C001", "x"), "INVALID_INPUT")


def test_server_registers_all_ten_tools():
    from fastmcp import Client

    from mcp_servers.business.server import mcp

    async def run():
        async with Client(mcp) as c:
            names = {t.name for t in await c.list_tools()}
            res = await c.call_tool("get_holders", {"stock_code": KY_STOCK})
            return names, res.data

    names, data = asyncio.run(run())
    assert names == {
        "list_new_disclosures", "get_disclosure", "get_disclosure_facts", "get_company_profile", "get_signal_history",
        "calculate_risk_score", "get_holders", "get_risk_state", "save_risk_state",
        "save_assessment", "create_contact_task", "update_task_status", "purge_disclosure_text",
        'verify_financial_evidence',
    }
    assert data["ok"] is True and data["data"]["count"] == 7


def test_embezzlement_has_no_amount_threshold():
    from risk_engine.stages import calculate
    for ratio in (0, 1, 3.5):
        res = calculate(new_signals=[{"signal_type": "EMBEZZLEMENT", "rcept_no": KY_AUDIT,
                        "detail": {"ratio_to_equity_pct": ratio}}], as_of="2025-03-21")
        assert res["stage"] == "RED"


def test_failure_can_be_saved_before_scoring():
    res = save_assessment(KY_CORP, KY_AUDIT, status="MANUAL_CHECK")
    assert res["ok"] and res["data"]["created"] is True
    # 같은 공시를 이후 정상 저장하면 같은 행을 갱신한다
    sc = calculate_risk_score(KY_CORP, [{"signal_type": "AUDIT_OPINION", "rcept_no": KY_AUDIT, "detail": {"opinion": "의견거절"}}], "2025-03-21")["data"]
    again = save_assessment(KY_CORP, KY_AUDIT, sc["signals"], sc, "x", "WAITING_APPROVAL")
    assert again["data"]["assessment_id"] == res["data"]["assessment_id"] and again["data"]["stage"] == "YELLOW"


CGV_CORP = "00303873"
CGV_CB_FIRST, CGV_CB_SECOND = "20220616000004", "20220708000007"  # 둘 다 [기재정정]주요사항보고서(전환사채권발행결정)


def test_correction_is_detected_from_disclosure_title_not_from_ai():
    def score(rcept, filed):
        # AI가 is_correction=false라고 주장해도 제목으로 판단한다
        return calculate_risk_score(CGV_CORP, [{"signal_type": "CB_BW", "detail": {"is_correction": False}, "rcept_no": rcept, "filed_at": filed}], filed)["data"]

    first = score(CGV_CB_FIRST, "2022-06-16")
    assert first["manual_check"] is True
    assert first["signals"] == []  # unknown original event must not become another financing
    assert first["orange_score"] == 0
    second = score(CGV_CB_SECOND, "2022-07-08")
    assert second["manual_check"] is True and second["orange_score"] == 0


def test_tool_annotations_mark_mutating_tools():
    """조회 도구는 readOnlyHint, 데이터를 바꾸는 도구 3개만 destructiveHint (수업 자료의 Harness 승인 규칙)."""
    from fastmcp import Client

    from mcp_servers.business.server import mcp

    async def run():
        async with Client(mcp) as c:
            return {t.name: t.annotations for t in await c.list_tools()}

    ann = asyncio.run(run())
    mutating = {n for n, a in ann.items() if a and a.destructiveHint}
    assert mutating == {"save_risk_state", "save_assessment", "create_contact_task", "update_task_status", "purge_disclosure_text"}
    assert all(a.readOnlyHint for n, a in ann.items() if n not in mutating)
