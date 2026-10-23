"""Trusted Python service boundary for C's API (not an HTTP/auth implementation)."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from langsmith import trace, tracing_context
from copy import deepcopy

from agent.integration import ExecutionContext, ensure_current
from agent.evidence import retained_data
from agent.phase1 import ALERT_STAGES

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from agent.execution import Execution, current_execution
from agent.graph import build_graph
from agent.nodes.pb_approval import validate_batch
from agent.state import ApprovalBatch, PBContext, initial_state
from agent.public_risk import customer_context, disclosure_context, public_risk_context
from harness.guardrails import check_explanation, check_message


class AgentRuntime:
    def __init__(self, deps, checkpointer):
        self.deps = deps
        self.graph = build_graph(deps, checkpointer)
        self._locks = {}

    @staticmethod
    def config(rcept_no, context=None):
        # This is a graph-superstep safeguard, not the separate eight-AI-turn budget.
        return {"configurable": {"thread_id": (context.thread_id(rcept_no) if context else rcept_no)},
                "recursion_limit": 256}

    def _context(self, context):
        if context is not None:
            context = ExecutionContext.model_validate(context)
        if self.deps.unified is not None and context is None:
            raise ValueError('새 Graph에는 R4가 만든 실행 context가 필요합니다')
        return context

    @staticmethod
    def _pb_scope(state, pb):
        tenant = state.get('execution_context', {}).get('tenant_id')
        if tenant is not None and pb.tenant_id != tenant:
            raise PermissionError('다른 이용 조직의 검토입니다')

    def _lock(self, rcept_no):
        return self._locks.setdefault(rcept_no, asyncio.Lock())

    async def get_state(self, rcept_no, *, context=None):
        """Internal full snapshot. C must apply PB-specific filtering before HTTP/SSE."""
        context = self._context(context)
        snapshot = await self.graph.aget_state(self.config(rcept_no, context))
        return dict(snapshot.values)

    async def get_review(self, rcept_no, pb: PBContext, *, context=None):
        pb = PBContext.model_validate(pb)
        state = await self.get_state(rcept_no, **({'context': context} if context is not None else {}))
        if not state:
            raise KeyError(rcept_no)
        self._pb_scope(state, pb)
        await ensure_current(state, self.deps)
        allowed = pb.allowed_customer_ids
        safe_drafts = {cid: text if not check_message(text) else ""
                       for cid, text in state["drafts"].items() if cid in allowed}
        explanation = state.get("ai_explanation") or ""
        # Fail closed for old checkpoints produced before output validation.
        if check_explanation(explanation):
            explanation = "설명문 확인이 필요합니다."
        return {
            "assessment_id": state.get("assessment_id"), "status": state["status"],
            "stage": state.get("stage"),
            "risk_level": state.get("risk_level"),
            "disclosure": disclosure_context(state["disclosure"]),
            "risk_summary": public_risk_context(state.get("score_result") or {}),
            "ai_explanation": explanation,
            "affected_customers": customer_context([c for c in state["affected_customers"] if c["customer_id"] in allowed]),
            "drafts": safe_drafts,
            "decisions": {c: {k: d[k] for k in ("status", "pb_id", "decided_at") if k in d}
                          for c, d in state["decisions"].items() if c in allowed},
            "manual_drafts": sorted({c for c in state.get("manual_drafts", []) if c in allowed}
                                    | {c for c, text in safe_drafts.items() if not text}),
            "guardrail_flags": [flag for flag in state.get("guardrail_flags", [])
                                if flag.partition(":")[0] in allowed],
            "customer_tasks": {c: task_id for c, task_id in state["customer_tasks"].items()
                               if c in allowed},
            "error": state.get("error"),
        }

    async def _invoke(self, rcept_no, value, trace_id, context=None):
        previous = value if isinstance(value, dict) else await self.get_state(rcept_no, context=context)
        budget = previous.get('execution_budget', {}) if self.deps.unified else {}
        scope = Execution(self.deps.execution_seconds, self.deps.max_model_turns,
                          model_turns=max(budget.get('model_turns', 0), context.model_requests_used if context else 0),
                          consumed_seconds=max(budget.get('consumed_seconds', 0), context.active_seconds_used if context else 0),
                          repeated_calls=dict(budget.get('repeated_calls', {})),
                          record_budget=(self.deps.unified.record_budget if self.deps.unified else None),
                          budget_context=context.model_dump() if context else {})
        token = current_execution.set(scope)
        config = self.config(rcept_no, context)
        config["metadata"] = {"rcept_no": rcept_no, "trace_id": trace_id}
        try:
            initial = isinstance(value, dict)
            previous = {} if initial else previous
            with trace("disclosure_agent" if initial else "disclosure_agent_resume",
                       run_id=trace_id if initial else None,
                       parent=previous.get("trace_headers"),
                       inputs={"rcept_no": rcept_no}, metadata=config["metadata"]) as run:
                if initial:
                    value["trace_headers"] = run.to_headers()
                # Automatic Graph/LLM traces serialize raw state and retrieved text.
                # Keep the explicit metadata-only outer trace; R4 node events
                # provide operational detail without raw payload capture.
                with tracing_context(enabled=False):
                    await self.graph.ainvoke(value, config=config)
                state = await self.get_state(rcept_no, context=context)
                run.add_metadata({"corp_name": state["disclosure"].get("corp_name"),
                                  "risk_level": state.get("risk_level"), "stage": state.get("stage")})
                run.end(outputs={"status": state["status"], "task_ids": state["task_ids"]})
                return state
        finally:
            current_execution.reset(token)

    async def start(self, rcept_no: str, *, context=None):
        if not isinstance(rcept_no, str) or not rcept_no.strip():
            raise ValueError("접수번호가 필요합니다")
        context = self._context(context)
        if self.deps.unified and context.kind != 'public':
            raise ValueError('조직별 검토는 start_review를 사용하세요')
        async with self._lock(self.config(rcept_no, context)['configurable']['thread_id']):
            snapshot = await self.graph.aget_state(self.config(rcept_no, context))
            existing = dict(snapshot.values)
            if existing:
                # 노드 실행 중에 프로세스가 죽은 실행: 다음 노드가 남아 있는데 승인 대기(interrupt)도 아니다.
                # 큐가 같은 공시를 다시 넘겨 줬을 때 조용히 "끝난 것"으로 돌려주면 공시가 사라지므로, 마지막 체크포인트부터 이어서 실행한다.
                crashed = bool(snapshot.next) and not any(t.interrupts for t in snapshot.tasks)
                if crashed:
                    return await self._invoke(rcept_no, None, existing["trace_id"], context)
                if self.deps.unified and existing.get('status') == 'FAILED' and existing.get('error_retryable'):
                    previous_attempt = existing.get('execution_context', {}).get('attempt', 1)
                    if context.attempt != previous_attempt + 1:
                        return existing
                    # Re-run the same failed public job; retain its consumed
                    # budget, never its partially calculated decision/drafts.
                    state = {key: None for key in existing}
                    state.update(initial_state(rcept_no, str(uuid4())),
                                 flag_attempts=0, explanation_attempts=0,
                                 execution_budget=existing.get('execution_budget', {}),
                                 execution_context=context.model_dump(), error_retryable=False)
                    return await self._invoke(rcept_no, state, state['trace_id'], context)
                # 끝났거나 승인 대기인 실행은 암묵적으로 다시 시작하지 않는다.
                return existing
            state = initial_state(rcept_no, str(uuid4()))
            if context:
                state['execution_context'] = context.model_dump()
            return await self._invoke(rcept_no, state, state['trace_id'], context)

    async def start_review(self, rcept_no, public_state, *, context):
        context = self._context(context)
        if self.deps.unified is None or context.kind != 'review':
            raise ValueError('통합 경로의 조직별 검토 context가 필요합니다')
        origin = public_state.get('execution_context', {})
        if (not public_state.get('analysis_complete') or public_state.get('status') != 'DONE'
                or public_state.get('stage') not in ALERT_STAGES
                or public_state.get('disclosure', {}).get('rcept_no') != rcept_no
                or any(origin.get(k) != getattr(context, k) for k in
                       ('assessment_key', 'input_version', 'rules_version', 'as_of'))):
            raise ValueError('동일 판정의 완료된 공개 분석 결과가 필요합니다')
        async with self._lock(self.config(rcept_no, context)['configurable']['thread_id']):
            existing = await self.get_state(rcept_no, context=context)
            if existing:
                await ensure_current(existing, self.deps)
                snapshot = await self.graph.aget_state(self.config(rcept_no, context))
                if snapshot.next and not any(t.interrupts for t in snapshot.tasks):
                    return await self._invoke(rcept_no, None, existing['trace_id'], context)
                return existing
            state = initial_state(rcept_no, str(uuid4()))
            for key in ('disclosure', 'score_result', 'signals', 'evidence', 'verified_sources',
                        'stage', 'risk_level', 'total_score', 'orange_score', 'yellow_score',
                        'combined_ratio', 'ai_explanation', 'assessment_id', 'analysis_complete'):
                if key in public_state:
                    state[key] = retained_data(public_state[key])
            state.update(phase1=True, execution_context=context.model_dump())
            await ensure_current(state, self.deps)
            return await self._invoke(rcept_no, state, state['trace_id'], context)

    async def resume(self, rcept_no: str, batch: ApprovalBatch, pb: PBContext, *, context=None):
        batch, pb = ApprovalBatch.model_validate(batch), PBContext.model_validate(pb)
        context = self._context(context)
        async with self._lock(self.config(rcept_no, context)['configurable']['thread_id']):
            state = await self.get_state(rcept_no, context=context)
            if not state:
                raise KeyError(rcept_no)
            self._pb_scope(state, pb)
            await ensure_current(state, self.deps)
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
            }), state["trace_id"], context)

    async def retry_tasks(self, rcept_no: str, pb: PBContext, *, context=None):
        """Explicitly retry accepted writes after a create_tasks failure.

        Requires an idempotent task store, including writes whose replies were lost.
        Does not reanalyze the disclosure or accept any new approval.
        """
        pb = PBContext.model_validate(pb)
        context = self._context(context)
        async with self._lock(self.config(rcept_no, context)['configurable']['thread_id']):
            state = await self.get_state(rcept_no, context=context)
            self._pb_scope(state, pb)
            await ensure_current(state, self.deps)
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
            await self.graph.aupdate_state(self.config(rcept_no, context),
                                           {"error": None, "status": "RUNNING"},
                                           as_node="save_rejection")
            return await self._invoke(rcept_no, None, state["trace_id"], context)


@asynccontextmanager
async def open_runtime(deps, checkpoint_db=None, *, checkpointer=None):
    if checkpointer is not None:
        if checkpoint_db is not None:
            raise ValueError('체크포인터와 DB 경로를 동시에 지정할 수 없습니다')
        yield AgentRuntime(deps, checkpointer)
        return
    if deps.unified is not None:
        raise ValueError('새 운영 경로는 R4가 생성한 checkpointer를 주입해야 합니다')
    # Compatibility-only local factory for the legacy API and offline tests.
    if checkpoint_db is None:
        from shared.config import get_settings
        checkpoint_db = get_settings().CHECKPOINT_DB
    if str(checkpoint_db).startswith(("postgresql://", "postgres://")):
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:
            raise RuntimeError("PostgreSQL 체크포인터 의존성이 필요합니다. R4에게 langgraph-checkpoint-postgres 추가를 요청하세요") from exc
        async with AsyncPostgresSaver.from_conn_string(str(checkpoint_db)) as saver:
            await saver.setup()
            yield AgentRuntime(deps, saver)
        return
    path = Path(checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield AgentRuntime(deps, saver)
