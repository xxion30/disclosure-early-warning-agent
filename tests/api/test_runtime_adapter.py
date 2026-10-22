"""RealGraphAdapter를 진짜 AgentRuntime(agent/)으로 검증한다. (담당: R4)

MCP·LLM·Harness는 Agent 테스트용 가짜(tests/agent/conftest.py)를 쓰고, 저장소만 API 테스트 DB에 쓰도록 바꾼다.
- 공시 1건: 분석 → 설명 → 저장 → done (승인 대기는 삭제됐다)
- 서버를 재시작해도 같은 thread_id의 결과가 그대로이고 다시 분석하지 않는다
- Dependencies가 없으면 failed 이벤트
"""
import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import api.main
from agent.ports import Dependencies
from api.deps import get_db
from api.graph_adapter import RealGraphAdapter, get_graph_adapter, now_iso
from api.main import app
from api.run_manager import RunManager, get_run_manager
from shared.models import AuditLog, RiskAssessment
from tests.agent.conftest import RCEPT as AGENT_RCEPT
from tests.agent.conftest import FakeHarness, FakeModel, FakeTools
from tests.api.helpers import RCEPT, login, read_events


class Tools(FakeTools):
    """A의 가짜 도구. 접수번호만 API 형식(14자리)으로 바꾼다."""

    async def call(self, name, arguments):
        res = await super().call(name, arguments)
        if res.ok:
            res.data = json.loads(json.dumps(res.data, ensure_ascii=False).replace(AGENT_RCEPT, RCEPT))
        return res


class Model(FakeModel):
    """A의 가짜 LLM. 인용 근거의 접수번호도 API 형식으로 바꾼다."""

    async def generate(self, prompt, schema, payload):
        out = await super().generate(prompt, schema, payload)
        return schema.model_validate_json(out.model_dump_json().replace(AGENT_RCEPT, RCEPT))


class DbStore:
    """Persistence를 API 테스트 DB에 쓴다 (진짜는 business-mcp의 save_assessment 몫)."""

    def __init__(self, session_factory):
        self.sf = session_factory

    async def save(self, state, status):
        rcept_no = state["disclosure"]["rcept_no"]
        score = state.get("score_result") or {}
        with self.sf() as s:
            row = s.scalar(select(RiskAssessment).where(RiskAssessment.rcept_no == rcept_no))
            if row is None:
                row = RiskAssessment(rcept_no=rcept_no, created_at=now_iso())
                s.add(row)
            row.corp_code = state["disclosure"].get("corp_code") or "UNKNOWN"
            row.total_score = score.get("total_score", 0)
            row.level = state.get("risk_level") or score.get("level") or "Low"
            row.combo_upgraded = bool(score.get("combo_upgraded"))
            row.reasons = score.get("breakdown", [])
            row.ai_explanation = state.get("ai_explanation")
            row.status = status
            s.commit()
            return row.assessment_id

    async def audit(self, state, action, payload):
        with self.sf() as s:
            s.add(AuditLog(actor="agent", action=action, target=state["disclosure"]["rcept_no"],
                           payload=payload, trace_id=state.get("trace_id"), created_at=now_iso()))
            s.commit()


@pytest.fixture
def kit(session_factory):
    tools, model, harness = Tools(), Model(), FakeHarness()
    store = DbStore(session_factory)
    deps = Dependencies(tools, model, harness, store,
                        classify=lambda title: "AUDIT" if "감사" in title else None,
                        signal_input=lambda extracted: [
                            {"signal_type": f["signal_type"], "detail": f["values"]} for f in extracted["facts"]])
    return deps, model


@pytest.fixture(autouse=True)
def no_real_db(monkeypatch):
    monkeypatch.setattr(api.main, "init_db", lambda: None)


@contextmanager
def server(session_factory, deps, checkpoint_path):
    """API 서버 1번 실행에 해당. with를 빠져나가면 서버가 꺼진 것과 같다."""
    adapter = RealGraphAdapter(deps=deps, checkpoint_path=checkpoint_path)
    manager = RunManager(adapter)

    def override_get_db():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_graph_adapter] = lambda: adapter
    app.dependency_overrides[get_run_manager] = lambda: manager
    try:
        with TestClient(app) as c:
            try:
                yield c
            finally:
                c.portal.call(adapter.aclose)  # 테스트가 실패해도 체크포인트 연결을 같은 루프에서 닫는다
    finally:
        app.dependency_overrides.clear()


def headers(client, email="p01@demo.local"):
    return {"Authorization": f"Bearer {login(client, email).json()['access_token']}"}


def start(client, h):
    assert client.post("/runs", json={"rcept_no": RCEPT}, headers=h).status_code == 202
    return read_events(client, h)


def test_run_with_agent_runtime_ends_done_and_survives_restart(session_factory, kit, tmp_path):
    deps, model = kit
    cp = tmp_path / "checkpoints.db"

    # ---- 서버 1: 실행 → done ----
    with server(session_factory, deps, cp) as c:
        h1 = headers(c)
        events = start(c, h1)
        names = [e for e, _ in events]
        assert names[-1] == "done" and "waiting_approval" not in names
        finished = [d["node"] for e, d in events if e == "node_finished"]
        assert finished[:3] == ["receive", "classify", "extract_facts"] and "explain_risk" in finished
        aid = events[-1][1]["assessment_id"]
        detail = c.get(f"/alerts/{aid}", headers=h1).json()
        assert detail["status"] == "DONE" and detail["level"] == "Critical" and detail["evidence"]
        assert "customers" not in detail  # 고객 연락 관련 필드는 삭제됐다
        calls_before = len(model.calls)

    # ---- 서버 2: 재시작 — 같은 thread_id의 결과가 그대로이고 다시 분석하지 않는다 ----
    with server(session_factory, deps, cp) as c:
        h1 = headers(c)
        detail = c.get(f"/alerts/{aid}", headers=h1).json()
        assert detail["graph_state_available"] is True and detail["status"] == "DONE"
    assert len(model.calls) == calls_before
    with session_factory() as s:
        assert len(s.scalars(select(RiskAssessment)).all()) == 1


def test_missing_dependencies_is_failed_event(session_factory, tmp_path, monkeypatch, request):
    # 이 노트북에서 MCP 서버(8001·8002)가 돌고 있어도 "서버가 없을 때"를 시험하도록 안 쓰는 포트로 고정한다
    from shared.config import get_settings

    monkeypatch.setenv("BUSINESS_MCP_URL", "http://127.0.0.1:9/mcp")
    monkeypatch.setenv("RAG_MCP_URL", "http://127.0.0.1:9/mcp")
    get_settings.cache_clear()
    request.addfinalizer(get_settings.cache_clear)  # 다른 테스트에 바뀐 설정이 남지 않게
    with server(session_factory, None, tmp_path / "cp.db") as c:
        h1 = headers(c)
        name, data = start(c, h1)[-1]
        assert name == "failed" and "Dependencies" in data["message"]
