"""business-mcp 도구 테스트 (임시 DB + 실제 데모 데이터)."""
import asyncio

import pytest

from mcp_servers.business.tools.calculate_risk_score import calculate_risk_score
from mcp_servers.business.tools.get_company_profile import get_company_profile
from mcp_servers.business.tools.get_disclosure import get_disclosure
from mcp_servers.business.tools.get_holders import get_holders
from mcp_servers.business.tools.get_signal_history import get_signal_history
from mcp_servers.business.tools.list_new_disclosures import list_new_disclosures
from mcp_servers.business.tools.save_assessment import save_assessment

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
    assert d["level"] == "Critical"
    assert any(b.get("quote") for b in d["breakdown"])


def test_calculate_risk_score_errors():
    assert_err(calculate_risk_score("nope", [], "2025-03-21"), "NOT_FOUND")
    assert_err(calculate_risk_score(KY_CORP, [{"signal_type": "X"}], "2025-03-21"), "INVALID_INPUT")
    assert_err(calculate_risk_score(KY_CORP, [], "bad"), "INVALID_INPUT")


def _score_and_save(status="RUNNING"):
    sc = calculate_risk_score(
        KY_CORP,
        [{"signal_type": "AUDIT_OPINION", "detail": {"opinion": "의견거절", "quote": "의견거절"}, "rcept_no": KY_AUDIT, "filed_at": "2025-03-21"}],
        "2025-03-21",
    )["data"]
    return save_assessment(KY_CORP, KY_AUDIT, sc["signals"], sc, "설명", status)


def test_signals_feed_history_and_combo_across_calls():
    # 기본 점수가 0인 오스템임플란트로, 신호 3종을 차례로 저장하면 다음 계산에서 이력으로 쓰이고 조합 규칙이 켜진다.
    seq = [("20240430000001", "2024-04-30", "FILING_DELAY", {}), ("20240601000001", "2024-06-01", "PLEDGE", {"pledge_ratio_pct": 60})]
    from shared.db import get_session
    from shared.models import Disclosure

    with get_session() as s:
        for rcept, filed, *_ in seq:
            s.add(Disclosure(rcept_no=rcept, corp_code=OS_CORP, title="t", filed_at=filed, processed=False))
    for rcept, filed, st, detail in seq:
        ev = [{"source": "s", "quote": "최대주주 주식담보 추가 설정", "rcept_no": rcept, "location": "chunk-0"}]  # 담보는 신규·추가 설정 표현이 있어야 점수가 붙는다
        sc = calculate_risk_score(OS_CORP, [{"signal_type": st, "detail": detail, "rcept_no": rcept, "filed_at": filed, "evidence": ev}], filed)["data"]
        assert save_assessment(OS_CORP, rcept, sc["signals"], sc, None, "DONE")["ok"]
    hist = get_signal_history(OS_CORP, 12, "2024-07-01")["data"]["signals"]
    assert [h["signal_type"] for h in hist] == ["FILING_DELAY", "PLEDGE"]
    # 세 번째 서로 다른 유형 → 합계 4+3+2=9(High) → 조합 규칙으로 한 단계 올라 Critical
    third = calculate_risk_score(OS_CORP, [{"signal_type": "CB_BW", "detail": {}, "filed_at": "2024-07-01"}], "2024-07-01")["data"]
    assert third["total_score"] == 9 and third["level"] == "Critical"
    assert third["combo_upgraded"] is True and third["level"] == "Critical"


def test_save_assessment_upsert_and_validation():
    first = _score_and_save()
    assert first["ok"] and first["data"]["created"] is True
    aid = first["data"]["assessment_id"]
    # 상태만 바꾸기 (RUNNING → DONE): 행이 늘지 않고 기존 값은 유지
    again = save_assessment(KY_CORP, KY_AUDIT, status="DONE")
    assert again["ok"] and again["data"]["assessment_id"] == aid and again["data"]["created"] is False
    assert again["data"]["level"] == "Critical"
    # 공시는 처리됨 표시
    assert get_disclosure(KY_AUDIT)["data"]["processed"] is True
    assert_err(save_assessment(KY_CORP, KY_AUDIT, status="BAD"), "INVALID_INPUT")
    assert_err(save_assessment("nope", KY_AUDIT, [], {"total_score": 0, "level": "Low"}, None, "DONE"), "NOT_FOUND")
    assert_err(save_assessment(KY_CORP, "99999999999999", [], {"total_score": 0, "level": "Low"}, None, "DONE"), "NOT_FOUND")


