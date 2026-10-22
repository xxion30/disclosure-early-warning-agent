"""진짜 모드에서 Graph(MCP 서버·모델)에 붙지 못할 때 — 화면이 500으로 깨지지 않고 DB 내용은 계속 보여야 한다. (담당: R4)"""
import pytest
from sqlalchemy import select

from api.graph_adapter import FakeGraphAdapter, get_graph_adapter, now_iso
from api.main import app
from api.routes.alerts import GRAPH_DOWN
from shared.models import RiskAssessment
from tests.api.helpers import RCEPT

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
                                 status="DONE", created_at=now_iso()))
        s.commit()
    adapter = GraphDown(delay=0, session_factory=session_factory)
    app.dependency_overrides[get_graph_adapter] = lambda: adapter
    return client


def critical_id(session_factory):
    with session_factory() as s:
        return s.scalar(select(RiskAssessment.assessment_id).where(RiskAssessment.rcept_no == RCEPT))


def test_alert_list_does_not_need_the_graph(down_client, auth_headers):
    """목록은 DB만 읽으므로 Graph가 꺼져 있어도 그대로 보인다."""
    res = down_client.get("/alerts?level=Critical&level=Low", headers=auth_headers)
    assert res.status_code == 200
    rows = {r["rcept_no"]: r for r in res.json()}
    assert rows[RCEPT]["level"] == "Critical" and rows[LOW_RCEPT]["level"] == "Low"
    assert "graph_error" not in rows[RCEPT] and "affected_count" not in rows[RCEPT]


def test_alert_detail_shows_db_part(down_client, auth_headers, session_factory):
    res = down_client.get(f"/alerts/{critical_id(session_factory)}", headers=auth_headers)
    assert res.status_code == 200
    body = res.json()
    assert body["graph_error"] == GRAPH_DOWN and body["graph_state_available"] is False
    assert body["level"] == "Critical" and body["reasons"]  # 점수 내역은 DB에서 그대로
    assert body["summary"]["headline"]  # 상단 요약도 DB만으로 만든다
