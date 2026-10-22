"""위험 평가·감사 기록 테이블. (담당: R4)"""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base

ASSESSMENT_STATUSES = ("RUNNING", "DONE", "FAILED", "MANUAL_CHECK")  # 승인 대기(WAITING_APPROVAL)는 연락 기능과 함께 삭제됐다
LEVELS = ("Low", "Medium", "High", "Critical")  # 🔴/🟠/🟡 단계로 바뀔 예정 (docs/dartch_phase1_mvp.md)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    assessment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey("companies.corp_code"), index=True)
    rcept_no: Mapped[str] = mapped_column(String, index=True)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    level: Mapped[str] = mapped_column(String, default="Low")
    combo_upgraded: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    ai_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="RUNNING")
    created_at: Mapped[str] = mapped_column(String)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
