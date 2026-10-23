"""Add structured facts and an explicit collection-to-analysis handoff."""
from alembic import op
from sqlalchemy import Column, JSON, String, inspect

revision = '20261023_03'
down_revision = '20261022_02'
branch_labels = None
depends_on = None


def upgrade():
    have = {c['name'] for c in inspect(op.get_bind()).get_columns('disclosures')}
    for column in (Column('structured_facts', JSON(), nullable=True),
                   Column('analysis_ready_at', String(), nullable=True), Column('input_version', String(), nullable=True)):
        if column.name not in have:
            op.add_column('disclosures', column)


def downgrade():
    raise RuntimeError('분석 입력과 이력 보존을 위해 자동 downgrade를 지원하지 않습니다.')
