"""진짜 모드에서 Graph(MCP 서버·모델)에 붙지 못할 때 — 화면이 500으로 깨지지 않고 DB 내용은 계속 보여야 한다."""
import pytest
from sqlalchemy import select

from api.graph_adapter import FakeGraphAdapter, get_graph_adapter, now_iso
from api.main import app
from api.routes.alerts import GRAPH_DOWN
from shared.models import AuditLog, RiskAssessment
from tests.api.helpers import RCEPT, read_events

LOW_RCEPT = "20240426000001"


class GraphDown(FakeGraphAdapter):
    """MCP 서버가 꺼져 Dependencies 조립이 실패하는 상황 (RealGraphAdapter.get_state가 올리는 예외와 같은 모양)."""

    async def get_state(self, rcept_no):
        raise RuntimeError("agent.Dependencies 조립 실패 (ExceptionGroup): business-mcp(8001)·rag-mcp(8002)를 확인하세요")


@pytest.fixture
def down_client(client, session_factory):
    with session_factory() as s:
        for rcept, level in ((RCEPT, "Critical"), (LOW_RCEPT, "Low")):
            s.add(RiskAssessment(corp_code="X", rcept_no=rcept, total_score=1, level=level,
                                 reasons=[{"group": "A", "item": "감사의견", "fixed_level": "Critical"}],
                                 status="WAITING_APPROVAL" if level == "Critical" else "DONE", created_at=now_iso()))
        s.commit()
    adapter = GraphDown(delay=0, session_factory=session_factory)
    app.dependency_overrides[get_graph_adapter] = lambda: adapter
    return client


def critical_id(session_factory):
    with session_factory() as s:
        return s.scalar(select(RiskAssessment.assessment_id).where(RiskAssessment.rcept_no == RCEPT))


def test_alert_list_still_works(down_client, auth_headers):
    res = down_client.get("/alerts?level=Critical&level=Low", headers=auth_headers)
    assert res.status_code == 200
    rows = {r["rcept_no"]: r for r in res.json()}
    assert rows[RCEPT]["graph_error"] == GRAPH_DOWN and rows[RCEPT]["affected_count"] == 0
    assert rows[LOW_RCEPT]["graph_error"] is None  # Low는 원래 Graph를 보지 않는다


def test_alert_detail_shows_db_part(down_client, auth_headers, session_factory):
    res = down_client.get(f"/alerts/{critical_id(session_factory)}", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["graph_error"] == GRAPH_DOWN and body["graph_state_available"] is False
    assert body["level"] == "Critical" and body["reasons"]  # 점수 내역은 DB에서 그대로
    assert body["customers"] == []


def test_drafts_and_decision_are_503_and_not_recorded(down_client, auth_headers, session_factory):
    aid = critical_id(session_factory)
    res = down_client.get(f"/alerts/{aid}/drafts", headers=auth_headers)
    assert res.status_code == 503 and res.json()["detail"] == GRAPH_DOWN
    res = down_client.post(f"/alerts/{aid}/decision", headers=auth_headers,
                           json={"decisions": [{"customer_id": "C001", "action": "approve"}]})
    assert res.status_code == 503
    with session_factory() as s:
        assert not s.scalars(select(AuditLog)).all()


def test_precheck_connection_error_is_503(client, auth_headers, adapter, session_factory):
    """상태는 읽혔는데 결정 검사 중 연결이 끊긴 경우도 500이 아니라 503, 결정은 기록하지 않는다."""
    client.post("/runs", json={"rcept_no": RCEPT}, headers=auth_headers)
    aid = read_events(client, auth_headers)[-1][1]["assessment_id"]

    async def broken_precheck(rcept_no, decision):
        raise ConnectionError("business-mcp 응답 없음")

    adapter.precheck = broken_precheck
    res = client.post(f"/alerts/{aid}/decision", headers=auth_headers,
                      json={"decisions": [{"customer_id": "C001", "action": "approve"}]})
    assert res.status_code == 503 and res.json()["detail"] == GRAPH_DOWN
    with session_factory() as s:
        assert not s.scalars(select(AuditLog)).all()
