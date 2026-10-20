"""agent.Dependencies 조립: B의 MCP 도구 + A의 모델 + D의 Harness + B의 저장 도구를 한곳에서 묶는다.

API(`api/graph_adapter.default_dependencies`)는 여기서 만든 deps를 받기만 한다.
각 조각의 경계는 agent/INTEGRATION.md, 도구 계약은 CLAUDE.md 4장과 mcp_servers/business/README.md.

    deps = await build_dependencies()          # business-mcp(8001)·rag-mcp(8002)가 떠 있어야 한다
    deps = await build_dependencies(model=...)  # 테스트: 모델(또는 tools)만 바꿔 끼운다
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import select

from agent.execution import invoke_tool
from agent.harness_adapter import HarnessBindings
from agent.persistence import ToolAssessmentStore, ToolTaskCreator
from agent.ports import AgentError, Dependencies
from agent.rules import load_classifier
from harness import context as ctx
from harness import guardrails, masking, recovery
from harness.tracing import write_audit_log
from mcp_servers.business.rules.loader import DEFAULT_PATH
from shared.config import get_settings
from shared.db import get_session
from shared.models import AuditLog, Customer, RiskAssessment
from shared.schemas import ToolResponse

log = logging.getLogger("agent.assembly")


# ------------------------------------------------------------------ Harness (D의 모듈을 A의 async 계약에 맞춤)
def _trace_id() -> str | None:
    """진행 중인 LangSmith trace의 root id (없으면 None)."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(run.trace_id) if run is not None else None
    except Exception:  # noqa: BLE001 — 관측 실패가 본 작업을 막으면 안 된다
        return None


async def _audit_system(action: str, target: str | None, error: Any, **extra: Any) -> None:
    try:
        await asyncio.to_thread(
            write_audit_log, "system", action, target, {"error": str(error), **extra}, _trace_id()
        )
    except Exception:  # noqa: BLE001
        log.exception("audit_logs 기록 실패: %s", action)


