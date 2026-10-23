"""Preserve the exact source used for a confirmed correction relationship."""
from alembic import op
import sqlalchemy as sa

revision = '20261024_07'
down_revision = '20261023_06'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('disclosures', sa.Column('event_link_evidence', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('disclosures', 'event_link_evidence')
