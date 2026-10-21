"""GET /alerts/{id}의 summary — 경보 상단 '이번 공시의 영향' 요약 (점수 내역에서 규칙으로 만든다)."""
import pytest

from shared.models import Company, RiskAssessment

PREV, NOW = "20250117000380", "20250321802001"


def _row(group, item, rcept, points, **kw):
    return {"group": group, "item": item, "signal_type": kw.pop("signal_type", item), "rcept_no": rcept,
            "points": points, "weight": 1.0, "evidence": [], **kw}


@pytest.fixture
def world(session_factory):
    with session_factory() as s:
        s.add(Company(corp_code="C1", stock_code="001570", name="금양", market="KOSPI", base_score=1, base_reasons=[]))
        s.add(RiskAssessment(assessment_id=1, corp_code="C1", rcept_no=PREV, total_score=5, level="Medium", status="DONE",
                             created_at="2025-01-17T09:00:00+09:00", reasons=[]))
        s.add(RiskAssessment(
            assessment_id=2, corp_code="C1", rcept_no=NOW, total_score=7, level="High", status="WAITING_APPROVAL",
            created_at="2025-02-01T09:00:00+09:00",
            reasons=[_row("BASE", "사업보고서 기본 점수", None, 1.0),
                     _row("B", "최대주주 주식 담보", "OLD", 2.0, from_history=True),
                     _row("B", "유상증자", NOW, 4.0, signal_type="RIGHTS_OFFERING", quote="신주의 종류와 수 보통주식")]))
        s.add(RiskAssessment(
            assessment_id=3, corp_code="C1", rcept_no="20250321802002", total_score=7, level="Critical", status="WAITING_APPROVAL",
            created_at="2025-03-21T09:00:00+09:00",
            reasons=[_row("A", "감사의견 거절", "20250321802002", 0, signal_type="AUDIT_OPINION", fixed_level="Critical",
                          quote="가. 감사의견 의견거절")]))
        s.commit()


def summary(client, headers, aid):
    res = client.get(f"/alerts/{aid}", headers=headers)
    assert res.status_code == 200
    return res.json()["summary"]


def test_hidden_signal_summary_compares_with_the_previous_assessment(client, auth_headers, world):
    sm = summary(client, auth_headers, 2)
    assert sm["previous"] == {"assessment_id": 1, "level": "Medium", "total_score": 5}
    assert sm["score_change"] == 2 and sm["carried_score"] == 3.0       # 7점 = 이번 공시 4점 + 기본·과거 3점
    assert sm["this_filing"]["points"] == 4.0 and sm["this_filing"]["fixed_level"] is None
    assert [i["item"] for i in sm["this_filing"]["items"]] == ["유상증자"]  # 과거·기본 점수는 이번 공시 항목이 아니다
    assert "+4.0점" in sm["headline"] and "Medium → High" in sm["headline"]


def test_confirmed_bad_news_is_headlined_as_a_fixed_level(client, auth_headers, world):
    sm = summary(client, auth_headers, 3)
    assert sm["this_filing"]["fixed_level"] == "Critical" and "확정 악재" in sm["headline"] and "Critical로 고정" in sm["headline"]
    assert sm["this_filing"]["items"][0]["quote"] == "가. 감사의견 의견거절"
    assert sm["previous"]["assessment_id"] == 2 and "High → Critical" in sm["headline"]


def test_first_assessment_has_no_previous(client, auth_headers, world):
    sm = summary(client, auth_headers, 1)
    assert sm["previous"] is None and sm["score_change"] is None
    assert "추가된 위험 신호는 없습니다" in sm["headline"]
