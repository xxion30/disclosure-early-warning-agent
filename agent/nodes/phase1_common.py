"""R2 node helpers for injected, non-LLM rule operations."""
import asyncio

from agent.execution import current_execution, invoke_tool
from agent.ports import AgentError
from shared.schemas import ToolResponse


def routes_for(state, bucket):
    return [r for r in state.get("risk_routes", []) if r["bucket"] == bucket]


async def rule_call(deps, name, payload):
    if deps.phase1 is None:
        raise AgentError("Phase 1 규칙 연결이 필요합니다")

    class BoundTools:
        async def call(self, tool_name, arguments):
            scope = current_execution.get()
            try:
                raw = await asyncio.wait_for(getattr(deps.phase1, tool_name)(arguments), scope.remaining())
            except TimeoutError:
                return ToolResponse(ok=False, error={"code": "TIMEOUT", "message": "규칙 도구 시간 초과"})
            if not isinstance(raw, dict):
                raise AgentError(f"규칙 연결 응답 형식 불일치: {tool_name}")
            return ToolResponse(ok=True, data=raw)

    # Reuse the same-input call budget and TIMEOUT recovery as every MCP call.
    return await invoke_tool(BoundTools(), deps.harness, name, payload)