def test_save_assessment_first_save_requires_score():
    assert_err(save_assessment(KY_CORP, KY_AUDIT, status="DONE"), "INVALID_INPUT")  # 점수 없이 첫 저장은 안 된다
    assert_err(save_assessment(KY_CORP, KY_AUDIT, status="WAITING_APPROVAL"), "INVALID_INPUT")  # 승인 대기 상태는 삭제됐다


def test_server_registers_all_seven_tools():
    from fastmcp import Client

    from mcp_servers.business.server import mcp

    async def run():
        async with Client(mcp) as c:
            names = {t.name for t in await c.list_tools()}
            res = await c.call_tool("get_holders", {"stock_code": KY_STOCK})
            return names, res.data

    names, data = asyncio.run(run())
    assert names == {
        "list_new_disclosures", "get_disclosure", "get_company_profile", "get_signal_history",
        "calculate_risk_score", "get_holders", "save_assessment",
    }
    assert data["ok"] is True and data["data"]["count"] == 7


def test_market_comes_from_company_profile_for_embezzlement():
    # 오스템임플란트(KOSDAQ): 자기자본 대비 3.5%는 코스닥 기준(3%) 이상 → Critical
    res = calculate_risk_score("00341916", [{"signal_type": "EMBEZZLEMENT", "detail": {"ratio_to_equity_pct": 3.5}}], "2022-01-03")
    assert res["data"]["level"] == "Critical"
    # 금양(KOSPI): 같은 3.5%는 5% 미만 → High
    res = calculate_risk_score(KY_CORP, [{"signal_type": "EMBEZZLEMENT", "detail": {"ratio_to_equity_pct": 3.5}}], "2025-03-21")
    assert res["data"]["level"] == "High"


def test_failure_can_be_saved_before_scoring():
    res = save_assessment(KY_CORP, KY_AUDIT, status="MANUAL_CHECK")
    assert res["ok"] and res["data"]["created"] is True
    # 같은 공시를 이후 정상 저장하면 같은 행을 갱신한다
    sc = calculate_risk_score(KY_CORP, [{"signal_type": "AUDIT_OPINION", "detail": {"opinion": "의견거절"}}], "2025-03-21")["data"]
    again = save_assessment(KY_CORP, KY_AUDIT, sc["signals"], sc, "x", "DONE")
    assert again["data"]["assessment_id"] == res["data"]["assessment_id"] and again["data"]["level"] == "Critical"


CGV_CORP = "00303873"
CGV_CB_FIRST, CGV_CB_SECOND = "20220616000004", "20220708000007"  # 둘 다 [기재정정]주요사항보고서(전환사채권발행결정)


def test_correction_is_detected_from_disclosure_title_not_from_ai():
    def score(rcept, filed):
        # AI가 is_correction=false라고 주장해도 제목으로 판단한다
        return calculate_risk_score(CGV_CORP, [{"signal_type": "CB_BW", "detail": {"is_correction": False}, "rcept_no": rcept, "filed_at": filed}], filed)["data"]

    first = score(CGV_CB_FIRST, "2022-06-16")
    assert first["signals"][0]["detail"]["is_correction"] is True
    assert first["total_score"] == 2 + 2  # 기본 2 + 이전 신호가 없는 정정 = 일반 신호 2
    save_assessment(CGV_CORP, CGV_CB_FIRST, first["signals"], first, None, "DONE")

    second = score(CGV_CB_SECOND, "2022-07-08")
    assert second["total_score"] == 2 + 2  # 같은 유형 정정은 점수를 다시 더하지 않는다 (이전: 8점 High였다)
    assert second["level"] == "Medium"
    assert next(b for b in second["breakdown"] if not b.get("from_history") and b["group"] == "B")["points"] == 0


def test_tool_annotations_mark_mutating_tools():
    """조회 도구는 readOnlyHint, 데이터를 바꾸는 도구(save_assessment)만 destructiveHint (수업 자료의 Harness 규칙)."""
    from fastmcp import Client

    from mcp_servers.business.server import mcp

    async def run():
        async with Client(mcp) as c:
            return {t.name: t.annotations for t in await c.list_tools()}

    ann = asyncio.run(run())
    mutating = {n for n, a in ann.items() if a and a.destructiveHint}
    assert mutating == {"save_assessment"}
    assert all(a.readOnlyHint for n, a in ann.items() if n not in mutating)
