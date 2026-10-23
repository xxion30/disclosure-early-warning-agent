"""Trusted Python service boundary for C's API (not an HTTP/auth implementation)."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from langsmith import trace

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.execution import Execution, current_execution
from agent.graph import build_graph
from agent.nodes.pb_approval import validate_batch
from agent.state import ApprovalBatch, PBContext, initial_state


class AgentRuntime:
    def __init__(self, deps, checkpointer):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)
        self._locks = {}

    @staticmethod
    def config(rcept_no):
        # This is a graph-superstep safeguard, not the separate eight-AI-turn budget.
        return {"configurable": {"thread_id": rcept_no}, "recursion_limit": 256}

    def _lock(self, rcept_no):
        return self._locks.setdefault(rcept_no, asyncio.Lock())

    async def get_state(self, rcept_no):
        """Internal full snapshot. C must apply PB-specific filtering before HTTP/SSE."""
        snapshot = await self.graph.aget_state(self.config(rcept_no))
        return dict(snapshot.values)

    async def get_review(self, rcept_no, pb: PBContext):
        pb = PBContext.model_validate(pb)
        state = await self.get_state(rcept_no)
        if not state:
            raise KeyError(rcept_no)
        allowed = pb.allowed_customer_ids
        return {
            "assessment_id": state.get("assessment_id"), "status": state["status"],
            "disclosure": state["disclosure"], "score_result": state.get("score_result"),
            "ai_explanation": state.get("ai_explanation"),
            "affected_customers": [c for c in state["affected_customers"] if c["customer_id"] in allowed],
            "drafts": {c: text for c, text in state["drafts"].items() if c in allowed},
            "decisions": {c: d for c, d in state["decisions"].items() if c in allowed},
            "manual_drafts": [c for c in state.get("manual_drafts", []) if c in allowed],
            "guardrail_flags": [flag for flag in state.get("guardrail_flags", [])
                                if flag.partition(":")[0] in allowed],
            "customer_tasks": {c: task_id for c, task_id in state["customer_tasks"].items()
                               if c in allowed},
            "error": state.get("error"),
        }

    async def _invoke(self, rcept_no, value, trace_id):
        scope = Execution(self.deps.execution_seconds, self.deps.max_model_turns)
        token = current_execution.set(scope)
        config = self.config(rcept_no)
        config["metadata"] = {"rcept_no": rcept_no, "trace_id": trace_id}
        try:
            initial = isinstance(value, dict)
            previous = {} if initial else await self.get_state(rcept_no)
            with trace("disclosure_agent" if initial else "disclosure_agent_resume",
                       run_id=trace_id if initial else None,
                       parent=previous.get("trace_headers"),
                       inputs={"rcept_no": rcept_no}, metadata=config["metadata"]) as run:
                if initial:
                    value["trace_headers"] = run.to_headers()
                await self.graph.ainvoke(value, config=config)
                state = await self.get_state(rcept_no)
                run.add_metadata({"corp_name": state["disclosure"].get("corp_name"),
                                  "risk_level": state.get("risk_level")})
                run.end(outputs={"status": state["status"], "task_ids": state["task_ids"]})
                return state
        finally:
            current_execution.reset(token)

    async def start(self, rcept_no: str):
        if not isinstance(rcept_no, str) or not rcept_no.strip():
            raise ValueError("접수번호가 필요합니다")
        async with self._lock(rcept_no):
            snapshot = await self.graph.aget_state(self.config(rcept_no))
            existing = dict(snapshot.values)
            if existing:
                # 노드 실행 중에 프로세스가 죽은 실행: 다음 노드가 남아 있는데 승인 대기(interrupt)도 아니다.
                # 큐가 같은 공시를 다시 넘겨 줬을 때 조용히 "끝난 것"으로 돌려주면 공시가 사라지므로, 마지막 체크포인트부터 이어서 실행한다.
                crashed = bool(snapshot.next) and not any(t.interrupts for t in snapshot.tasks)
                if crashed:
                    return await self._invoke(rcept_no, None, existing["trace_id"])
                # 끝났거나 승인 대기인 실행은 암묵적으로 다시 시작하지 않는다.
                return existing
            state = initial_state(rcept_no, str(uuid4()))
            return await self._invoke(rcept_no, state, state["trace_id"])

    async def resume(self, rcept_no: str, batch: ApprovalBatch, pb: PBContext):
        batch, pb = ApprovalBatch.model_validate(batch), PBContext.model_validate(pb)
        async with self._lock(rcept_no):
            state = await self.get_state(rcept_no)
            if not state:
                raise KeyError(rcept_no)
            # Check ownership before any idempotent response or graph resumption.
            if any(d.customer_id not in pb.allowed_customer_ids for d in batch.decisions):
                raise PermissionError("담당 고객만 결정할 수 있습니다")
            existing = [state["decisions"].get(d.customer_id) for d in batch.decisions]
            if all(existing):
                if all(old["pb_id"] == pb.pb_id and all(
                    old.get(k) == v for k, v in new.model_dump().items()
                ) for old, new in zip(existing, batch.decisions)):
                    return state
                raise ValueError("이미 처리된 결정과 내용이 다릅니다")
            if any(existing):
                raise ValueError("처리된 고객과 미결정 고객은 나누어 요청하세요")
            if state["status"] != "WAITING_APPROVAL":
                raise ValueError("현재 승인 대기 상태가 아닙니다")
            # Invalid decisions never consume the persisted interrupt.
            await validate_batch(state, batch, pb, self.deps)
            return await self._invoke(rcept_no, Command(resume={
                "verified_pb": pb.model_dump(mode="json"), "batch": batch.model_dump(),
            }), state["trace_id"])

    async def retry_tasks(self, rcept_no: str, pb: PBContext):
        """Explicitly retry accepted writes after a create_tasks failure.

        Requires an idempotent task store, including writes whose replies were lost.
        Does not reanalyze the disclosure or accept any new approval.
        """
        pb = PBContext.model_validate(pb)
        async with self._lock(rcept_no):
            state = await self.get_state(rcept_no)
            if state.get("status") != "MANUAL_CHECK" or state.get("last_node") != "create_tasks":
                raise ValueError("업무 생성 실패만 이 경로에서 재시도할 수 있습니다")
            outstanding = [(cid, decision) for cid, decision in state["decisions"].items()
                           if decision["status"] == "approved" and cid not in state["customer_tasks"]]
            if not outstanding or any(cid not in pb.allowed_customer_ids or decision["pb_id"] != pb.pb_id
                                      for cid, decision in outstanding):
                raise PermissionError("본인이 승인한 미완료 업무만 재시도할 수 있습니다")
            await asyncio.wait_for(self.deps.persistence.audit(state, "retry_approved_tasks", {
                "pb_id": pb.pb_id, "customer_ids": [cid for cid, _ in outstanding],
            }), timeout=5)
            await self.graph.aupdate_state(self.config(rcept_no),
                                           {"error": None, "status": "RUNNING"},
                                           as_node="save_rejection")
            return await self._invoke(rcept_no, None, state["trace_id"])


@asynccontextmanager
async def open_runtime(deps, checkpoint_db=None):
    if checkpoint_db is None:
        from shared.config import get_settings
        checkpoint_db = get_settings().CHECKPOINT_DB
    path = Path(checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield AgentRuntime(deps, saver)
