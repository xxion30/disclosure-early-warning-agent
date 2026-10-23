"""Frozen R4 execution schema; additive and data preserving."""
from alembic import op
from sqlalchemy import Column, ForeignKey, Integer, JSON, MetaData, String, Table, UniqueConstraint, inspect
revision = '20261023_04'
down_revision = '20261023_03'
branch_labels = None
depends_on = None


def upgrade():
    bind=op.get_bind()
    metadata=MetaData()
    for name in ('companies','disclosures','tenants','risk_assessments'):
        Table(name, metadata, autoload_with=bind)
    Table('analysis_jobs',metadata,
        Column('job_id',String(),primary_key=True,nullable=False),
        Column('job_key',String(),nullable=False),
        Column('corp_code',String(),ForeignKey('companies.corp_code'),nullable=False),
        Column('rcept_no',String(),ForeignKey('disclosures.rcept_no'),nullable=False),
        Column('as_of',String(),nullable=False),
        Column('input_manifest',JSON(),nullable=False),
        Column('input_hash',String(),nullable=False),
        Column('rules_digest',String(),nullable=False),
        Column('status',String(),nullable=False),
        Column('attempts',Integer(),nullable=False),
        Column('lease_token',String()),
        Column('lease_expires_at',String()),
        Column('next_attempt_at',String()),
        Column('created_at',String(),nullable=False),
        Column('finished_at',String()),
        Column('error_code',String()),
        UniqueConstraint('job_key'),
    ).create(bind,checkfirst=True)
    Table('public_assessments',metadata,
        Column('assessment_id',String(),primary_key=True,nullable=False),
        Column('job_id',String(),ForeignKey('analysis_jobs.job_id'),nullable=False),
        Column('corp_code',String(),ForeignKey('companies.corp_code'),nullable=False),
        Column('rcept_no',String(),nullable=False),
        Column('as_of',String(),nullable=False),
        Column('input_hash',String(),nullable=False),
        Column('rules_digest',String(),nullable=False),
        Column('legacy_assessment_id',Integer(),ForeignKey('risk_assessments.assessment_id'),nullable=False),
        Column('result',JSON(),nullable=False),
        Column('explanation',String(),nullable=False),
        Column('state_version',Integer(),nullable=False),
        Column('created_at',String(),nullable=False),
        UniqueConstraint('job_id'),
    ).create(bind,checkfirst=True)
    Table('organization_reviews',metadata,
        Column('review_id',String(),primary_key=True,nullable=False),
        Column('tenant_id',String(),ForeignKey('tenants.tenant_id'),nullable=False),
        Column('assessment_id',String(),ForeignKey('public_assessments.assessment_id'),nullable=False),
        Column('thread_id',String(),nullable=False),
        Column('status',String(),nullable=False),
        Column('version',Integer(),nullable=False),
        Column('lease_token',String()),
        Column('lease_expires_at',String()),
        Column('drafts',JSON(),nullable=False),
        Column('rewrite_customers',JSON(),nullable=False),
        Column('decisions',JSON(),nullable=False),
        Column('created_at',String(),nullable=False),
        Column('finished_at',String()),
        UniqueConstraint('tenant_id','assessment_id',name='uq_org_public_review'),
        UniqueConstraint('thread_id'),
    ).create(bind,checkfirst=True)
    for table,name in (('risk_states','public_assessment_id'),('contact_tasks','review_id')):
        if name not in {c['name'] for c in inspect(bind).get_columns(table)}:
            op.add_column(table,Column(name,String(),nullable=True))


def downgrade():
    raise RuntimeError('판정/승인 이력 보존을 위해 자동 downgrade를 지원하지 않습니다.')
