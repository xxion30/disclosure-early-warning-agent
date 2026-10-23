"""R2 factory for R4 open_executors; no API, DB or rule-engine imports.

R4 supplies approved UnifiedBindings and owns publication, review authorization,
leases and decisions. This module never creates a contact task or checkpoint DB.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context
from pydantic import Field

from agent.evidence import retained_data
from agent.execution import Execution, current_execution
from agent.integration import ExecutionContext
from agent.nodes.draft_messages import draft_messages
from agent.nodes.guardrail_check import guardrail_check, message_flags
from agent.phase1 import ALERT_STAGES
from agent.ports import AgentError
from agent.runtime import AgentRuntime
from agent.state import AgentState, StrictModel, initial_state
from harness.guardrails import check_explanation


class ReviewInput(StrictModel):
    review_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    assessment_id: str = Field(min_length=1)
    thread_id: str = Field(min_length=1)
    version: int = Field(ge=1, strict=True)
    public_result: dict
    explanation: str = Field(min_length=1)
    customers: tuple[dict, ...]
    existing_drafts: dict[str, str]
    decisions: dict[str, dict]
    rewrite_customers: tuple[str, ...]


def _mapping(context, fields):
    return ({key: context[key] for key in fields} if isinstance(context, dict)
            else {key: getattr(context, key) for key in fields})


class HostedRuntime:
    def __init__(self, deps, saver):
        if saver is None:
            raise ValueError('R4가 초기화한 checkpointer를 주입해야 합니다')
        if deps.unified is None or any(not callable(getattr(deps.unified, k, None)) for k in
                                       ('classify', 'prepare_signals', 'ensure_current')):
            raise ValueError('R4 조립에서 UnifiedBindings를 주입해야 합니다. 구형 흐름으로 우회하지 않습니다')
        self.deps = replace(deps, phase1=None, defer_publication=True)
        self.runtime = AgentRuntime(self.deps, saver)
        self._locks = {}
        graph = StateGraph(AgentState)

        async def draft(state):
            scope = current_execution.get()
            update = await asyncio.wait_for(draft_messages(state, self.deps), scope.remaining())
            return {**update, 'execution_budget': scope.snapshot()}

        async def check(state):
            scope = current_execution.get()
            update = await asyncio.wait_for(guardrail_check(state, self.deps), scope.remaining())
            status = ('MANUAL_CHECK' if update['manual_drafts'] else
                      'RUNNING' if update['rewrite_customers'] else 'WAITING_APPROVAL')
            return {**update, 'status': status, 'execution_budget': scope.snapshot()}

        graph.add_node('draft', draft)
        graph.add_node('check', check)
        graph.add_edge(START, 'draft')
        graph.add_edge('draft', 'check')
        graph.add_conditional_edges('check', lambda s: 'draft' if s['status'] == 'RUNNING' else END)
        self.review_graph = graph.compile(checkpointer=saver)

    async def analyze_public(self, context):
        """Accept the claimed AnalysisContext. Publication happens in R4 worker."""
        receipt = context.rcept_no if not isinstance(context, dict) else context['rcept_no']
        supplied = context.execution_context if not isinstance(context, dict) else context['execution_context']
        trusted = ExecutionContext.model_validate(supplied)
        if trusted.kind != 'public' or trusted.tenant_id is not None:
            raise ValueError('공개 분석에는 공개 실행 문맥이 필요합니다')
        state = await self.runtime.start(receipt, context=trusted)
        if state['status'] == 'FAILED' and state.get('error_retryable'):
            # R4 AnalysisWorker recognizes built-in TimeoutError as retryable.
            # Never import its worker exception classes into agent/.
            raise TimeoutError('공개 분석의 일시적 외부 호출 실패')
        if state['status'] != 'DONE' or not state.get('analysis_complete'):
            return {'status': 'MANUAL_CHECK'}
        return {'status': 'DONE', 'score_result': retained_data(state['score_result']),
                'ai_explanation': state['ai_explanation']}

    async def draft_review(self, context):
        """Return drafts only. R4 run_review saves them; R4 decide owns approval.

        Unlike legacy start_review, this entry point must not write the old
        assessment store or interrupt for approvals already owned by R4 DB.
        """
        data = ReviewInput.model_validate(_mapping(context, ReviewInput.model_fields))
        if data.public_result.get('stage') not in ALERT_STAGES:
            raise ValueError('조직별 초안에는 경보 판정이 필요합니다')
        if check_explanation(data.explanation):
            raise ValueError('공개 설명문 검사 실패')
        from shared.schemas import AffectedCustomer
        customers = [AffectedCustomer.model_validate(row).model_dump() for row in data.customers]
        ids = {c['customer_id'] for c in customers}
        if len(ids) != len(customers) or not ids:
            raise ValueError('중복 없는 보유 고객이 필요합니다')
        if not set(data.existing_drafts) <= ids or not set(data.decisions) <= ids:
            raise ValueError('다른 고객의 초안·결정이 섞여 있습니다')
        if not set(data.rewrite_customers) <= ids - set(data.decisions):
            raise ValueError('재작성 대상은 미결정 보유 고객이어야 합니다')
        if not set(data.decisions) <= set(data.existing_drafts):
            raise ValueError('기존 결정의 안내문이 누락됐습니다')
        # A semantic change or new R4 claim version cannot reuse old drafts.
        identity = data.model_dump(mode='json')
        digest = sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                   separators=(',', ':'), allow_nan=False).encode()).hexdigest()
        config = {'configurable': {'thread_id': 'dartch:hosted-review:' + digest}, 'recursion_limit': 32}
        lock = self._locks.setdefault(digest, asyncio.Lock())
        async with lock:
            with tracing_context(enabled=False):
                snapshot = await self.review_graph.aget_state(config)
            old = dict(snapshot.values)
            if old and not snapshot.next:
                return await self._review_output(old, data)
            budget = old.get('execution_budget', {})
            scope = Execution(self.deps.execution_seconds, self.deps.max_model_turns,
                              model_turns=budget.get('model_turns', 0),
                              consumed_seconds=budget.get('consumed_seconds', 0),
                              repeated_calls=dict(budget.get('repeated_calls', {})))
            token = current_execution.set(scope)
            try:
                state = initial_state('', str(uuid4()))
                # ReviewContext has no primary receipt/title. Do not fabricate
                # those fields or perform a live public/disclosure lookup.
                state.update(disclosure={k: data.public_result[k] for k in ('corp_code',) if k in data.public_result},
                             score_result=retained_data(data.public_result), ai_explanation=data.explanation,
                             affected_customers=customers, drafts=deepcopy(data.existing_drafts),
                             decisions=deepcopy(data.decisions), rewrite_customers=list(data.rewrite_customers))
                with tracing_context(enabled=False):
                    await self.review_graph.ainvoke(None if old else state, config=config)
                    result = dict((await self.review_graph.aget_state(config)).values)
                return await self._review_output(result, data)
            except BaseException:
                # Keep usage after a provider/validation failure or cancellation.
                # No DB write and no raw exception/body goes into this snapshot.
                try:
                    with tracing_context(enabled=False):
                        await asyncio.wait_for(self.review_graph.aupdate_state(config,
                            {'status': 'MANUAL_CHECK', 'execution_budget': scope.snapshot()}), timeout=3)
                except Exception:
                    pass
                raise
            finally:
                current_execution.reset(token)

    async def _review_output(self, state, context):
        drafts = state.get('drafts', {})
        if state.get('status') != 'WAITING_APPROVAL' or set(drafts) != {c['customer_id'] for c in context.customers}:
            raise AgentError('보유 고객 전원의 안내문 검증이 필요합니다')
        for cid, message in drafts.items():
            if await message_flags(message, self.deps):
                raise AgentError('검증되지 않은 안내문은 반환할 수 없습니다')
            if cid in context.decisions and message != context.existing_drafts[cid]:
                raise AgentError('이미 결정한 고객의 안내문이 변경됐습니다')
        return {'drafts': dict(drafts)}


def build_runtime(deps, saver):
    """Set PHASE1_RUNTIME_FACTORY=agent.hosted:build_runtime AFTER R4 wiring."""
    return HostedRuntime(deps, saver)