def _shrink_strings(obj: Any, limit: int) -> Any:
    if isinstance(obj, dict):
        return {k: _shrink_strings(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_shrink_strings(v, limit) for v in obj]
    if isinstance(obj, str) and len(obj) > limit:
        return ctx.truncate_tool_result(obj, limit)
    return obj


def _longest_string(obj: Any) -> int:
    if isinstance(obj, dict):
        return max((_longest_string(v) for v in obj.values()), default=0)
    if isinstance(obj, list):
        return max((_longest_string(v) for v in obj), default=0)
    return len(obj) if isinstance(obj, str) else 0


async def prepare_input(payload: dict) -> dict:
    """모델에 보내기 직전: 개인정보 마스킹 → 긴 문자열 4,000자 자르기 → 입력 6,000토큰 안으로 줄이기."""
    prepared = _shrink_strings(masking.mask_obj(payload), ctx.TOOL_RESULT_MAX_CHARS)
    limit = ctx.TOOL_RESULT_MAX_CHARS
    for _ in range(12):
        if ctx.estimate_tokens(json.dumps(prepared, ensure_ascii=False)) <= ctx.TOKEN_BUDGET:
            break
        limit = max(50, min(limit, _longest_string(prepared)) // 2)
        prepared = _shrink_strings(prepared, limit)
    return prepared


async def check_message(message: str) -> list[str]:
    return guardrails.check_message(message)


async def recover_llm(primary, fallback):
    """429·503이면 1초·3초 간격 2회 재시도 → 대체 모델 → 그래도 실패하면 LLMUnavailable (D의 recovery 규칙).

    FAKE_LLM_ERROR 주입은 agent.llm.StructuredLLM이 이미 하므로 여기서는 하지 않는다(이중 주입 방지).
    """
    last: BaseException | None = None
    delays = recovery.LLM_RETRY_DELAYS
    for attempt in range(len(delays) + 1):
        try:
            return await primary()
        except Exception as exc:  # noqa: BLE001
            if not recovery.is_retryable(exc):
                raise
            last = exc
            await _audit_system("llm_retry", None, exc, attempt=attempt + 1)
            if attempt < len(delays):
                await asyncio.sleep(delays[attempt])
    await _audit_system("llm_fallback", None, last)
    try:
        return await fallback()
    except Exception as exc:  # noqa: BLE001
        last = exc
    await _audit_system("llm_failed", None, last)
    raise recovery.LLMUnavailable(str(last)) from last


async def recover_tool(operation) -> ToolResponse:
    """MCP 도구 TIMEOUT은 1회 재시도, 나머지 오류는 그대로 돌려준다."""
    result = await operation()
    if not result.ok and result.error and result.error.code == "TIMEOUT":
        await _audit_system("tool_timeout_retry", None, result.error.message)
        result = await operation()
    if not result.ok:
        await _audit_system("tool_failed", None, result.error)
    return result


async def event(name: str, payload: dict) -> None:
    log.info("agent_event %s %s", name, {k: v for k, v in payload.items() if k != "error"})


def default_harness() -> HarnessBindings:
    return HarnessBindings(
        prepare_input=prepare_input, check_message=check_message, recover_llm=recover_llm,
        recover_tool=recover_tool, event=event,
    )


# ------------------------------------------------------------------ 저장·감사·업무 생성
class AssessmentStore(ToolAssessmentStore):
    """A의 ToolAssessmentStore + 점수 계산 전에 멈춘 평가(MANUAL_CHECK·FAILED)의 저장.

    save_assessment 도구는 rcept_no 기준 upsert이고, 점수 없이도 RUNNING·FAILED·MANUAL_CHECK로 저장된다.
    """

    async def save(self, state, status):
        if "score_result" in state:
            return await super().save(state, status)
        disclosure = state.get("disclosure") or {}
        if not disclosure.get("corp_code") or not disclosure.get("rcept_no"):
            raise AgentError("공시 정보를 받기 전에 실패해 평가를 저장할 수 없습니다")
        result = await invoke_tool(self.tools, self.harness, "save_assessment", {
            "corp_code": disclosure["corp_code"], "rcept_no": disclosure["rcept_no"], "status": status,
        })
        return result["assessment_id"]


def _write_audit(state: dict, action: str, payload: dict) -> None:
    key = payload.get("idempotency_key")
    target = (state.get("disclosure") or {}).get("rcept_no")
    if key:
        with get_session() as s:
            exists = s.scalars(
                select(AuditLog.log_id).where(
                    AuditLog.action == action, AuditLog.payload["idempotency_key"].as_string() == key
                )
            ).first()
        if exists:
            return  # 재개로 같은 노드가 다시 실행돼도 한 번만 남긴다
    write_audit_log(payload.get("pb_id") or "agent", action, target, payload, state.get("trace_id"))


async def audit(state: dict, action: str, payload: dict) -> None:
    await asyncio.to_thread(_write_audit, state, action, payload)


def _owns(pb_id: str, customer_id: str, assessment_id: int) -> bool:
    with get_session() as s:
        customer = s.get(Customer, customer_id)
        return bool(customer and customer.pb_id == pb_id and s.get(RiskAssessment, assessment_id))


async def authorize_task(pb_id: str, customer_id: str, assessment_id: int) -> bool:
    """담당 PB의 고객이고 존재하는 평가일 때만 업무 생성을 허용한다 (API도 먼저 검사한다)."""
    return await asyncio.to_thread(_owns, pb_id, customer_id, assessment_id)


# ------------------------------------------------------------------ 사실 → 규칙 입력
def signal_input(extracted: dict) -> list[dict]:
    """검증된 사실을 calculate_risk_score의 new_signals로 바꾼다. 점수는 넣지 않는다(B 규칙표가 정한다)."""
    signals = []
    for fact in extracted.get("facts", []):
        evidence = list(fact.get("evidence") or [])
        signals.append({
            "signal_type": fact["signal_type"],
            "detail": dict(fact.get("values") or {}),
            "evidence": evidence,
            "rcept_no": evidence[0]["rcept_no"] if evidence else None,
        })
    return signals


# ------------------------------------------------------------------ 조립
async def build_dependencies(settings=None, *, tools=None, model=None, harness=None) -> Dependencies:
    """tools·model·harness를 비우면 운영 구성(MCP 서버 2개, Anthropic/OpenAI 모델, D의 Harness)을 만든다."""
    settings = settings or get_settings()
    harness = harness or default_harness()
    if tools is None:
        from agent.mcp_client import MCPTools

        tools = await MCPTools.connect(settings)
    if model is None:
        from agent.llm import StructuredLLM

        model = StructuredLLM.from_settings(harness, settings)
    return Dependencies(
        tools=tools,
        model=model,
        harness=harness,
        persistence=AssessmentStore(tools, audit, harness, upsert_confirmed=True),
        classify=load_classifier(DEFAULT_PATH),
        signal_input=signal_input,
        create_task=ToolTaskCreator(tools, harness, authorize_task, idempotency_confirmed=True),
    )
