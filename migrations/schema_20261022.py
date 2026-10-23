"""Frozen schema for revision 20261022_01; do not import live application models."""
from sqlalchemy import MetaData, Table, Column, String, Text, Integer, Float, Boolean, JSON, ForeignKey, UniqueConstraint

metadata = MetaData()

Table('audit_logs', metadata,
    Column('tenant_id', String(), primary_key=False, nullable=True, index=True),
    Column('log_id', Integer(), primary_key=True, nullable=False),
    Column('actor', String(), primary_key=False, nullable=False),
    Column('action', String(), primary_key=False, nullable=False),
    Column('target', String(), primary_key=False, nullable=True),
    Column('payload', JSON(), primary_key=False, nullable=False),
    Column('trace_id', String(), primary_key=False, nullable=True),
    Column('created_at', String(), primary_key=False, nullable=False),
)

Table('collect_cursors', metadata,
    Column('stock_code', String(), primary_key=True, nullable=False),
    Column('last_success_at', String(), primary_key=False, nullable=True),
    Column('active_run_id', String(), primary_key=False, nullable=True),
)

Table('collect_runs', metadata,
    Column('run_id', String(), primary_key=True, nullable=False),
    Column('tenant_id', String(), primary_key=False, nullable=True, index=True),
    Column('requested_by', String(), primary_key=False, nullable=True),
    Column('status', String(), primary_key=False, nullable=False),
    Column('stock_codes', JSON(), primary_key=False, nullable=False),
    Column('started_at', String(), primary_key=False, nullable=False),
    Column('finished_at', String(), primary_key=False, nullable=True),
    Column('fetched_count', Integer(), primary_key=False, nullable=False),
    Column('failed_count', Integer(), primary_key=False, nullable=False),
    Column('error', String(), primary_key=False, nullable=True),
)

Table('companies', metadata,
    Column('corp_code', String(), primary_key=True, nullable=False),
    Column('stock_code', String(), primary_key=False, nullable=False, index=True),
    Column('name', String(), primary_key=False, nullable=False),
    Column('market', String(), primary_key=False, nullable=True),
    Column('base_score', Integer(), primary_key=False, nullable=False),
    Column('base_reasons', JSON(), primary_key=False, nullable=False),
)

Table('pbs', metadata,
    Column('tenant_id', String(), primary_key=False, nullable=False, index=True, server_default='default'),
    Column('pb_id', String(), primary_key=True, nullable=False),
    Column('name', String(), primary_key=False, nullable=False),
    Column('email', String(), primary_key=False, nullable=False, unique=True),
    Column('role', String(), primary_key=False, nullable=False, server_default='PB'),
    Column('password_hash', String(), primary_key=False, nullable=False),
)

Table('tenant_runs', metadata,
    Column('id', Integer(), primary_key=True, nullable=False),
    Column('tenant_id', String(), primary_key=False, nullable=False, index=True),
    Column('alert_key', String(), primary_key=False, nullable=False),
    Column('corp_code', String(), primary_key=False, nullable=False),
    Column('assessment_id', Integer(), primary_key=False, nullable=True),
    Column('thread_id', String(), primary_key=False, nullable=False, unique=True),
    Column('status', String(), primary_key=False, nullable=False),
    UniqueConstraint('tenant_id', 'alert_key'),
)

Table('tenants', metadata,
    Column('tenant_id', String(), primary_key=True, nullable=False),
    Column('name', String(), primary_key=False, nullable=False),
)

Table('customers', metadata,
    Column('tenant_id', String(), primary_key=False, nullable=False, index=True, server_default='default'),
    Column('customer_id', String(), primary_key=True, nullable=False),
    Column('pb_id', String(), ForeignKey('pbs.pb_id'), primary_key=False, nullable=False, index=True),
    Column('name', String(), primary_key=False, nullable=False),
    Column('risk_profile', String(), primary_key=False, nullable=False),
    Column('total_assets', Integer(), primary_key=False, nullable=False),
)

Table('disclosures', metadata,
    Column('rcept_no', String(), primary_key=True, nullable=False),
    Column('corp_code', String(), ForeignKey('companies.corp_code'), primary_key=False, nullable=False, index=True),
    Column('title', String(), primary_key=False, nullable=False),
    Column('disclosure_type', String(), primary_key=False, nullable=True),
    Column('filed_at', String(), primary_key=False, nullable=False, index=True),
    Column('url', String(), primary_key=False, nullable=True),
    Column('body_text', Text(), primary_key=False, nullable=True),
    Column('event_id', String(), primary_key=False, nullable=True, index=True),
    Column('event_started_at', String(), primary_key=False, nullable=True),
    Column('original_rcept_no', String(), primary_key=False, nullable=True),
    Column('processed', Boolean(), primary_key=False, nullable=False),
    Column('content_hash', String(), primary_key=False, nullable=True),
    Column('processed_at', String(), primary_key=False, nullable=True),
    Column('purged_at', String(), primary_key=False, nullable=True),
)

