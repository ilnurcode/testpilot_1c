"""Журнал аудитов вставленного кода."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'code_audit',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('source_name', sa.String(255), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('total_files', sa.Integer(), nullable=False),
        sa.Column('sources_checked', sa.Integer(), nullable=True),
        sa.Column('total_issues', sa.Integer(), nullable=False),
        sa.Column('issues_by_severity', sa.JSON(), nullable=False),
        sa.Column('issues_by_category', sa.JSON(), nullable=False),
        sa.Column('issues', sa.JSON(), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('message', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=False),
        sa.Column('created_at', sa.String(50), nullable=False),
    )
    op.create_index('ix_code_audit_sha256', 'code_audit', ['sha256'])
    # Листинг журнала сортирует created_at DESC, id DESC.
    op.create_index('ix_code_audit_created_at', 'code_audit', ['created_at'])


def downgrade():
    op.drop_index('ix_code_audit_created_at', table_name='code_audit')
    op.drop_index('ix_code_audit_sha256', table_name='code_audit')
    op.drop_table('code_audit')
