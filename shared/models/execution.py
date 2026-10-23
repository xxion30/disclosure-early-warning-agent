"""R4 durable execution and immutable public results; independent of Graph internals."""
from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from shared.models.base import Base


class AnalysisJob(Base):
    __tablename__ = 'analysis_jobs'
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_key: Mapped[str] = mapped_column(String, unique=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey('companies.corp_code'), index=True)
    rcept_no: Mapped[str] = mapped_column(ForeignKey('disclosures.rcept_no'))
    as_of: Mapped[str] = mapped_column(String)
    input_manifest: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String)
    rules_digest: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default='PENDING', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    next_attempt_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)


class PublicAssessment(Base):
    __tablename__ = 'public_assessments'
    assessment_id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('analysis_jobs.job_id'), unique=True)
    corp_code: Mapped[str] = mapped_column(ForeignKey('companies.corp_code'), index=True)
    rcept_no: Mapped[str] = mapped_column(String)
    as_of: Mapped[str] = mapped_column(String)
    input_hash: Mapped[str] = mapped_column(String)
    rules_digest: Mapped[str] = mapped_column(String)
    legacy_assessment_id: Mapped[int] = mapped_column(ForeignKey('risk_assessments.assessment_id'))
    result: Mapped[dict] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(String)
    state_version: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String)


class OrganizationReview(Base):
    __tablename__ = 'organization_reviews'
    __table_args__ = (UniqueConstraint('tenant_id', 'assessment_id', name='uq_org_public_review'),)
    review_id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey('tenants.tenant_id'), index=True)
    assessment_id: Mapped[str] = mapped_column(ForeignKey('public_assessments.assessment_id'))
    thread_id: Mapped[str] = mapped_column(String, unique=True)
    status: Mapped[str] = mapped_column(String, default='PENDING')
    version: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    drafts: Mapped[dict] = mapped_column(JSON, default=dict)
    rewrite_customers: Mapped[list] = mapped_column(JSON, default=list)
    decisions: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
