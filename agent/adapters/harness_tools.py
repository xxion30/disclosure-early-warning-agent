"""도구 쪽 Harness 연결과 기본 Harness 묶음 (담당: R4): MCP 도구 TIMEOUT 재시도, 이벤트 기록, default_harness.

assembly.py에서 분리했다. 모델 쪽(prepare_input·recover_llm 등)은 agent/harness_llm.py(R2).
"""
from __future__ import annotations

import logging

from agent.adapters.bindings import HarnessBindings
from agent.harness_llm import check_message, prepare_input, recover_llm
from harness.tracing import audit_system
from shared.schemas import ToolResponse

log = logging.getLogger("agent.adapters")


async def recover_tool(operation) -> ToolResponse:
    """MCP 도구 TIMEOUT은 1회 재시도, 나머지 오류는 그대로 돌려준다."""
    result = await operation()
    if not result.ok and result.error and result.error.code == "TIMEOUT":
        await audit_system("tool_timeout_retry", None, result.error.message)
        result = await operation()
    if not result.ok:
        await audit_system("tool_failed", None, result.error)
    return result


async def event(name: str, payload: dict) -> None:
    log.info("agent_event %s %s", name, {k: v for k, v in payload.items() if k != "error"})


def default_harness() -> HarnessBindings:
    return HarnessBindings(
        prepare_input=prepare_input, check_message=check_message, recover_llm=recover_llm,
        recover_tool=recover_tool, event=event,
    )
