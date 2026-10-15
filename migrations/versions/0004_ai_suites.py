"""Предложения нейросети, согласование и фактические результаты учебного стенда."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('ti_ai_suites',sa.Column('id',sa.String(36),primary_key=True),sa.Column('package_id',sa.String(120),sa.ForeignKey('ti_packages.id'),nullable=False),sa.Column('project_id',sa.String(36),nullable=False),sa.Column('status',sa.String(30),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('created_at',sa.String(50),nullable=False))
    op.create_index('ix_ti_ai_suites_package_id','ti_ai_suites',['package_id'])
    op.create_index('ix_ti_ai_suites_project_id','ti_ai_suites',['project_id'])
    op.create_table('ti_ai_evidence',sa.Column('id',sa.String(36),primary_key=True),sa.Column('suite_id',sa.String(36),sa.ForeignKey('ti_ai_suites.id'),nullable=False),sa.Column('content',sa.LargeBinary(),nullable=False))
    op.create_index('ix_ti_ai_evidence_suite_id','ti_ai_evidence',['suite_id'])
    op.create_table('demo_orders',sa.Column('id',sa.String(36),primary_key=True),sa.Column('payload',sa.JSON(),nullable=False))

def downgrade():
    op.drop_table('demo_orders');op.drop_table('ti_ai_evidence');op.drop_table('ti_ai_suites')
