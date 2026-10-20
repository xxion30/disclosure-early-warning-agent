"""API가 A의 Graph를 부르는 유일한 통로.

- FakeGraphAdapter: 진짜 Graph를 돌릴 수 없을 때(MCP·키 없음, 발표 비상용) 시나리오 A(금양) 기대 결과로 흐름만 흉내 낸다.
  A·B가 맡을 DB 쓰기(save_assessment, create_contact_task)도 가짜로 대신해서 화면을 끝까지 만들 수 있게 한다.
- RealGraphAdapter: A의 agent.AgentRuntime으로 실제 실행한다 (agent/INTEGRATION.md).

`GRAPH_ADAPTER=fake|real` 환경 변수로 고른다 (기본 fake).
TODO(B): shared/config.py Settings에 GRAPH_ADAPTER, FAKE_GRAPH_DELAY 추가 요청. 그 전까지 os.environ에서 읽는다.

이벤트는 모두 {"event": <이름>, "data": {...}} 형태이고 이름은 CLAUDE.md 8장 SSE 이벤트를 따른다.

PB 결정 흐름 (A 구현과 같음):
  pb_approval [interrupt] → 결정 1묶음(PB 1명) 받음 → create_tasks(이번 묶음의 승인 고객만)
    → 결정 안 된 고객이 남았으면 다시 pb_approval [interrupt], 없으면 END
  resume에 넘기는 C 형식: {"pb_id", "decided_at", "allowed_customer_ids", "decisions": [{customer_id, action, edited_draft, reject_reason}]}
  action: approve | reject | rewrite(초안 다시 작성, 계속 승인 대기). RealGraphAdapter가 A 형식(ApprovalBatch, PBContext)으로 바꾼다.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractContextManager
from contextvars import ContextVar
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
QUEUED = "queued"  # 큐 모드에서 실행 전 (끝 이벤트 아님)
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
         affected_customers, drafts, guardrail_flags, manual_drafts, decided: {customer_id: 결정}, task_ids, error}
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
            "manual_drafts": [],
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

        async for ev in self._step(rcept_no, "prepare_approval"):
            yield ev
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

        # A의 Graph와 같은 순서: save_rejection → create_tasks → finalize → (다시 쓰기 / 승인 대기 / 끝)
        async for ev in self._step(rcept_no, "save_rejection"):
            yield ev
        yield make_event(NODE_STARTED, rcept_no, node="create_tasks")
        await asyncio.sleep(self.delay)
        ids = self._create_tasks(state, pb_id, approved) if approved else []
        state["task_ids"].extend(ids)
        yield make_event(NODE_FINISHED, rcept_no, node="create_tasks", approved=len(ids), task_ids=ids)
        async for ev in self._step(rcept_no, "finalize"):
            yield ev

        if rewrite:
            for cid in rewrite:
                state["drafts"][cid] = FAKE_DRAFT.replace("[FAKE 초안]", "[FAKE 초안 · 다시 작성]")
            async for ev in self._step(rcept_no, "draft_messages", drafts=len(rewrite)):
                yield ev
            async for ev in self._step(rcept_no, "guardrail_check", passed=True):
                yield ev

        if pending_customers(state):
            async for ev in self._step(rcept_no, "prepare_approval"):
                yield ev
            yield self._wait(rcept_no)
            return
        state["status"] = "DONE"
        self._set_assessment_status(state["assessment_id"], "DONE")
        yield make_event(DONE, rcept_no, assessment_id=state["assessment_id"])

    async def get_state(self, rcept_no: str) -> dict | None:
        return self._states.get(rcept_no)


# ---------------------------------------------------------------------------
# 진짜 Graph (A의 AgentRuntime)
# ---------------------------------------------------------------------------
# 실행 중인 공시의 이벤트를 받을 큐. runtime.start/resume을 부르는 task의 context에만 설정된다.
_event_sink: ContextVar[asyncio.Queue | None] = ContextVar("graph_event_sink", default=None)

_DECISION_TO_AGENT = {"approve": "approved", "reject": "rejected", "rewrite": "rewrite"}
_DECISION_FROM_AGENT = {v: k for k, v in _DECISION_TO_AGENT.items()}


class _EventTap:
    """Harness를 감싸서 node_started/node_finished를 SSE로도 보낸다. 나머지 메서드는 그대로 넘긴다.

    waiting_approval·done·failed는 Harness 이벤트를 쓰지 않고, 실행이 끝난 뒤 State로 만든다.
    (고객 수·남은 고객 같은 화면 정보가 필요하고, 실행이 완전히 끝난 뒤에 보내야 하기 때문)
    """

    def __init__(self, inner: Any):
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def event(self, name: str, payload: dict) -> None:
        await self._inner.event(name, payload)
        sink = _event_sink.get()
        if sink is not None and name in (NODE_STARTED, NODE_FINISHED) and payload.get("node"):
            sink.put_nowait((name, payload["node"]))


async def default_dependencies():
    """진짜 MCP·LLM·Harness·저장소를 묶은 agent.Dependencies (조립은 agent/assembly.py).

    business-mcp(8001)·rag-mcp(8002)가 떠 있고 모델 API 키가 .env에 있어야 한다.
    """
    from agent.assembly import build_dependencies

    try:
        return await build_dependencies()
    except Exception as exc:
        raise RuntimeError(
            f"agent.Dependencies 조립 실패 ({type(exc).__name__}): "
            "business-mcp(8001)·rag-mcp(8002)가 떠 있는지, .env의 ANTHROPIC_API_KEY·OPENAI_API_KEY가 있는지 확인하세요"
        ) from exc


class RealGraphAdapter:
    """A의 `agent.AgentRuntime`으로 실행한다 (agent/INTEGRATION.md "C: Python 호출 경계").

    - 시작: runtime.start(rcept_no), 재개: runtime.resume(rcept_no, ApprovalBatch, PBContext)
    - 진행 이벤트: Harness.event의 node_started/node_finished
    - thread_id = rcept_no, 체크포인트는 data/checkpoints.db (서버를 재시작해도 이어서 실행)
    """

    def __init__(self, deps: Any = None, checkpoint_path: str | os.PathLike | None = None):
        self.checkpoint_path = checkpoint_path or ROOT_DIR / get_settings().CHECKPOINT_DB
        self._deps = deps
        self._runtime = None
        self._runtime_cm = None
        self._open_lock = asyncio.Lock()

    async def _get_runtime(self):
        async with self._open_lock:
            if self._runtime is None:
                from agent import open_runtime

                deps = self._deps if self._deps is not None else await default_dependencies()
                deps = dataclasses.replace(deps, harness=_EventTap(deps.harness))
                self._runtime_cm = open_runtime(deps, str(self.checkpoint_path))
                self._runtime = await self._runtime_cm.__aenter__()
        return self._runtime

    async def aclose(self) -> None:
        """체크포인트 DB 연결을 닫는다. 서버 종료 시 lifespan에서 부른다."""
        if self._runtime_cm is not None:
            await self._runtime_cm.__aexit__(None, None, None)
        self._runtime = None
        self._runtime_cm = None

    # ---- 결정 묶음 변환 (C 형식 → A 형식) ----
    @staticmethod
    def _batch_and_pb(decision: dict):
        from agent import ApprovalBatch, PBContext

        batch = ApprovalBatch(decisions=[{
            "customer_id": d["customer_id"],
            "status": _DECISION_TO_AGENT[d["action"]],
            "edited_message": d.get("edited_draft"),
            "reject_reason": d.get("reject_reason"),
        } for d in decision["decisions"]])
        pb = PBContext(pb_id=decision["pb_id"], allowed_customer_ids=frozenset(decision["allowed_customer_ids"]))
        return batch, pb

    async def precheck(self, rcept_no: str, decision: dict) -> None:
        """결정 API가 202를 돌려주기 전에 부른다. runtime.resume과 같은 검사를 먼저 해서
        잘못된 결정(권한, 상태, 가드레일 실패한 수정안)을 바로 403/422로 돌려줄 수 있게 한다.
        PermissionError → 403, ValueError → 422."""
        from agent.nodes.pb_approval import validate_batch

        runtime = await self._get_runtime()
        batch, pb = self._batch_and_pb(decision)
        state = await runtime.get_state(rcept_no)
        if not state:
            raise ValueError("Graph 상태가 없습니다.")
        if any(d.customer_id not in pb.allowed_customer_ids for d in batch.decisions):
            raise PermissionError("담당 고객만 결정할 수 있습니다")
        if state.get("status") != "WAITING_APPROVAL":
            raise ValueError("현재 승인 대기 상태가 아닙니다")
        await validate_batch(state, batch, pb, runtime.deps)

    # ---- 실행 ----
    async def _drive(self, rcept_no: str, call: Callable[[], Any]) -> AsyncIterator[Event]:
        """call()을 백그라운드로 돌리면서 Harness 이벤트를 흘려보내고, 끝나면 State로 마지막 이벤트를 만든다."""
        queue: asyncio.Queue = asyncio.Queue()
        token = _event_sink.set(queue)
        try:
            task = asyncio.create_task(call())  # 지금 context(큐 포함)가 복사된다
        finally:
            _event_sink.reset(token)

        while True:
            getter = asyncio.create_task(queue.get())
            done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
            if getter in done:
                name, node = getter.result()
                yield make_event(name, rcept_no, node=node)
                continue
            getter.cancel()
            break
        while not queue.empty():
            name, node = queue.get_nowait()
            yield make_event(name, rcept_no, node=node)

        try:
            state = task.result()
        except Exception as exc:  # 권한·입력 오류, 의존성 조립 실패 등
            yield make_event(FAILED, rcept_no, message=f"{type(exc).__name__}: {exc}")
            return
        yield self._terminal_event(rcept_no, self._view(state))

    def _terminal_event(self, rcept_no: str, view: dict) -> Event:
        status = view.get("status")
        if status == "WAITING_APPROVAL":
            customers = view["affected_customers"]
            return make_event(
                WAITING_APPROVAL, rcept_no,
                node="pb_approval",
                assessment_id=view["assessment_id"],
                affected=len(customers),
                p1=sum(c.get("priority") == "P1" for c in customers),
                pending=pending_customers(view),
                manual=view["manual_drafts"],
            )
        if status in ("MANUAL_CHECK", "FAILED"):
            return make_event(FAILED, rcept_no, status=status, assessment_id=view["assessment_id"],
                              message=view.get("error") or status)
        return make_event(DONE, rcept_no, status=status, assessment_id=view["assessment_id"])

    async def run(self, rcept_no: str) -> AsyncIterator[Event]:
        try:
            runtime = await self._get_runtime()
        except Exception as exc:
            yield make_event(FAILED, rcept_no, message=f"{type(exc).__name__}: {exc}")
            return
        async for ev in self._drive(rcept_no, lambda: runtime.start(rcept_no)):
            yield ev

    async def resume(self, rcept_no: str, decision: dict) -> AsyncIterator[Event]:
        runtime = await self._get_runtime()
        batch, pb = self._batch_and_pb(decision)
        async for ev in self._drive(rcept_no, lambda: runtime.resume(rcept_no, batch, pb)):
            yield ev

    # ---- 상태 ----
    @staticmethod
    def _view(state: dict) -> dict:
        """A의 전체 State → API 공통 모양 (GraphAdapter.get_state 설명 참고). 화면용 필터는 라우트가 한다."""
        score = state.get("score_result") or {}
        decided = {
            cid: {
                "action": _DECISION_FROM_AGENT.get(d.get("status"), d.get("status")),
                "edited_draft": d.get("edited_message"),
                "reject_reason": d.get("reject_reason"),
                "pb_id": d.get("pb_id"),
                "decided_at": d.get("decided_at"),
            }
            for cid, d in (state.get("decisions") or {}).items()
        }
        return {
            "status": state.get("status"),
            "assessment_id": state.get("assessment_id"),
            "disclosure": state.get("disclosure", {}),
            "risk_level": state.get("risk_level") or score.get("level"),
            "total_score": state.get("total_score", score.get("total_score")),
            "ai_explanation": state.get("ai_explanation"),
            "evidence": state.get("evidence", []),
            "affected_customers": state.get("affected_customers", []),
            "drafts": state.get("drafts", {}),
            "guardrail_flags": state.get("guardrail_flags", []),
            "manual_drafts": state.get("manual_drafts", []),
            "decided": decided,
            "task_ids": state.get("task_ids", []),
            "error": state.get("error"),
        }

    async def get_state(self, rcept_no: str) -> dict | None:
        runtime = await self._get_runtime()
        state = await runtime.get_state(rcept_no)
        return self._view(state) if state else None


_adapter: GraphAdapter | None = None


def get_graph_adapter() -> GraphAdapter:
    global _adapter
    if _adapter is None:
        kind = os.getenv("GRAPH_ADAPTER", "fake").lower()
        _adapter = RealGraphAdapter() if kind == "real" else FakeGraphAdapter()
    return _adapter


async def close_graph_adapter() -> None:
    global _adapter
    if _adapter is not None and hasattr(_adapter, "aclose"):
        await _adapter.aclose()
    _adapter = None