Table('financial_snapshots', metadata,
    Column('id', Integer(), primary_key=True, nullable=False),
    Column('corp_code', String(), ForeignKey('companies.corp_code'), primary_key=False, nullable=False, index=True),
    Column('rcept_no', String(), primary_key=False, nullable=False),
    Column('period', String(), primary_key=False, nullable=False),
    Column('period_kind', String(), primary_key=False, nullable=False),
    Column('basis', String(), primary_key=False, nullable=False),
    Column('indicators', JSON(), primary_key=False, nullable=False),
    Column('sources', JSON(), primary_key=False, nullable=False),
    Column('revision', Integer(), primary_key=False, nullable=False),
    Column('created_at', String(), primary_key=False, nullable=False),
    UniqueConstraint('corp_code', 'rcept_no', 'period', 'basis', 'revision'),
)

Table('risk_assessments', metadata,
    Column('assessment_id', Integer(), primary_key=True, nullable=False),
    Column('corp_code', String(), ForeignKey('companies.corp_code'), primary_key=False, nullable=False, index=True),
    Column('rcept_no', String(), primary_key=False, nullable=False, index=True),
    Column('stage', String(), primary_key=False, nullable=False, server_default='NONE'),
    Column('total_score', Float(), primary_key=False, nullable=False),
    Column('level', String(), primary_key=False, nullable=False),
    Column('combo_upgraded', Boolean(), primary_key=False, nullable=False),
    Column('reasons', JSON(), primary_key=False, nullable=False),
    Column('ai_explanation', Text(), primary_key=False, nullable=True),
    Column('status', String(), primary_key=False, nullable=False),
    Column('created_at', String(), primary_key=False, nullable=False),
)

Table('risk_states', metadata,
    Column('corp_code', String(), ForeignKey('companies.corp_code'), primary_key=True, nullable=False),
    Column('stock_code', String(), primary_key=False, nullable=False, index=True),
    Column('stage', String(), primary_key=False, nullable=False),
    Column('orange_score', Float(), primary_key=False, nullable=False),
    Column('yellow_score', Float(), primary_key=False, nullable=False),
    Column('composite_ratio', Float(), primary_key=False, nullable=False),
    Column('sort_value', Float(), primary_key=False, nullable=False),
    Column('active_stages', JSON(), primary_key=False, nullable=False),
    Column('items', JSON(), primary_key=False, nullable=False),
    Column('manual_check', Boolean(), primary_key=False, nullable=False),
    Column('resolved', Boolean(), primary_key=False, nullable=False),
    Column('resolution_evidence', JSON(), primary_key=False, nullable=False),
    Column('alert_version', Integer(), primary_key=False, nullable=False),
    Column('updated_at', String(), primary_key=False, nullable=False),
    Column('latest_filed_at', String(), primary_key=False, nullable=False),
)

Table('signals', metadata,
    Column('signal_id', Integer(), primary_key=True, nullable=False),
    Column('corp_code', String(), ForeignKey('companies.corp_code'), primary_key=False, nullable=False, index=True),
    Column('rcept_no', String(), primary_key=False, nullable=False),
    Column('event_id', String(), primary_key=False, nullable=True, index=True),
    Column('event_started_at', String(), primary_key=False, nullable=True),
    Column('signal_type', String(), primary_key=False, nullable=False),
    Column('points', Float(), primary_key=False, nullable=False),
    Column('detail', JSON(), primary_key=False, nullable=False),
    Column('filed_at', String(), primary_key=False, nullable=False),
)

Table('state_reads', metadata,
    Column('pb_id', String(), ForeignKey('pbs.pb_id'), primary_key=True, nullable=False),
    Column('corp_code', String(), ForeignKey('companies.corp_code'), primary_key=True, nullable=False),
    Column('tenant_id', String(), primary_key=False, nullable=False, index=True),
    Column('read_version', Integer(), primary_key=False, nullable=False),
)

Table('contact_tasks', metadata,
    Column('tenant_id', String(), primary_key=False, nullable=False, index=True, server_default='default'),
    Column('task_id', Integer(), primary_key=True, nullable=False),
    Column('assessment_id', Integer(), ForeignKey('risk_assessments.assessment_id'), primary_key=False, nullable=False, index=True),
    Column('customer_id', String(), ForeignKey('customers.customer_id'), primary_key=False, nullable=False, index=True),
    Column('pb_id', String(), ForeignKey('pbs.pb_id'), primary_key=False, nullable=False, index=True),
    Column('draft_message', Text(), primary_key=False, nullable=False),
    Column('status', String(), primary_key=False, nullable=False),
    Column('reject_reason', Text(), primary_key=False, nullable=True),
    Column('result_note', Text(), primary_key=False, nullable=True),
    Column('created_at', String(), primary_key=False, nullable=False),
    Column('updated_at', String(), primary_key=False, nullable=False),
    UniqueConstraint('assessment_id', 'customer_id', name='uq_contact_assessment_customer'),
)

Table('holdings', metadata,
    Column('tenant_id', String(), primary_key=False, nullable=False, index=True, server_default='default'),
    Column('customer_id', String(), ForeignKey('customers.customer_id'), primary_key=True, nullable=False),
    Column('stock_code', String(), primary_key=True, nullable=False),
    Column('eval_amount', Integer(), primary_key=False, nullable=False),
    Column('on_credit', Boolean(), primary_key=False, nullable=False),
)
