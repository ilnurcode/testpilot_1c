"""Сохранённый результат аудита загрузки."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('impact_uploads', sa.Column('audit_result', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('impact_uploads', 'audit_result')
