"""API가 Graph를 부르는 유일한 통로. (담당: R4, Graph 쪽은 R2)

- FakeGraphAdapter: 진짜 Graph를 돌릴 수 없을 때(MCP·키 없음, 발표 비상용) 흐름만 흉내 낸다.
- RealGraphAdapter: agent.AgentRuntime으로 실제 실행한다 (docs/contracts/agent.md).

`.env`의 `GRAPH_ADAPTER=fake|real`로 고른다 (기본 fake, shared.config).

이벤트는 {"event": <이름>, "data": {...}} 형태이고 이름은 node_started·node_finished·done·failed(큐 모드는 queued)다.
고객 연락(PB 승인·결정 재개)은 삭제됐다: 공시 1건은 분석 → 설명 → 저장 후 done으로 끝난다.
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
from shared.models import Company, Disclosure, RiskAssessment

NODE_STARTED = "node_started"
NODE_FINISHED = "node_finished"
DONE = "done"
FAILED = "failed"
QUEUED = "queued"  # 큐 모드에서 실행 전 (끝 이벤트 아님)
TERMINAL_EVENTS = {DONE, FAILED}

Event = dict[str, Any]
SessionFactory = Callable[[], AbstractContextManager[Session]]

KST = timezone(timedelta(hours=9))


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")  # 예: 2025-04-01T09:00:00+09:00


def make_event(name: str, rcept_no: str, **data: Any) -> Event:
    return {"event": name, "data": {"rcept_no": rcept_no, "ts": now_iso(), **data}}


class GraphAdapter(Protocol):
    def run(self, rcept_no: str) -> AsyncIterator[Event]:
        """공시 1건 처리를 시작하고, 끝날 때까지 이벤트를 내보낸다."""

    async def get_state(self, rcept_no: str) -> dict | None:
        """thread의 현재 상태를 아래 공통 모양으로 돌려준다. 없으면 None.

        {status, assessment_id, disclosure, risk_level, total_score, ai_explanation, evidence, error}
        """


# ---------------------------------------------------------------------------
# 가짜 Graph
# ---------------------------------------------------------------------------
FAKE_NODES = ["receive", "classify", "extract_facts", "score_risk", "explain_risk", "save_and_summarize"]  # 진짜 Graph와 같은 이름
FAKE_STOCK_CODE = "001570"  # 가짜 흐름이 가리키는 종목 (금양)


class FakeGraphAdapter:
    """agent/ 없이 /runs, SSE, 화면을 끝까지 만들어 보기 위한 가짜. 상태는 메모리에만 둔다."""

    def __init__(self, delay: float | None = None, session_factory: SessionFactory = get_session):
        self.delay = get_settings().FAKE_GRAPH_DELAY if delay is None else delay
        self.session_factory = session_factory
        self._states: dict[str, dict] = {}

    async def _step(self, rcept_no: str, node: str, **finished: Any) -> AsyncIterator[Event]:
        yield make_event(NODE_STARTED, rcept_no, node=node)
        await asyncio.sleep(self.delay)
        yield make_event(NODE_FINISHED, rcept_no, node=node, **finished)

    # save_assessment 흉내 (진짜는 Agent의 노드가 business-mcp로 저장)
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
                reasons=[{"item": "[FAKE] 감사의견 거절 (확정 악재 → Critical)", "points": None}],
                ai_explanation=state["ai_explanation"],
                status="DONE",
                created_at=now_iso(),
            )
            s.add(row)
            s.commit()
            return row.assessment_id

    async def run(self, rcept_no: str) -> AsyncIterator[Event]:
        state = {
            "status": "RUNNING",
            "assessment_id": None,
            "disclosure": {"rcept_no": rcept_no, "stock_code": FAKE_STOCK_CODE, "title": "[FAKE] 감사의견 거절 공시"},
            "risk_level": "Critical",
            "total_score": None,  # 가짜라 점수는 만들지 않는다
            "ai_explanation": "[FAKE] 감사의견 거절은 확정 악재라 점수와 상관없이 Critical입니다.",
            "evidence": [{"source": "[FAKE]", "quote": "[FAKE] 원문 인용 자리 — 진짜 Graph에서 채워짐", "rcept_no": rcept_no}],
            "error": None,
        }
        self._states[rcept_no] = state
        for node in FAKE_NODES:
            async for ev in self._step(rcept_no, node, **({"risk_level": state["risk_level"]} if node == "score_risk" else {})):
                yield ev
            if node == "explain_risk":
                state["assessment_id"] = self._save_assessment(state)
        state["status"] = "DONE"
        yield make_event(DONE, rcept_no, assessment_id=state["assessment_id"])

    async def get_state(self, rcept_no: str) -> dict | None:
        return self._states.get(rcept_no)


# ---------------------------------------------------------------------------
# 진짜 Graph (A의 AgentRuntime)
# ---------------------------------------------------------------------------
# 실행 중인 공시의 이벤트를 받을 큐. runtime.start를 부르는 task의 context에만 설정된다.
_event_sink: ContextVar[asyncio.Queue | None] = ContextVar("graph_event_sink", default=None)


class _EventTap:
    """Harness를 감싸서 node_started/node_finished를 SSE로도 보낸다. 나머지 메서드는 그대로 넘긴다.

    done·failed는 Harness 이벤트를 쓰지 않고, 실행이 끝난 뒤 State로 만든다. (실행이 완전히 끝난 뒤에 보내야 하기 때문)
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
    from agent.adapters.assembly import build_dependencies

    try:
        return await build_dependencies()
    except Exception as exc:
        raise RuntimeError(
            f"agent.Dependencies 조립 실패 ({type(exc).__name__}): "
            "business-mcp(8001)·rag-mcp(8002)가 떠 있는지, .env의 ANTHROPIC_API_KEY·OPENAI_API_KEY가 있는지 확인하세요"
        ) from exc


class RealGraphAdapter:
    """`agent.AgentRuntime`으로 실행한다 (docs/contracts/agent.md "C: Python 호출 경계").

    - 시작: runtime.start(rcept_no)
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

    # ---- 상태 ----
    @staticmethod
    def _view(state: dict) -> dict:
        """Agent의 전체 State → API 공통 모양 (GraphAdapter.get_state 설명 참고). 화면용 필터는 라우트가 한다."""
        score = state.get("score_result") or {}
        return {
            "status": state.get("status"),
            "assessment_id": state.get("assessment_id"),
            "disclosure": state.get("disclosure", {}),
            "risk_level": state.get("risk_level") or score.get("level"),
            "total_score": state.get("total_score", score.get("total_score")),
            "ai_explanation": state.get("ai_explanation"),
            "evidence": state.get("evidence", []),
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
        kind = get_settings().GRAPH_ADAPTER.strip().lower()
        _adapter = RealGraphAdapter() if kind == "real" else FakeGraphAdapter()
    return _adapter


async def close_graph_adapter() -> None:
    global _adapter
    if _adapter is not None and hasattr(_adapter, "aclose"):
        await _adapter.aclose()
    _adapter = None
