"""Initial additive DARTCH schema; historical columns and rows are retained."""
from alembic import op
from sqlalchemy import inspect, text
from migrations.schema_20261022 import metadata

revision = '20261022_01'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    existing = set(inspect(conn).get_table_names())
    metadata.create_all(conn)
    for table in metadata.sorted_tables:
        if table.name not in existing:
            continue
        have = {c['name'] for c in inspect(conn).get_columns(table.name)}
        for column in table.columns:
            if column.name not in have:
                op.add_column(table.name, column._copy())
        if table.name == 'contact_tasks':
            constraints = inspect(conn).get_unique_constraints(table.name)
            if not any(set(c['column_names']) == {'assessment_id', 'customer_id'} for c in constraints):
                # Do not delete old duplicates to make a migration pass.
                duplicate = conn.execute(text('SELECT 1 FROM contact_tasks GROUP BY assessment_id, customer_id HAVING COUNT(*) > 1 LIMIT 1')).first()
                if duplicate:
                    raise RuntimeError('연락 업무 중복 데이터 정리가 먼저 필요합니다. 기존 행은 보존했습니다.')
                op.create_index('uq_contact_assessment_customer', 'contact_tasks', ['assessment_id', 'customer_id'], unique=True)
    if conn.dialect.name == 'sqlite':
        conn.execute(text("INSERT OR IGNORE INTO tenants (tenant_id,name) VALUES ('default','Default')"))
    else:
        conn.execute(text("INSERT INTO tenants (tenant_id,name) VALUES ('default','Default') ON CONFLICT DO NOTHING"))


def downgrade():
    raise RuntimeError('데이터 보존을 위해 자동 downgrade를 지원하지 않습니다. 백업에서 복구하세요.')
