"""API가 A의 Graph를 부르는 유일한 통로.

- FakeGraphAdapter: agent/가 준비되기 전, 시나리오 A(금양) 기대 결과로 흐름만 흉내 낸다.
  A·B가 맡을 DB 쓰기(save_assessment, create_contact_task)도 가짜로 대신해서 화면을 끝까지 만들 수 있게 한다.
- RealGraphAdapter: agent.graph를 LangGraph로 실제 실행한다.

`GRAPH_ADAPTER=fake|real` 환경 변수로 고른다 (기본 fake).
TODO(B): shared/config.py Settings에 GRAPH_ADAPTER, FAKE_GRAPH_DELAY 추가 요청. 그 전까지 os.environ에서 읽는다.

이벤트는 모두 {"event": <이름>, "data": {...}} 형태이고 이름은 CLAUDE.md 8장 SSE 이벤트를 따른다.

PB 결정 흐름 (A에게 같은 방식 요청):
  pb_approval [interrupt] → 결정 1묶음(PB 1명) 받음 → create_tasks(이번 묶음의 승인 고객만)
    → 결정 안 된 고객이 남았으면 다시 pb_approval [interrupt], 없으면 END
  resume 값: {"pb_id", "decided_at", "decisions": [{customer_id, action, edited_draft, reject_reason}]}
  action: approve | reject | rewrite(초안 다시 작성, 계속 승인 대기)
"""
from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.config import ROOT_DIR, get_settings
from shared.db import get_session
from shared.models import Company, ContactTask, Disclosure, RiskAssessment

NODE_STARTED = "node_started"
NODE_FINISHED = "node_finished"
WAITING_APPROVAL = "waiting_approval"
DONE = "done"
FAILED = "failed"
TERMINAL_EVENTS = {WAITING_APPROVAL, DONE, FAILED}

Event = dict[str, Any]
SessionFactory = Callable[[], AbstractContextManager[Session]]

KST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")  # 예: 2025-04-01T09:00:00+09:00


def make_event(name: str, rcept_no: str, **data: Any) -> Event:
    return {"event": name, "data": {"rcept_no": rcept_no, "ts": now_iso(), **data}}


class GraphAdapter(Protocol):
    def run(self, rcept_no: str) -> AsyncIterator[Event]:
        """공시 1건 처리를 시작하고, 멈추거나 끝날 때까지 이벤트를 내보낸다."""

    def resume(self, rcept_no: str, decision: dict) -> AsyncIterator[Event]:
        """pb_approval에서 멈춘 thread를 PB 결정 1묶음으로 이어서 실행한다."""

    async def get_state(self, rcept_no: str) -> dict | None:
        """thread의 현재 상태를 아래 공통 모양으로 돌려준다. 없으면 None.

        {status, assessment_id, disclosure, risk_level, total_score, ai_explanation, evidence,
         affected_customers, drafts, guardrail_flags, decided: {customer_id: 결정}, task_ids, error}
        """


def pending_customers(view: dict) -> list[str]:
    """P1·P2 중 아직 결정되지 않은 고객 (P3는 안내문 대상이 아니다)."""
    return [
        c["customer_id"] for c in view.get("affected_customers", [])
        if c.get("priority") in ("P1", "P2") and c["customer_id"] not in view.get("decided", {})
    ]


# ---------------------------------------------------------------------------
# 가짜 Graph
# ---------------------------------------------------------------------------
FAKE_NODES = [
    "receive", "classify", "extract_facts", "score_risk", "explain_risk",
    "route_by_level", "find_customers", "draft_messages", "guardrail_check",
]

# 안내문 초안 자리표시. 투자 권유 표현 없이 사실 안내만 한다.
FAKE_DRAFT = (
    "{고객명}님, 보유하신 종목과 관련해 중요 공시가 발표되어 안내드립니다. "
    "공시 내용과 향후 일정은 담당 PB가 직접 설명드리겠습니다. "
    "편하신 시간에 연락 주시면 자세히 안내해 드리겠습니다. [FAKE 초안]"
)
FAKE_STOCK_CODE = "001570"  # 시나리오 A 금양


