"""위험 평가·업무·감사 기록 테이블. (담당: R4)"""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from shared.models.base import Base

ASSESSMENT_STATUSES = ("RUNNING", "WAITING_APPROVAL", "DONE", "FAILED", "MANUAL_CHECK")
TASK_STATUSES = ("승인대기", "승인", "반려", "연락완료", "보류")
STAGES = ("RED", "ORANGE", "YELLOW", "COMPOSITE", "WATCH", "NONE")
LEVELS = ("Low", "Medium", "High", "Critical")


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    assessment_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey("companies.corp_code"), index=True)
    rcept_no: Mapped[str] = mapped_column(String, index=True)
    stage: Mapped[str] = mapped_column(String, default='NONE', server_default='NONE')
    total_score: Mapped[float] = mapped_column(Float, default=0)
    level: Mapped[str] = mapped_column(String, default="Low")
    combo_upgraded: Mapped[bool] = mapped_column(Boolean, default=False)
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    ai_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, default="RUNNING")
    created_at: Mapped[str] = mapped_column(String)


class ContactTask(Base):
    __tablename__ = "contact_tasks"

    __table_args__ = (UniqueConstraint('assessment_id', 'customer_id', name='uq_contact_assessment_customer'),)
    tenant_id: Mapped[str] = mapped_column(String, default='default', server_default='default', index=True)
    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("risk_assessments.assessment_id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    pb_id: Mapped[str] = mapped_column(ForeignKey("pbs.pb_id"), index=True)
    draft_message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="승인대기")
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    tenant_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
