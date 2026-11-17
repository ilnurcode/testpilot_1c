"""Ограничение детализации замечаний аудита кода."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('code_audit', sa.Column('issues_kept', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('code_audit', sa.Column('issues_truncated', sa.Boolean(), nullable=False, server_default=sa.false()))
    # Прежние записи хранили все замечания: срез в них не срабатывал.
    op.execute('UPDATE code_audit SET issues_kept = total_issues, issues_truncated = false')
    op.alter_column('code_audit', 'issues_kept', server_default=None)
    op.alter_column('code_audit', 'issues_truncated', server_default=None)


def downgrade():
    op.drop_column('code_audit', 'issues_truncated')
    op.drop_column('code_audit', 'issues_kept')