def _load_expected_impact() -> list[dict]:
    """data/customers.json의 시나리오 A 기대 결과(D가 만든 평가 정답)를 그대로 쓴다."""
    path = ROOT_DIR / "data" / "customers.json"
    rows = json.loads(path.read_text(encoding="utf-8"))["expected_impact_001570_critical"]
    return [
        {
            "customer_id": r["customer_id"],
            "impact_score": r["impact_score"],
            "impact_level": r["impact_level"],
            "priority": r["priority"],
            "reasons": {"weight_pct": r.get("weight_pct")},
        }
        for r in rows
    ]


class FakeGraphAdapter:
    """agent/ 없이 /runs, SSE, 결정 API, 화면을 끝까지 만들어 보기 위한 가짜. 상태는 메모리에만 둔다."""

    def __init__(self, delay: float | None = None, session_factory: SessionFactory = get_session):
        self.delay = float(os.getenv("FAKE_GRAPH_DELAY", "0.4")) if delay is None else delay
        self.session_factory = session_factory
        self._states: dict[str, dict] = {}

    async def _step(self, rcept_no: str, node: str, **finished: Any) -> AsyncIterator[Event]:
        yield make_event(NODE_STARTED, rcept_no, node=node)
        await asyncio.sleep(self.delay)
        yield make_event(NODE_FINISHED, rcept_no, node=node, **finished)

    # save_assessment 흉내 (진짜는 A의 노드가 business-mcp로 저장)
    def _save_assessment(self, state: dict) -> int:
        rcept_no = state["disclosure"]["rcept_no"]
        with self.session_factory() as s:
            disclosure = s.get(Disclosure, rcept_no)
            company = s.scalar(select(Company).where(Company.stock_code == FAKE_STOCK_CODE))
            corp_code = (disclosure.corp_code if disclosure else None) or (company.corp_code if company else None)
            if disclosure is not None:
                state["disclosure"].update(title=disclosure.title, filed_at=disclosure.filed_at)
            row = RiskAssessment(
                corp_code=corp_code or f"TODO-{FAKE_STOCK_CODE}",
                rcept_no=rcept_no,
                total_score=0,
                level=state["risk_level"],
                combo_upgraded=False,
                reasons=[{"item": "[FAKE] 감사의견 거절 (A그룹 확정 악재 → Critical)", "points": None}],
                ai_explanation=state["ai_explanation"],
                status="RUNNING",
                created_at=now_iso(),
            )
            s.add(row)
            s.commit()
            return row.assessment_id

    def _set_assessment_status(self, assessment_id: int, status: str) -> None:
        with self.session_factory() as s:
            row = s.get(RiskAssessment, assessment_id)
            if row is not None:
                row.status = status
                s.commit()

    # create_contact_task 흉내 (진짜는 A의 create_tasks 노드가 business-mcp로 생성)
    def _create_tasks(self, state: dict, pb_id: str, approved: list[dict]) -> list[int]:
        impacts = {c["customer_id"]: c for c in state["affected_customers"]}
        ids = []
        with self.session_factory() as s:
            for d in approved:
                cid = d["customer_id"]
                now = now_iso()
                task = ContactTask(
                    assessment_id=state["assessment_id"],
                    customer_id=cid,
                    pb_id=pb_id,
                    priority=impacts[cid]["priority"],
                    impact_score=impacts[cid]["impact_score"],
                    draft_message=d.get("edited_draft") or state["drafts"][cid],
                    status="승인",
                    created_at=now,
                    updated_at=now,
                )
                s.add(task)
                s.flush()
                ids.append(task.task_id)
            s.commit()
        return ids

    async def run(self, rcept_no: str) -> AsyncIterator[Event]:
        customers = _load_expected_impact()
        state = {
            "status": "RUNNING",
            "assessment_id": None,
            "disclosure": {"rcept_no": rcept_no, "stock_code": FAKE_STOCK_CODE, "title": "[FAKE] 감사의견 거절 공시"},
            "risk_level": "Critical",
            "total_score": None,  # 가짜라 점수는 만들지 않는다
            "ai_explanation": "[FAKE] 감사의견 거절은 A그룹 확정 악재라 점수와 상관없이 Critical입니다.",
            "evidence": [{"source": "[FAKE]", "quote": "[FAKE] 원문 인용 자리 — 진짜 Graph에서 채워짐", "rcept_no": rcept_no}],
            "affected_customers": customers,
            "drafts": {c["customer_id"]: FAKE_DRAFT for c in customers if c["priority"] in ("P1", "P2")},
            "guardrail_flags": [],
            "decided": {},
            "task_ids": [],
            "error": None,
        }
        self._states[rcept_no] = state

        summaries = {
            "score_risk": {"risk_level": state["risk_level"]},
            "find_customers": {"affected": len(customers)},
            "draft_messages": {"drafts": len(state["drafts"])},
            "guardrail_check": {"passed": True},
        }
        for node in FAKE_NODES:
            async for ev in self._step(rcept_no, node, **summaries.get(node, {})):
                yield ev
            if node == "explain_risk":
                state["assessment_id"] = self._save_assessment(state)

        yield self._wait(rcept_no)

    def _wait(self, rcept_no: str) -> Event:
        state = self._states[rcept_no]
        state["status"] = "WAITING_APPROVAL"
        self._set_assessment_status(state["assessment_id"], "WAITING_APPROVAL")
        return make_event(
            WAITING_APPROVAL, rcept_no,
            node="pb_approval",
            assessment_id=state["assessment_id"],
            affected=len(state["affected_customers"]),
            p1=sum(c["priority"] == "P1" for c in state["affected_customers"]),
            pending=pending_customers(state),
        )

    async def resume(self, rcept_no: str, decision: dict) -> AsyncIterator[Event]:
        state = self._states.get(rcept_no)
        if state is None or state["status"] != "WAITING_APPROVAL":
            yield make_event(FAILED, rcept_no, message="승인 대기 중인 thread가 아닙니다.")
            return
        state["status"] = "RUNNING"
        self._set_assessment_status(state["assessment_id"], "RUNNING")

        pb_id = decision["pb_id"]
        approved, rewrite = [], []
        for d in decision.get("decisions", []):
            if d["action"] == "rewrite":
                rewrite.append(d["customer_id"])
                continue
            state["decided"][d["customer_id"]] = {**d, "pb_id": pb_id, "decided_at": decision.get("decided_at")}
            if d["action"] == "approve":
                approved.append(d)
        yield make_event(NODE_FINISHED, rcept_no, node="pb_approval", decided=len(decision.get("decisions", [])))

        if rewrite:
            for cid in rewrite:
                state["drafts"][cid] = FAKE_DRAFT.replace("[FAKE 초안]", "[FAKE 초안 · 다시 작성]")
            async for ev in self._step(rcept_no, "draft_messages", drafts=len(rewrite)):
                yield ev
            async for ev in self._step(rcept_no, "guardrail_check", passed=True):
                yield ev

        if approved:
            yield make_event(NODE_STARTED, rcept_no, node="create_tasks")
            await asyncio.sleep(self.delay)
            ids = self._create_tasks(state, pb_id, approved)
            state["task_ids"].extend(ids)
            yield make_event(NODE_FINISHED, rcept_no, node="create_tasks", approved=len(ids), task_ids=ids)

        if pending_customers(state):
            yield self._wait(rcept_no)
            return
        state["status"] = "DONE"
        self._set_assessment_status(state["assessment_id"], "DONE")
        yield make_event(DONE, rcept_no, assessment_id=state["assessment_id"])

    async def get_state(self, rcept_no: str) -> dict | None:
        return self._states.get(rcept_no)


