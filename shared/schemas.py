"""도구 입출력, 공통 응답 형식, 오류 코드 (CLAUDE.md 4-2 ~ 4-4장)."""
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

Level = Literal["Low", "Medium", "High", "Critical"]
Priority = Literal["P1", "P2", "P3"]
AssessmentStatus = Literal["RUNNING", "WAITING_APPROVAL", "DONE", "FAILED", "MANUAL_CHECK"]
TaskStatus = Literal["승인대기", "승인", "반려", "연락완료", "보류"]
RefCategory = Literal["상장규정", "공시해설", "회계용어", "안내문가이드"]


class ErrorCode(str, Enum):
    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    DB_ERROR = "DB_ERROR"
    TIMEOUT = "TIMEOUT"


# ---- 공통 응답 형식: {"ok": true, "data": ...} / {"ok": false, "error": {...}} ----
def ok(data: Any = None) -> dict:
    return {"ok": True, "data": data if data is not None else {}}


def fail(code: ErrorCode | str, message: str) -> dict:
    code_value = code.value if isinstance(code, ErrorCode) else code
    return {"ok": False, "error": {"code": code_value, "message": message}}


class ToolError(BaseModel):
    code: ErrorCode
    message: str


class ToolResponse(BaseModel):
    ok: bool
    data: Any = None
    error: ToolError | None = None


# ---- 신호 / 점수 ----
class Signal(BaseModel):
    signal_type: str
    points: float
    detail: dict = Field(default_factory=dict)
    filed_at: str | None = None


class ScoreResult(BaseModel):
    total_score: float
    level: Level
    combo_upgraded: bool = False
    breakdown: list[dict] = Field(default_factory=list)


# ---- 고객 영향도 ----
class CustomerImpact(BaseModel):
    customer_id: str
    impact_score: int
    impact_level: Literal["낮음", "보통", "높음"]
    priority: Priority
    reasons: dict = Field(default_factory=dict)  # 요소별 점수


# ---- 도구 입력 (4-2장) ----
class SaveAssessmentInput(BaseModel):
    corp_code: str
    rcept_no: str
    signals: list[Signal]
    score_result: ScoreResult
    ai_explanation: str | None = None
    status: AssessmentStatus


class CreateContactTaskInput(BaseModel):
    assessment_id: int
    customer_id: str
    priority: Priority
    draft_message: str


class UpdateTaskStatusInput(BaseModel):
    task_id: int
    status: TaskStatus
    note: str | None = None
