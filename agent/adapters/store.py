"""평가 저장·감사 기록 연결 (담당: R4). persistence.py와 assembly.py의 저장 부분을 합쳤다."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select

from agent.execution import invoke_tool
from agent.ports import AgentError
from harness.tracing import write_audit_log
from shared.db import get_session
from shared.models import AuditLog
from shared.schemas import SaveAssessmentInput

log = logging.getLogger("agent.adapters")


@dataclass(frozen=True)
class PersistenceBindings:
    save: Callable[[dict, str], Awaitable[int]]
    audit: Callable[[dict, str, dict], Awaitable[None]]


class ToolAssessmentStore:
    def __init__(self, tools, audit, harness=None, *, upsert_confirmed: bool = False):
        if not upsert_confirmed:
            raise ValueError("B의 rcept_no 기준 upsert 계약 확인이 필요합니다")
        if harness is None:
            raise ValueError("Harness 연결이 필요합니다")
        self.tools, self.audit, self.harness = tools, audit, harness

    async def save(self, state, status):
        if "score_result" not in state:
            raise AgentError("미계산 평가의 실패 저장 계약은 별도 binding이 필요합니다")
        payload = SaveAssessmentInput(
            corp_code=state["disclosure"]["corp_code"], rcept_no=state["disclosure"]["rcept_no"],
            signals=state["signals"], score_result=state["score_result"],
            ai_explanation=state.get("ai_explanation"), status=status,
        )
        result = await invoke_tool(self.tools, self.harness, "save_assessment", payload.model_dump())
        assessment_id = result["assessment_id"]
        if not isinstance(assessment_id, int) or isinstance(assessment_id, bool) or assessment_id <= 0:
            raise AgentError("평가 ID 형식 불일치")
        if state.get("assessment_id", assessment_id) != assessment_id:
            raise AgentError("평가 갱신에서 ID 변경 감지")
        return assessment_id


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
