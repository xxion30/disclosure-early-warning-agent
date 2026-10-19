"""CLAUDE.md 4-1장 테이블 정의. 날짜는 ISO 문자열로 저장한다."""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ASSESSMENT_STATUSES = ("RUNNING", "WAITING_APPROVAL", "DONE", "FAILED", "MANUAL_CHECK")
TASK_STATUSES = ("승인대기", "승인", "반려", "연락완료", "보류")
LEVELS = ("Low", "Medium", "High", "Critical")
PRIORITIES = ("P1", "P2", "P3")


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"

    corp_code: Mapped[str] = mapped_column(String, primary_key=True)
    stock_code: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    market: Mapped[str | None] = mapped_column(String, nullable=True)
    base_score: Mapped[int] = mapped_column(Integer, default=0)
    base_reasons: Mapped[list] = mapped_column(JSON, default=list)


class Disclosure(Base):
    __tablename__ = "disclosures"

    rcept_no: Mapped[str] = mapped_column(String, primary_key=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey("companies.corp_code"), index=True)
    title: Mapped[str] = mapped_column(String)
    disclosure_type: Mapped[str | None] = mapped_column(String, nullable=True)
    filed_at: Mapped[str] = mapped_column(String, index=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)


class Signal(Base):
    __tablename__ = "signals"

    signal_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey("companies.corp_code"), index=True)
    rcept_no: Mapped[str] = mapped_column(String)
    signal_type: Mapped[str] = mapped_column(String)
    points: Mapped[float] = mapped_column(Float)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    filed_at: Mapped[str] = mapped_column(String)


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


class PB(Base):
    __tablename__ = "pbs"

    pb_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    email: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(String)


class Customer(Base):
    __tablename__ = "customers"

    customer_id: Mapped[str] = mapped_column(String, primary_key=True)
    pb_id: Mapped[str] = mapped_column(ForeignKey("pbs.pb_id"), index=True)
    name: Mapped[str] = mapped_column(String)
    risk_profile: Mapped[str] = mapped_column(String)
    total_assets: Mapped[int] = mapped_column(Integer)


class Holding(Base):
    __tablename__ = "holdings"

    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), primary_key=True)
    stock_code: Mapped[str] = mapped_column(String, primary_key=True)
    eval_amount: Mapped[int] = mapped_column(Integer)
    on_credit: Mapped[bool] = mapped_column(Boolean, default=False)


class ContactTask(Base):
    __tablename__ = "contact_tasks"

    task_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assessment_id: Mapped[int] = mapped_column(ForeignKey("risk_assessments.assessment_id"), index=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.customer_id"), index=True)
    pb_id: Mapped[str] = mapped_column(ForeignKey("pbs.pb_id"), index=True)
    priority: Mapped[str] = mapped_column(String)
    impact_score: Mapped[int] = mapped_column(Integer)
    draft_message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, default="승인대기")
    reject_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
