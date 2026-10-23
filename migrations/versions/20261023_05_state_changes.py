"""Record score-free changes for the stock detail summary."""
from alembic import op
from sqlalchemy import Column, ForeignKey, Integer, JSON, MetaData, String, Table, UniqueConstraint

revision='20261023_05'
down_revision='20261023_04'
branch_labels=None
depends_on=None


def upgrade():
    metadata=MetaData()
    Table('companies',metadata,autoload_with=op.get_bind())
    Table('risk_state_changes',metadata,
        Column('change_id',Integer(),primary_key=True,autoincrement=True),
        Column('corp_code',String(),ForeignKey('companies.corp_code'),nullable=False),
        Column('state_version',Integer(),nullable=False),
        Column('from_stage',String(),nullable=True),Column('to_stage',String(),nullable=False),
        Column('changes',JSON(),nullable=False),Column('created_at',String(),nullable=False),
        UniqueConstraint('corp_code','state_version',name='uq_state_change_version')
    ).create(op.get_bind(),checkfirst=True)


def downgrade():
    raise RuntimeError('변경 이력 보존을 위해 자동 downgrade를 지원하지 않습니다.')
