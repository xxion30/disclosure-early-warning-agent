"""평가 저장·감사 기록·업무 생성 연결 (담당: R4). persistence.py와 assembly.py의 저장 부분을 합쳤다."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select

from agent.execution import invoke_tool
from agent.ports import AgentError
from harness.tracing import write_audit_log
from shared.config import get_settings
from shared.db import get_session
from shared.models import AuditLog, Customer, RiskAssessment
from shared.schemas import CreateContactTaskInput, SaveAssessmentInput

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


class ToolTaskCreator:
    """C rechecks PB ownership; B guarantees unique assessment/customer writes."""
    def __init__(self, tools, harness, authorize, *, idempotency_confirmed=False):
        if not idempotency_confirmed:
            raise ValueError("B의 평가·고객별 업무 생성 멱등성 확인이 필요합니다")
        self.tools, self.harness, self.authorize = tools, harness, authorize

    async def __call__(self, payload):
        if not await self.authorize(payload["pb_id"], payload["customer_id"], payload["assessment_id"]):
            raise PermissionError("업무 생성 권한 없음")
        arguments = CreateContactTaskInput.model_validate({
            key: payload[key] for key in CreateContactTaskInput.model_fields
        })
        result = await invoke_tool(self.tools, self.harness, "create_contact_task", arguments.model_dump())
        return result["task_id"]


class AssessmentStore(ToolAssessmentStore):
    """A의 ToolAssessmentStore + 점수 계산 전에 멈춘 평가(MANUAL_CHECK·FAILED)의 저장.

    save_assessment 도구는 rcept_no 기준 upsert이고, 점수 없이도 RUNNING·FAILED·MANUAL_CHECK로 저장된다.
    """

    async def save(self, state, status):
        assessment_id = await self._save(state, status)
        await self._release_after_save(state, status)
        return assessment_id

    async def _release_after_save(self, state, status):
        """운영(RAG_INDEX_MODE=ephemeral): 경보를 저장한 뒤 이 공시의 임시 색인과 DB 본문을 지운다.
        실패(MANUAL_CHECK·FAILED)는 해결될 때까지 남겨 두고, 정리가 실패해도 처리 결과는 바꾸지 않는다."""
        if status not in ("WAITING_APPROVAL", "DONE") or get_settings().RAG_INDEX_MODE != "ephemeral":
            return
        rcept_no = (state.get("disclosure") or {}).get("rcept_no")
        if not rcept_no:
            return
        try:
            released = await invoke_tool(self.tools, self.harness, "release_disclosure_index", {"rcept_no": rcept_no})
            await invoke_tool(self.tools, self.harness, "purge_disclosure_text",
                              {"rcept_no": rcept_no, "content_hash": released.get("content_sha256")})
        except Exception as exc:  # noqa: BLE001 — 정리는 부가 작업이라 평가 저장을 되돌리지 않는다
            log.warning("처리 후 색인·본문 정리 실패 %s: %s: %s", rcept_no, type(exc).__name__, exc)

    async def _save(self, state, status):
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
