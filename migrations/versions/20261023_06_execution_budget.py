"""Keep model/tool/time usage across analysis and review claims."""
from alembic import op
import sqlalchemy as sa

revision = '20261023_06'
down_revision = '20261023_05'
branch_labels = None
depends_on = None


def upgrade():
    for table in ('analysis_jobs', 'organization_reviews'):
        op.add_column(table, sa.Column('execution_budget', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    for table in ('organization_reviews', 'analysis_jobs'):
        op.drop_column(table, 'execution_budget')
