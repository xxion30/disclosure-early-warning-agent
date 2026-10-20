"""RealGraphAdapter를 진짜 LangGraph + SQLite 체크포인트로 검증한다.

agent/graph.py(A) 대신 tests/api/contract_graph.py를 agent.graph로 끼워 넣는다.
- 시나리오 A 9~12단계가 진짜 interrupt/resume으로 도는지
- 서버를 재시작해도(어댑터·RunManager를 새로 만들어도) 같은 thread_id로 이어서 승인되는지 (9장 실패 시나리오 3)
- 노드 실패가 failed 이벤트로 오는지
"""
import sys
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import api.main
from api.deps import get_db
from api.graph_adapter import RealGraphAdapter, get_graph_adapter
from api.main import app
from api.run_manager import RunManager, get_run_manager
from shared.models import ContactTask, RiskAssessment
from tests.api import contract_graph
from tests.api.helpers import RCEPT, login, read_events

P01 = ["C001", "C003", "C005", "C007"]
EDITED = "{고객명}님, 공시 내용을 정리해 내일 직접 전화드리겠습니다."


@pytest.fixture(autouse=True)
def use_contract_graph(monkeypatch, session_factory):
    monkeypatch.setitem(sys.modules, "agent.graph", contract_graph)
    monkeypatch.setattr(contract_graph, "SESSION_FACTORY", session_factory)
    monkeypatch.setattr(api.main, "init_db", lambda: None)


@contextmanager
def server(session_factory, checkpoint_path):
    """API 서버 1번 실행에 해당. with를 빠져나가면 서버가 꺼진 것과 같다."""
    adapter = RealGraphAdapter(checkpoint_path=checkpoint_path)
    manager = RunManager(adapter)

    def override_get_db():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_graph_adapter] = lambda: adapter
    app.dependency_overrides[get_run_manager] = lambda: manager
    try:
        with TestClient(app) as c:
            yield c
            c.portal.call(adapter.aclose)
    finally:
        app.dependency_overrides.clear()


def headers(client, email):
    return {"Authorization": f"Bearer {login(client, email).json()['access_token']}"}


def decide(client, h, assessment_id, decisions):
    return client.post(f"/alerts/{assessment_id}/decision", json={"decisions": decisions}, headers=h)


def tasks(session_factory):
    with session_factory() as s:
        return {t.customer_id: t for t in s.scalars(select(ContactTask))}


def test_scenario_a_survives_server_restart(session_factory, tmp_path):
    cp = tmp_path / "checkpoints.db"

    # ---- 서버 1: 실행 → 승인 대기 → P01 결정 ----
    with server(session_factory, cp) as c:
        h1 = headers(c, "p01@demo.local")
        assert c.post("/runs", json={"rcept_no": RCEPT}, headers=h1).status_code == 202
        events = read_events(c, h1)
        nodes = [d["node"] for e, d in events if e == "node_finished"]
        assert nodes[:3] == ["receive", "classify", "extract_facts"] and "guardrail_check" in nodes
        assert "pb_approval" not in nodes  # interrupt로 멈춘 노드는 끝난 것으로 보내지 않는다
        last = events[-1]
        assert last[0] == "waiting_approval" and last[1]["affected"] == 7 and last[1]["p1"] == 5
        aid = last[1]["assessment_id"]

        alert = c.get("/alerts", headers=h1).json()[0]
        assert alert["status"] == "WAITING_APPROVAL" and alert["my_pending_count"] == 4

        res = decide(c, h1, aid, [{"customer_id": "C001", "action": "approve", "edited_draft": EDITED},
                                  *({"customer_id": x, "action": "approve"} for x in P01[1:])])
        assert res.status_code == 202, res.text
        events = read_events(c, h1)
        assert events[-1][0] == "waiting_approval"
        assert sorted(events[-1][1]["pending"]) == ["C002", "C004", "C006"]
        assert sorted(tasks(session_factory)) == P01
        assert tasks(session_factory)["C001"].draft_message == EDITED

    # ---- 서버 2: 재시작. 메모리(RunManager)는 비었지만 체크포인트에서 이어서 승인 ----
    with server(session_factory, cp) as c:
        h2 = headers(c, "p02@demo.local")
        detail = c.get(f"/alerts/{aid}", headers=h2).json()
        decided = {x["customer_id"]: x["decision"] for x in detail["customers"]}
        assert decided["C001"] == "승인" and decided["C002"] == "승인대기"

        assert decide(c, headers(c, "p01@demo.local"), aid, [{"customer_id": "C002", "action": "approve"}]).status_code == 403
        res = decide(c, h2, aid, [
            {"customer_id": "C002", "action": "reject", "reject_reason": "이미 안내받음"},
            {"customer_id": "C004", "action": "approve"},
            {"customer_id": "C006", "action": "approve"},
        ])
        assert res.status_code == 202, res.text
        assert read_events(c, h2)[-1][0] == "done"

        all_tasks = tasks(session_factory)
        assert sorted(all_tasks) == sorted(P01 + ["C004", "C006"])
        with session_factory() as s:
            assert s.get(RiskAssessment, aid).status == "DONE"
        detail = c.get(f"/alerts/{aid}", headers=h2).json()
        assert detail["status"] == "DONE" and detail["pending"] == []


def test_rewrite_goes_back_through_drafts(session_factory, tmp_path):
    with server(session_factory, tmp_path / "cp.db") as c:
        h1 = headers(c, "p01@demo.local")
        c.post("/runs", json={"rcept_no": RCEPT}, headers=h1)
        aid = read_events(c, h1)[-1][1]["assessment_id"]

        assert decide(c, h1, aid, [{"customer_id": "C003", "action": "rewrite"}]).status_code == 202
        events = read_events(c, h1)
        finished = [d["node"] for e, d in events if e == "node_finished"]
        assert finished == ["pb_approval", "create_tasks", "draft_messages", "guardrail_check"]
        assert events[-1][0] == "waiting_approval" and "C003" in events[-1][1]["pending"]
        drafts = {d["customer_id"]: d["draft"] for d in c.get(f"/alerts/{aid}/drafts", headers=h1).json()["drafts"]}
        assert "다시 작성" in drafts["C003"] and "다시 작성" not in drafts["C001"]


def test_node_error_becomes_failed_event(session_factory, tmp_path):
    rcept = "20250401000999"  # contract_graph가 receive에서 일부러 실패
    with server(session_factory, tmp_path / "cp.db") as c:
        h1 = headers(c, "p01@demo.local")
        c.post("/runs", json={"rcept_no": rcept}, headers=h1)
        events = read_events(c, h1, rcept)
        assert events[-1][0] == "failed"
        assert "TIMEOUT" in events[-1][1]["message"]
        assert c.get(f"/runs/{rcept}", headers=h1).json()["last_event"] == "failed"
