"""GET /customers — 내 담당 고객 현황 (읽기 전용). 메모리 DB에 회사·보유·경보·업무를 직접 넣어 확인한다."""
import json

import pytest

from shared.config import ROOT_DIR
from shared.models import AuditLog, Company, ContactTask, Holding, RiskAssessment

SEED = json.loads((ROOT_DIR / "data" / "seed" / "customers.json").read_text(encoding="utf-8"))
OWN = {"P01": {c["customer_id"] for c in SEED["customers"] if c["pb_id"] == "P01"},
       "P02": {c["customer_id"] for c in SEED["customers"] if c["pb_id"] == "P02"}}
NOW = "2025-03-21T10:00:00+09:00"


@pytest.fixture
def world(session_factory):
    """금양(Critical 경보)·CGV(Low만) + customers.json의 실제 보유 종목."""
    with session_factory() as s:
        s.add(Company(corp_code="00106119", stock_code="001570", name="금양", market="KOSPI", base_score=1, base_reasons=[]))
        s.add(Company(corp_code="00303873", stock_code="079160", name="CJ CGV", market="KOSPI", base_score=2, base_reasons=[]))
        for h in SEED["holdings"]:
            s.add(Holding(customer_id=h["customer_id"], stock_code=h["stock_code"], eval_amount=h["eval_amount"], on_credit=h["on_credit"]))
        s.add(RiskAssessment(assessment_id=1, corp_code="00106119", rcept_no="20250117000380", total_score=8, level="High",
                             status="DONE", created_at="2025-01-17T09:00:00+09:00", reasons=[]))
        s.add(RiskAssessment(assessment_id=2, corp_code="00106119", rcept_no="20250321802001", total_score=8, level="Critical",
                             status="WAITING_APPROVAL", created_at=NOW, reasons=[]))
        s.add(RiskAssessment(assessment_id=3, corp_code="00303873", rcept_no="20230620000238", total_score=5, level="Medium",
                             status="DONE", created_at=NOW, reasons=[]))
        s.commit()
    return session_factory


def by_id(res):
    return {c["customer_id"]: c for c in res.json()["customers"]}


def test_requires_login(client):
    assert client.get("/customers").status_code == 401


def test_only_my_customers_are_returned(client, auth_headers, p02_headers, world):
    p01 = by_id(client.get("/customers", headers=auth_headers))
    p02 = by_id(client.get("/customers", headers=p02_headers))
    assert set(p01) == OWN["P01"] and set(p02) == OWN["P02"]
    assert not set(p01) & set(p02)
    assert all(c["name"] and not c["name"].startswith("고객 ") for c in p01.values())  # 내 고객은 실명


def test_exposure_matches_the_alert_level(client, auth_headers, p02_headers, world):
    p01 = by_id(client.get("/customers", headers=auth_headers))
    # 금양 Critical → 보유 고객 전원(C001~C007)이 Critical 노출. 영향도(P1~P3)와 점수는 없다
    for cid in ("C001", "C003", "C005", "C007"):
        assert p01[cid]["exposure"]["top_level"] == "Critical", cid
    p02 = by_id(client.get("/customers", headers=p02_headers))
    for cid in ("C002", "C004", "C006"):
        assert p02[cid]["exposure"]["top_level"] == "Critical", cid
    assert "top_priority" not in p01["C001"]["exposure"]
    # 경보와 연결: 가장 최근의 High·Critical 평가(2번)를 가리킨다
    h = next(x for x in p01["C001"]["holdings"] if x["stock_code"] == "001570")
    assert h["alert"]["assessment_id"] == 2 and h["alert"]["level"] == "Critical" and h["stock_name"] == "금양"
    assert h["ratio_pct"] == 30.0  # 9천만 ÷ 3억


