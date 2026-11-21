"""Состояние асинхронного прогона AI-плана: claim/lease/cancel/idempotency."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('ti_ai_suites', sa.Column('attempt_id', sa.String(36), nullable=True))
    op.add_column('ti_ai_suites', sa.Column('attempt_version', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('ti_ai_suites', sa.Column('idempotency_key', sa.String(64), nullable=True))
    op.add_column('ti_ai_suites', sa.Column('lease_expires_at', sa.String(50), nullable=True))
    op.add_column('ti_ai_suites', sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('ti_ai_suites', sa.Column('plan_revision', sa.String(64), nullable=True))
    op.add_column('ti_ai_suites', sa.Column('started_at', sa.String(50), nullable=True))
    op.add_column('ti_ai_suites', sa.Column('finished_at', sa.String(50), nullable=True))
    op.add_column('ti_ai_suites', sa.Column('last_heartbeat_at', sa.String(50), nullable=True))
    op.create_index('ix_ti_ai_suites_idempotency_key', 'ti_ai_suites', ['idempotency_key'], unique=True)
    op.create_index('ix_ti_ai_suites_status', 'ti_ai_suites', ['status'])


def downgrade():
    op.drop_index('ix_ti_ai_suites_status', table_name='ti_ai_suites')
    op.drop_index('ix_ti_ai_suites_idempotency_key', table_name='ti_ai_suites')
    for column in ('last_heartbeat_at', 'finished_at', 'started_at', 'plan_revision',
                   'cancel_requested', 'lease_expires_at', 'idempotency_key', 'attempt_version', 'attempt_id'):
        op.drop_column('ti_ai_suites', column)
