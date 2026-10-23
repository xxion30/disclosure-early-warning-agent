"""Version public risk states and add collection dispatch leases."""
from alembic import op
from sqlalchemy import Column, Integer, String, inspect

revision = '20261022_02'
down_revision = '20261022_01'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite init_db can already have added these fields in local development.
    additions = {
        'risk_states': [Column('as_of', String(), nullable=True), Column('rules_digest', String(), nullable=True),
                        Column('state_version', Integer(), nullable=False, server_default='0')],
        'risk_assessments': [Column('risk_state_version', Integer(), nullable=True), Column('score_fingerprint', String(), nullable=True)],
        'collect_runs': [Column('lease_expires_at', String(), nullable=True)],
    }
    for table, columns in additions.items():
        have = {c['name'] for c in inspect(op.get_bind()).get_columns(table)}
        for column in columns:
            if column.name not in have:
                op.add_column(table, column)


def downgrade():
    raise RuntimeError('상태 버전과 실행 이력 보존을 위해 자동 downgrade를 지원하지 않습니다.')
