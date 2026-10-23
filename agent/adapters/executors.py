"""R4 lifecycle/context adapters for explicit R2 public and private entry points.

No legacy Graph fallback: a rcept-only start() cannot enforce tenant isolation.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from agent.adapters.checkpoints import open_checkpointer


@dataclass(frozen=True)
class RuntimeExecutors:
    public: object
    review: object


def public_execution_context(context):
    """Map a claimed R4 job to R2's public context without reading live inputs."""
    from agent.integration import ExecutionContext
    return ExecutionContext(
        assessment_key=context.job_id,
        as_of=context.as_of,
        rules_version=context.rules_digest,
        input_version=context.input_hash,
        facts_version=context.facts_version,
        run_id=context.job_id,
        attempt=context.attempt,
        kind='public',
        model_requests_used=getattr(context, 'execution_budget', {}).get('model_turns', 0),
        active_seconds_used=getattr(context, 'execution_budget', {}).get('consumed_seconds', 0),
    )


@asynccontextmanager
async def open_executors(factory, deps, location):
    """R2 factory(deps, saver) supplies analyze_public(context), draft_review(context)."""
    async with open_checkpointer(location) as saver:
        import inspect
        runtime=factory(deps,saver)
        if inspect.isawaitable(runtime):runtime=await runtime
        if not callable(getattr(runtime,'analyze_public',None)) or not callable(getattr(runtime,'draft_review',None)):
            raise RuntimeError('R2 공개 분석/조직별 검토 진입점 연결이 필요합니다. legacy fallback은 하지 않습니다.')
        from agent.adapters.budgets import ledger_for, record_budget
        if getattr(getattr(deps, 'unified', None), 'record_budget', None) is record_budget:
            async def public(context):
                with ledger_for(context, 'public'):
                    return await runtime.analyze_public(context)
            async def review(context):
                with ledger_for(context, 'review'):
                    return await runtime.draft_review(context)
            yield RuntimeExecutors(public, review)
        else:
            yield RuntimeExecutors(runtime.analyze_public,runtime.draft_review)
