"""Lease-fenced R4 execution ledger, including private graph claim changes."""
from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass, field
from math import isfinite
import hashlib
import json

from agent.execution import current_execution
from agent.ports import AgentError
from shared.analysis import locked_job, utcnow, LostLease
from shared.db import get_session


@dataclass
class Ledger:
    kind: str
    identifier: str
    lease_token: str = field(repr=False)
    tenant_id: str | None = None
    session_factory: object = get_session
    scope: object = field(default=None, repr=False)

    def row(self, db):
        if self.kind == 'public':
            return locked_job(db, self.identifier, self.lease_token)
        from mcp_servers.business.reviews import scoped_review
        row = scoped_review(db, self.identifier, self.tenant_id, lock=True)
        if (row.status != 'RUNNING' or row.lease_token != self.lease_token
                or not row.lease_expires_at or row.lease_expires_at <= utcnow().isoformat()):
            raise LostLease('검토 실행 예산 기록의 임대 상실')
        return row

    def attach(self):
        scope = current_execution.get()
        if self.scope is scope:
            return scope
        with self.session_factory() as db:
            old = dict(self.row(db).execution_budget or {})
        scope.model_turns = max(scope.model_turns, old.get('model_turns', 0))
        scope.consumed_seconds = max(scope.consumed_seconds, old.get('consumed_seconds', 0))
        for key, count in old.get('repeated_calls', {}).items():
            scope.repeated_calls[key] = max(scope.repeated_calls.get(key, 0), count)
        scope.record_budget = record_budget
        self.scope = scope
        scope.remaining()
        return scope

    def save(self, *, final=False):
        scope = self.scope if final else self.attach()
        if scope is None:
            return
        data = scope.snapshot()
        if (type(data['model_turns']) is not int or data['model_turns'] < 0
                or not isfinite(data['consumed_seconds']) or data['consumed_seconds'] < 0):
            raise AgentError('실행 예산 값 오류')
        with self.session_factory() as db:
            row = self.row(db)
            previous = row.execution_budget or {}
            row.execution_budget = {
                'tool_requests': previous.get('tool_requests', {}),
                'model_turns': max(previous.get('model_turns', 0), data['model_turns']),
                'consumed_seconds': max(previous.get('consumed_seconds', 0), data['consumed_seconds']),
                'repeated_calls': {key: max(previous.get('repeated_calls', {}).get(key, 0),
                                           data['repeated_calls'].get(key, 0))
                                   for key in previous.get('repeated_calls', {}).keys() | data['repeated_calls'].keys()},
            }

    def reserve_tool(self, name, arguments):
        from harness.loop_limits import MAX_SAME_TOOL
        # RAG holder is an invocation lease, not a semantic search argument.
        arguments = {k: v for k, v in arguments.items() if k != 'holder'}
        key = name + ':' + hashlib.sha256(json.dumps(arguments, sort_keys=True,
                                                     ensure_ascii=False).encode()).hexdigest()
        with self.session_factory() as db:
            row = self.row(db)
            data = dict(row.execution_budget or {})
            counts = dict(data.get('tool_requests', {}))
            if counts.get(key, 0) >= MAX_SAME_TOOL:
                raise AgentError('동일 입력 도구 호출의 누적 한도 초과')
            counts[key] = counts.get(key, 0) + 1
            row.execution_budget = {**data, 'tool_requests': counts}


active_ledger = ContextVar('r4_execution_ledger', default=None)


async def record_budget(payload):
    ledger = active_ledger.get()
    if ledger is None:
        raise AgentError('R4가 선점한 실행의 예산 장부가 필요합니다')
    ledger.save()


@contextmanager
def ledger_for(context, kind, *, session_factory=get_session):
    token = getattr(context, 'lease_token', None)
    if not token:
        raise AgentError('선점 토큰 없는 운영 실행은 허용하지 않습니다')
    ledger = Ledger(kind, context.job_id if kind == 'public' else context.review_id, token,
                    getattr(context, 'tenant_id', None), session_factory)
    # Verify even a cached graph result before handing it to the worker.
    with session_factory() as db:
        ledger.row(db)
    handle = active_ledger.set(ledger)
    try:
        yield
    finally:
        try:
            # R2 has reset its ContextVar, but the owned scope remains available.
            ledger.save(final=True)
        finally:
            active_ledger.reset(handle)


class BudgetTools:
    def __init__(self, inner):
        self.inner = inner

    async def call(self, name, arguments):
        if name == 'release_disclosure_index':
            # Holder-bound cleanup must remain possible after budget exhaustion
            # or a lost worker lease; R3 owns holder fencing and its finite TTL.
            return await self.inner.call(name, arguments)
        ledger = active_ledger.get()
        if ledger is None:
            raise AgentError('운영 도구는 R4 실행기에서 호출해야 합니다')
        ledger.attach()
        ledger.save()
        ledger.reserve_tool(name, arguments)
        try:
            return await self.inner.call(name, arguments)
        finally:
            ledger.save()


class BudgetModel:
    """Preserve StructuredLLM's per-provider accounting; cover custom models too."""
    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, key):
        return getattr(self.inner, key)

    async def generate(self, prompt, schema, payload):
        ledger = active_ledger.get()
        if ledger is None:
            raise AgentError('운영 모델은 R4 실행기에서 호출해야 합니다')
        ledger.attach()
        ledger.save()
        try:
            return await self.inner.generate(prompt, schema, payload)
        finally:
            ledger.save()
