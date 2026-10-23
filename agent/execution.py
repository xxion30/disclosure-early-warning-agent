"""Per-invocation budgets; human waiting is outside this context."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import time

from langgraph.errors import GraphInterrupt

from agent.ports import AgentError
from harness.loop_limits import MAX_SAME_TOOL
from shared.schemas import ToolResponse


@dataclass
class Execution:
    seconds: float
    max_model_turns: int
    started: float = field(default_factory=time.monotonic)
    model_turns: int = 0
    repeated_calls: dict[str, int] = field(default_factory=dict)
    tool_counts: dict[str, int] = field(default_factory=dict)
    consumed_seconds: float = 0
    record_budget: object = None
    budget_context: dict = field(default_factory=dict)
    model_generation_calls: int = 0
    provider_counted: bool = False

    def remaining(self):
        remaining = self.seconds - self.consumed_seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise AgentError("실행 시간 한도 초과")
        return remaining

    def snapshot(self):
        return {'model_turns': self.model_turns,
                'consumed_seconds': self.consumed_seconds + time.monotonic() - self.started,
                'repeated_calls': dict(self.repeated_calls)}

    async def persist_budget(self):
        if self.record_budget is not None:
            await asyncio.wait_for(self.record_budget({'context': self.budget_context,
                                                       'budget': self.snapshot()}), self.remaining())

    async def reserve_model(self):
        self.remaining()
        if self.model_turns >= self.max_model_turns:
            raise AgentError('AI 반복 한도 초과')
        self.model_turns += 1
        await self.persist_budget()


current_execution: ContextVar[Execution] = ContextVar("agent_execution")


async def invoke_tool(tools, harness, name, arguments):
    scope = current_execution.get()
    key = name + ":" + hashlib.sha256(
        json.dumps(arguments, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()

    async def operation():
        scope.remaining()
        count = scope.repeated_calls.get(key, 0) + 1
        if count > MAX_SAME_TOOL:
            raise AgentError(f"동일 입력 도구 호출 한도 초과: {name}")
        scope.repeated_calls[key] = count
        scope.tool_counts[name] = scope.tool_counts.get(name, 0) + 1
        await scope.persist_budget()
        result = ToolResponse.model_validate(await tools.call(name, arguments))
        return result

    result = await harness.recover_tool(operation)
    result = ToolResponse.model_validate(result)
    if not result.ok:
        code = result.error.code if result.error else "INVALID_RESPONSE"
        # Business error messages can contain raw customer data; do not copy them to state.
        retryable = bool(scope.budget_context) and code in {'TIMEOUT', 'UPSTREAM_ERROR'}
        raise AgentError(f"MCP 도구 실패: {name} ({code})",
                         'FAILED' if retryable else 'MANUAL_CHECK', retryable=retryable)
    return result.data


async def call_tool(deps, name, arguments):
    return await invoke_tool(deps.tools, deps.harness, name, arguments)


async def generate(deps, prompt, schema, payload):
    scope = current_execution.get()
    scope.remaining()
    scope.model_generation_calls += 1
    # StructuredLLM counts each provider request, including retries/fallback.
    # Fake or custom models reserve one request per generate call here.
    if not getattr(deps.model, 'counts_provider_requests', False):
        await scope.reserve_model()
    prepared = await deps.harness.prepare_input(payload)
    if not isinstance(prepared, dict):
        raise AgentError("Harness 입력 준비 결과는 dict여야 합니다")
    from langsmith import tracing_context
    with tracing_context(enabled=False):
        result = await deps.model.generate(prompt, schema, prepared)
    return schema.model_validate(result)


def guarded(name, operation, deps):
    async def node(state):
        scope = current_execution.get()
        before = dict(scope.tool_counts)
        try:
            await deps.harness.event("node_started", {
                "node": name, "rcept_no": state["disclosure"]["rcept_no"],
                "trace_id": state["trace_id"],
            })
            result = await asyncio.wait_for(operation(state), timeout=scope.remaining())
            result = dict(result or {})
            result["last_node"] = name
            await deps.harness.event("node_finished", {
                "node": name, "trace_id": state["trace_id"],
            })
        except GraphInterrupt:
            raise
        except (AgentError, asyncio.TimeoutError) as exc:
            result = {"error": str(exc) or "실행 시간 한도 초과",
                      "status": exc.status if isinstance(exc, AgentError) else "MANUAL_CHECK",
                      "error_retryable": getattr(exc, 'retryable', False),
                      "last_node": name}
        except Exception as exc:
            # Raw exceptions may include API credentials or PII.
            result = {"error": f"{name} 실패: {type(exc).__name__}",
                      "status": "MANUAL_CHECK", "last_node": name}
        counts = dict(result.get("tool_call_counts", state.get("tool_call_counts", {})))
        for tool, value in scope.tool_counts.items():
            counts[tool] = counts.get(tool, 0) + value - before.get(tool, 0)
        result["tool_call_counts"] = counts
        if state.get('execution_context'):
            result['execution_budget'] = scope.snapshot()
            try:
                await scope.persist_budget()
            except Exception:
                result.update(error='실행 예산 기록 실패', status='MANUAL_CHECK', error_retryable=False)
        return result
    return node
