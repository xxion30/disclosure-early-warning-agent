"""RealGraphAdapter를 A의 진짜 AgentRuntime(agent/)으로 검증한다.

MCP·LLM·Harness는 A의 테스트용 가짜(tests/agent/conftest.py)를 쓰고, 저장소만 API 테스트 DB에 쓰도록 바꾼다.
- 시나리오 A 9~12단계 (PB별 결정 묶음, 수정 후 승인, 반려, 403)
- 서버를 재시작해도 같은 thread_id로 이어서 승인 (9장 실패 시나리오 3)
- 금지 표현이 든 수정안은 202 전에 422, 가드레일 3회 실패 → "직접 작성 필요"
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
from shared.models import AuditLog, ContactTask, RiskAssessment
from tests.agent.conftest import RCEPT as AGENT_RCEPT
from tests.agent.conftest import FakeHarness, FakeModel, FakeTools
from tests.api.helpers import RCEPT, login, read_events

P01 = ["C001", "C003", "C005", "C007"]
P02 = ["C002", "C004", "C006"]
EDITED = "{고객명}님, 공시 내용을 정리해 내일 직접 전화드리겠습니다."


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
    """Persistence + create_task를 API 테스트 DB에 쓴다 (진짜는 B의 save_assessment·create_contact_task 몫)."""

    def __init__(self, session_factory, impacts):
        self.sf = session_factory
        self.impacts = {c["customer_id"]: c for c in impacts}

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

    async def create_task(self, payload):
        with self.sf() as s:
            existing = s.scalar(select(ContactTask).where(
                ContactTask.assessment_id == payload["assessment_id"],
                ContactTask.customer_id == payload["customer_id"]))
            if existing is not None:  # 멱등
                return existing.task_id
            now = now_iso()
            t = ContactTask(assessment_id=payload["assessment_id"], customer_id=payload["customer_id"],
                            pb_id=payload["pb_id"],
                            draft_message=payload["draft_message"], status="승인", created_at=now, updated_at=now)
            s.add(t)
            s.commit()
            return t.task_id


@pytest.fixture
def kit(session_factory):
    tools, model, harness = Tools(), Model(), FakeHarness()
    store = DbStore(session_factory, tools.customers)
    deps = Dependencies(tools, model, harness, store,
                        classify=lambda title: "AUDIT" if "감사" in title else None,
                        signal_input=lambda extracted: [
                            {"signal_type": f["signal_type"], "detail": f["values"]} for f in extracted["facts"]],
                        create_task=store.create_task)
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


def decide(client, h, assessment_id, decisions):
    return client.post(f"/alerts/{assessment_id}/decision", json={"decisions": decisions}, headers=h)


def tasks(session_factory):
    with session_factory() as s:
        return {t.customer_id: t for t in s.scalars(select(ContactTask))}


def start(client, h):
    assert client.post("/runs", json={"rcept_no": RCEPT}, headers=h).status_code == 202
    return read_events(client, h)


def test_scenario_a_with_agent_runtime_and_restart(session_factory, kit, tmp_path):
    deps, _ = kit
    cp = tmp_path / "checkpoints.db"

    # ---- 서버 1: 실행 → 승인 대기 → P01 결정 ----
    with server(session_factory, deps, cp) as c:
        h1 = headers(c)
        events = start(c, h1)
        finished = [d["node"] for e, d in events if e == "node_finished"]
        assert finished[:2] == ["receive", "classify"]
        assert "guardrail_check" in finished and "prepare_approval" in finished
        assert "pb_approval" not in finished  # interrupt로 멈춘 노드
        name, last = events[-1]
        assert name == "waiting_approval", last
        assert last["affected"] == 7 and sorted(last["pending"]) == sorted(P01 + P02)
        aid = last["assessment_id"]

        row = c.get("/alerts", headers=h1).json()[0]
        assert row["status"] == "WAITING_APPROVAL" and row["level"] == "Critical" and row["my_pending_count"] == 4
        drafts = c.get(f"/alerts/{aid}/drafts", headers=h1).json()["drafts"]
        assert sorted(d["customer_id"] for d in drafts) == P01 and all("{고객명}" in d["draft"] for d in drafts)

        assert decide(c, h1, aid, [{"customer_id": "C002", "action": "approve"}]).status_code == 403
        res = decide(c, h1, aid, [{"customer_id": "C001", "action": "approve", "edited_draft": EDITED},
                                  *({"customer_id": x, "action": "approve"} for x in P01[1:])])
        assert res.status_code == 202, res.text
        events = read_events(c, h1)
        assert events[-1][0] == "waiting_approval", events[-1]
        assert sorted(events[-1][1]["pending"]) == P02
        assert sorted(tasks(session_factory)) == P01
        assert tasks(session_factory)["C001"].draft_message == EDITED

    # ---- 서버 2: 재시작. 체크포인트에서 이어서 P02 결정 ----
    with server(session_factory, deps, cp) as c:
        h2 = headers(c, "p02@demo.local")
        detail = c.get(f"/alerts/{aid}", headers=h2).json()
        decided = {x["customer_id"]: x["decision"] for x in detail["customers"]}
        assert decided["C001"] == "승인" and decided["C002"] == "승인대기"

        res = decide(c, h2, aid, [
            {"customer_id": "C002", "action": "reject", "reject_reason": "이미 안내받음"},
            {"customer_id": "C004", "action": "approve"},
            {"customer_id": "C006", "action": "approve"},
        ])
        assert res.status_code == 202, res.text
        assert read_events(c, h2)[-1][0] == "done"

        assert sorted(tasks(session_factory)) == sorted(P01 + ["C004", "C006"])  # 반려 C002는 업무 없음
        with session_factory() as s:
            assert s.get(RiskAssessment, aid).status == "DONE"
            agent_logs = s.scalars(select(AuditLog).where(AuditLog.actor == "agent")).all()
        assert any(l.payload.get("reject_reason") == "이미 안내받음" for l in agent_logs)
        detail = c.get(f"/alerts/{aid}", headers=h2).json()
        assert {x["customer_id"]: x["decision"] for x in detail["customers"]}["C002"] == "반려"


def test_forbidden_edit_is_rejected_before_202(session_factory, kit, tmp_path):
    deps, _ = kit
    with server(session_factory, deps, tmp_path / "cp.db") as c:
        h1 = headers(c)
        aid = start(c, h1)[-1][1]["assessment_id"]
        res = decide(c, h1, aid, [{"customer_id": "C001", "action": "approve",
                                   "edited_draft": "{고객명}님, 지금 매도하세요."}])
        assert res.status_code == 422, res.text
        with session_factory() as s:  # 거절된 결정은 기록하지 않는다
            assert not s.scalars(select(AuditLog).where(AuditLog.action.startswith("decision."))).all()
        # 상태는 그대로 승인 대기 — 정상 결정은 계속 받을 수 있다
        assert decide(c, h1, aid, [{"customer_id": "C001", "action": "approve"}]).status_code == 202


def test_unsafe_drafts_become_manual(session_factory, kit, tmp_path):
    deps, model = kit
    model.unsafe = True  # 가짜 LLM이 계속 "매도하세요"를 쓴다
    with server(session_factory, deps, tmp_path / "cp.db") as c:
        h1 = headers(c)
        name, last = start(c, h1)[-1]
        assert name == "waiting_approval", last
        assert sorted(last["manual"]) == sorted(P01 + P02)
        drafts = {d["customer_id"]: d for d in c.get(f"/alerts/{last['assessment_id']}/drafts", headers=h1).json()["drafts"]}
        assert drafts["C001"]["manual"] and drafts["C001"]["draft"] == ""
        # 빈 초안 그대로 승인은 안 되고, 직접 쓴 문장으로는 승인된다
        assert decide(c, h1, last["assessment_id"], [{"customer_id": "C001", "action": "approve"}]).status_code == 422
        assert decide(c, h1, last["assessment_id"], [{"customer_id": "C001", "action": "approve",
                                                     "edited_draft": EDITED}]).status_code == 202


def test_rewrite_keeps_customer_pending(session_factory, kit, tmp_path):
    deps, _ = kit
    with server(session_factory, deps, tmp_path / "cp.db") as c:
        h1 = headers(c)
        aid = start(c, h1)[-1][1]["assessment_id"]
        assert decide(c, h1, aid, [{"customer_id": "C003", "action": "rewrite"}]).status_code == 202
        events = read_events(c, h1)
        assert "draft_messages" in [d["node"] for e, d in events if e == "node_finished"]
        assert events[-1][0] == "waiting_approval" and "C003" in events[-1][1]["pending"]


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