# ---------------------------------------------------------------------------
# 진짜 Graph
# ---------------------------------------------------------------------------
class RealGraphAdapter:
    """agent.graph를 실행한다. thread_id = rcept_no (CLAUDE.md 4-5장).

    TODO(A와 확인): `agent.graph.build_graph(checkpointer)`가 컴파일된 graph를 돌려준다고 가정.
    시작 입력 {"disclosure": {"rcept_no": ...}}, 재개는 Command(resume=decision).
    State에 assessment_id, approval.decisions({customer_id: 결정})가 있다고 가정.
    """

    def __init__(self):
        self._graph = None
        self._saver_cm = None

    async def _get_graph(self):
        if self._graph is None:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            from agent.graph import build_graph

            db_path = ROOT_DIR / get_settings().CHECKPOINT_DB
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._saver_cm = AsyncSqliteSaver.from_conn_string(str(db_path))
            saver = await self._saver_cm.__aenter__()
            self._graph = build_graph(saver)
        return self._graph

    @staticmethod
    def _config(rcept_no: str) -> dict:
        return {"configurable": {"thread_id": rcept_no}, "recursion_limit": 25}

    async def _stream(self, rcept_no: str, graph_input: Any) -> AsyncIterator[Event]:
        graph = await self._get_graph()
        config = self._config(rcept_no)
        try:
            # tasks 모드: 노드 시작 시 {"name", "input"}, 끝날 때 {"name", "result", "error", "interrupts"}
            async for chunk in graph.astream(graph_input, config, stream_mode="tasks"):
                node = chunk.get("name")
                if node is None or node.startswith("__"):
                    continue
                if "result" not in chunk and "error" not in chunk:
                    yield make_event(NODE_STARTED, rcept_no, node=node)
                elif chunk.get("error"):
                    yield make_event(FAILED, rcept_no, node=node, message=str(chunk["error"]))
                    return
                elif chunk.get("interrupts"):
                    continue  # interrupt로 멈춘 노드는 끝난 게 아니다. 아래에서 waiting_approval을 낸다
                else:
                    yield make_event(NODE_FINISHED, rcept_no, node=node)
        except Exception as exc:  # 노드 밖 예외도 failed 이벤트로 돌려준다
            yield make_event(FAILED, rcept_no, message=f"{type(exc).__name__}: {exc}")
            return

        view = await self.get_state(rcept_no)
        if view is None:
            yield make_event(FAILED, rcept_no, message="Graph 상태를 읽을 수 없습니다.")
        elif view["status"] == "WAITING_APPROVAL":
            customers = view["affected_customers"]
            yield make_event(
                WAITING_APPROVAL, rcept_no,
                node="pb_approval",
                assessment_id=view["assessment_id"],
                affected=len(customers),
                p1=sum(c.get("priority") == "P1" for c in customers),
                pending=pending_customers(view),
            )
        elif view["error"]:
            yield make_event(FAILED, rcept_no, message=view["error"])
        else:
            yield make_event(DONE, rcept_no, assessment_id=view["assessment_id"])

    async def run(self, rcept_no: str) -> AsyncIterator[Event]:
        async for ev in self._stream(rcept_no, {"disclosure": {"rcept_no": rcept_no}}):
            yield ev

    async def resume(self, rcept_no: str, decision: dict) -> AsyncIterator[Event]:
        from langgraph.types import Command

        async for ev in self._stream(rcept_no, Command(resume=decision)):
            yield ev

    async def get_state(self, rcept_no: str) -> dict | None:
        graph = await self._get_graph()
        snapshot = await graph.aget_state(self._config(rcept_no))
        v = snapshot.values
        if not v:
            return None
        if snapshot.interrupts:
            status = "WAITING_APPROVAL"
        elif snapshot.next:
            status = "RUNNING"
        else:
            status = "FAILED" if v.get("error") else "DONE"
        return {
            "status": status,
            "assessment_id": v.get("assessment_id"),
            "disclosure": v.get("disclosure", {}),
            "risk_level": v.get("risk_level"),
            "total_score": v.get("total_score"),
            "ai_explanation": v.get("ai_explanation"),
            "evidence": v.get("evidence", []),
            "affected_customers": v.get("affected_customers", []),
            "drafts": v.get("drafts", {}),
            "guardrail_flags": v.get("guardrail_flags", []),
            "decided": (v.get("approval") or {}).get("decisions", {}),
            "task_ids": v.get("task_ids", []),
            "error": v.get("error"),
        }


_adapter: GraphAdapter | None = None


def get_graph_adapter() -> GraphAdapter:
    global _adapter
    if _adapter is None:
        kind = os.getenv("GRAPH_ADAPTER", "fake").lower()
        _adapter = RealGraphAdapter() if kind == "real" else FakeGraphAdapter()
    return _adapter
