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


@asynccontextmanager
async def open_executors(factory, deps, location):
    """R2 factory(deps, saver) supplies analyze_public(context), draft_review(context)."""
    async with open_checkpointer(location) as saver:
        import inspect
        runtime=factory(deps,saver)
        if inspect.isawaitable(runtime):runtime=await runtime
        if not callable(getattr(runtime,'analyze_public',None)) or not callable(getattr(runtime,'draft_review',None)):
            raise RuntimeError('R2 공개 분석/조직별 검토 진입점 연결이 필요합니다. legacy fallback은 하지 않습니다.')
        yield RuntimeExecutors(runtime.analyze_public,runtime.draft_review)
