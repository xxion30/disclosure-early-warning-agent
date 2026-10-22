"""R4 persistence contract; R3's collect.py remains owned by R3."""
from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from shared.models.base import Base


class Tenant(Base):
    __tablename__ = 'tenants'
    tenant_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)


class RiskState(Base):
    __tablename__ = 'risk_states'
    corp_code: Mapped[str] = mapped_column(ForeignKey('companies.corp_code'), primary_key=True)
    stock_code: Mapped[str] = mapped_column(String, index=True)
    stage: Mapped[str] = mapped_column(String, default='NONE')
    orange_score: Mapped[float] = mapped_column(Float, default=0)
    yellow_score: Mapped[float] = mapped_column(Float, default=0)
    composite_ratio: Mapped[float] = mapped_column(Float, default=0)
    sort_value: Mapped[float] = mapped_column(Float, default=0)
    active_stages: Mapped[list] = mapped_column(JSON, default=list)
    items: Mapped[list] = mapped_column(JSON, default=list)
    manual_check: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    resolution_evidence: Mapped[list] = mapped_column(JSON, default=list)
    alert_version: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[str] = mapped_column(String)
    latest_filed_at: Mapped[str] = mapped_column(String)
    as_of: Mapped[str | None] = mapped_column(String, nullable=True)
    rules_digest: Mapped[str | None] = mapped_column(String, nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=0, server_default='0')


class StateRead(Base):
    __tablename__ = 'state_reads'
    pb_id: Mapped[str] = mapped_column(ForeignKey('pbs.pb_id'), primary_key=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey('companies.corp_code'), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    read_version: Mapped[int] = mapped_column(Integer, default=0)


class FinancialSnapshot(Base):
    __tablename__ = 'financial_snapshots'
    __table_args__ = (UniqueConstraint('corp_code', 'rcept_no', 'period', 'basis', 'revision'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey('companies.corp_code'), index=True)
    rcept_no: Mapped[str] = mapped_column(String)
    period: Mapped[str] = mapped_column(String)
    period_kind: Mapped[str] = mapped_column(String)
    basis: Mapped[str] = mapped_column(String)
    indicators: Mapped[dict] = mapped_column(JSON, default=dict)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String)


class CollectRun(Base):
    __tablename__ = 'collect_runs'
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default='PENDING')
    stock_codes: Mapped[list] = mapped_column(JSON, default=list)
    started_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)


class CollectCursor(Base):
    __tablename__ = 'collect_cursors'
    stock_code: Mapped[str] = mapped_column(String, primary_key=True)
    last_success_at: Mapped[str | None] = mapped_column(String, nullable=True)
    active_run_id: Mapped[str | None] = mapped_column(String, nullable=True)


class TenantRun(Base):
    __tablename__ = 'tenant_runs'
    __table_args__ = (UniqueConstraint('tenant_id', 'alert_key'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String, index=True)
    alert_key: Mapped[str] = mapped_column(String)
    corp_code: Mapped[str] = mapped_column(String)
    assessment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    thread_id: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, default='PENDING')