def test_non_alert_holdings_have_no_alert_and_known_names(client, auth_headers, world):
    c1 = by_id(client.get("/customers", headers=auth_headers))["C001"]
    other = [h for h in c1["holdings"] if h["stock_code"] != "001570"]
    assert other and all(h["alert"] is None for h in other)
    assert {h["stock_code"]: h["stock_name"] for h in c1["holdings"]}["005930"] == "삼성전자"  # DB에 회사가 없어도 이름이 나온다
    # CGV는 Medium 평가뿐이라 경보가 아니다
    p02 = by_id(client.get("/customers", headers=auth_headers))
    cgv_holders = [c for c in p02.values() if any(h["stock_code"] == "079160" for h in c["holdings"])]
    assert all(h["alert"] is None for c in cgv_holders for h in c["holdings"] if h["stock_code"] == "079160")


def test_sorted_by_urgency_and_summary(client, auth_headers, world):
    res = client.get("/customers", headers=auth_headers).json()
    order = [c["exposure"]["top_level"] for c in res["customers"]]
    rank = {"Critical": 0, "High": 1, None: 9}
    assert [rank[o] for o in order] == sorted(rank[o] for o in order)  # Critical → High → 노출 없음
    s = res["summary"]
    assert s["customers"] == len(OWN["P01"]) and s["exposed"] >= s["critical"] + s["high"] >= 1


def test_contact_history_shows_tasks_and_rejections(client, auth_headers, p02_headers, world):
    with world() as s:
        s.add(ContactTask(assessment_id=2, customer_id="C001", pb_id="P01",
                          draft_message="{고객명}님, 공시 안내드립니다.", status="연락완료", result_note="통화 완료",
                          created_at="2025-03-21T11:00:00+09:00", updated_at="2025-03-22T09:00:00+09:00"))
        s.add(AuditLog(actor="P02", action="decision.reject", target="assessment:2/customer:C002",
                       payload={"customer_id": "C002", "reject_reason": "고객 요청으로 보류", "decided_at": "2025-03-21T12:00:00+09:00"},
                       created_at="2025-03-21T12:00:00+09:00"))
        s.commit()
    c1 = by_id(client.get("/customers", headers=auth_headers))["C001"]
    t = c1["history"][0]
    assert (t["kind"], t["status"], t["corp_name"], t["note"]) == ("task", "연락완료", "금양", "통화 완료")
    assert "한지우" in t["message_preview"] and "{고객명}" not in t["message_preview"]  # 내 고객이라 실명으로 채워 보여 준다
    assert c1["last_contact"]["status"] == "연락완료"
    c2 = by_id(client.get("/customers", headers=p02_headers))["C002"]
    assert (c2["history"][0]["kind"], c2["history"][0]["status"], c2["history"][0]["note"]) == ("reject", "반려", "고객 요청으로 보류")
    # 반려 기록은 담당 PB 화면에만 나온다
    assert all(x["kind"] != "reject" for c in by_id(client.get("/customers", headers=auth_headers)).values() for x in c["history"])


def test_other_pbs_history_never_leaks(client, auth_headers, world):
    with world() as s:
        s.add(ContactTask(assessment_id=2, customer_id="C002", pb_id="P02",
                          draft_message="{고객명}님", status="승인", created_at=NOW, updated_at=NOW))
        s.commit()
    body = client.get("/customers", headers=auth_headers).text
    assert "C002" not in body and "오민재" not in body


def test_read_only(client, auth_headers):
    for method in ("post", "put", "patch", "delete"):
        assert getattr(client, method)("/customers", headers=auth_headers).status_code == 405


def test_empty_when_no_data(client, auth_headers):
    res = client.get("/customers", headers=auth_headers).json()  # 보유·경보가 없어도 깨지지 않는다
    assert res["summary"]["exposed"] == 0 and all(c["holdings"] == [] and c["history"] == [] for c in res["customers"])


def test_dashboard_alerts_are_sorted_by_priority_without_scores(client, auth_headers, world):
    rows = client.get("/alerts?alert=true", headers=auth_headers).json()
    assert [r["level"] for r in rows] == ["Critical", "High"]  # Medium은 알림이 아니다, 높은 등급이 위
    assert all("total_score" not in r and "p1_count" not in r for r in rows)
