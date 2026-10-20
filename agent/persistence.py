"""Optional binding to B's tool once its upsert contract is confirmed."""
from dataclasses import dataclass
from typing import Awaitable, Callable

from agent.ports import AgentError
from agent.execution import invoke_tool
from shared.schemas import SaveAssessmentInput, CreateContactTaskInput


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
