"""Каталог, история, оценки, планы и контракт исполнителя. Зафиксированный снимок схемы."""
from alembic import op
import sqlalchemy as sa
revision='0002'
down_revision='0001'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('ti_ml_models',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('configuration_id', sa.String(length=120), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(length=50), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_ml_models_configuration_id'), 'ti_ml_models', ['configuration_id'], unique=False)
    op.create_table('ti_packages',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('configuration_id', sa.String(length=120), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_packages_configuration_id'), 'ti_packages', ['configuration_id'], unique=False)
    op.create_table('ti_runs',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('configuration_id', sa.String(length=120), nullable=False),
    sa.Column('version', sa.String(length=300), nullable=False),
    sa.Column('synthetic', sa.Boolean(), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.String(length=50), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_runs_configuration_id'), 'ti_runs', ['configuration_id'], unique=False)
    op.create_table('ti_settings',
    sa.Column('id', sa.String(length=30), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('ti_tests',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('configuration_id', sa.String(length=120), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('mandatory', sa.Boolean(), nullable=False),
    sa.Column('criticality', sa.String(length=20), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_tests_configuration_id'), 'ti_tests', ['configuration_id'], unique=False)
    op.create_table('ti_coverage',
    sa.Column('test_id', sa.String(length=120), nullable=False),
    sa.Column('object_id', sa.String(length=120), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('source', sa.String(length=300), nullable=False),
    sa.Column('actor', sa.String(length=100), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('updated_at', sa.String(length=50), nullable=False),
    sa.ForeignKeyConstraint(['test_id'], ['ti_tests.id'], ),
    sa.PrimaryKeyConstraint('test_id', 'object_id')
    )
    op.create_table('ti_defects',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('run_id', sa.String(length=120), nullable=False),
    sa.Column('test_id', sa.String(length=120), nullable=False),
    sa.Column('severity', sa.String(length=20), nullable=False),
    sa.Column('state', sa.String(length=20), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['ti_runs.id'], ),
    sa.ForeignKeyConstraint(['test_id'], ['ti_tests.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_defects_run_id'), 'ti_defects', ['run_id'], unique=False)
    op.create_index(op.f('ix_ti_defects_test_id'), 'ti_defects', ['test_id'], unique=False)
    op.create_table('ti_plans',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('package_id', sa.String(length=120), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('approved', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.String(length=50), nullable=False),
    sa.ForeignKeyConstraint(['package_id'], ['ti_packages.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_plans_package_id'), 'ti_plans', ['package_id'], unique=False)
    op.create_table('ti_results',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('run_id', sa.String(length=120), nullable=False),
    sa.Column('test_id', sa.String(length=120), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=False),
    sa.Column('started_at', sa.String(length=50), nullable=False),
    sa.Column('finished_at', sa.String(length=50), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['ti_runs.id'], ),
    sa.ForeignKeyConstraint(['test_id'], ['ti_tests.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'test_id')
    )
    op.create_index(op.f('ix_ti_results_run_id'), 'ti_results', ['run_id'], unique=False)
    op.create_index(op.f('ix_ti_results_started_at'), 'ti_results', ['started_at'], unique=False)
    op.create_index(op.f('ix_ti_results_test_id'), 'ti_results', ['test_id'], unique=False)
    op.create_table('ti_execution_jobs',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('plan_id', sa.String(length=36), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('report_checksum', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['plan_id'], ['ti_plans.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ti_execution_jobs_plan_id'), 'ti_execution_jobs', ['plan_id'], unique=False)
    op.create_table('ti_priorities',
    sa.Column('plan_id', sa.String(length=36), nullable=False),
    sa.Column('test_id', sa.String(length=120), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('factors', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['ti_plans.id'], ),
    sa.ForeignKeyConstraint(['test_id'], ['ti_tests.id'], ),
    sa.PrimaryKeyConstraint('plan_id', 'test_id')
    )


def downgrade():
    op.drop_table('ti_priorities')
    op.drop_index(op.f('ix_ti_execution_jobs_plan_id'), table_name='ti_execution_jobs')
    op.drop_table('ti_execution_jobs')
    op.drop_index(op.f('ix_ti_results_test_id'), table_name='ti_results')
    op.drop_index(op.f('ix_ti_results_started_at'), table_name='ti_results')
    op.drop_index(op.f('ix_ti_results_run_id'), table_name='ti_results')
    op.drop_table('ti_results')
    op.drop_index(op.f('ix_ti_plans_package_id'), table_name='ti_plans')
    op.drop_table('ti_plans')
    op.drop_index(op.f('ix_ti_defects_test_id'), table_name='ti_defects')
    op.drop_index(op.f('ix_ti_defects_run_id'), table_name='ti_defects')
    op.drop_table('ti_defects')
    op.drop_table('ti_coverage')
    op.drop_index(op.f('ix_ti_tests_configuration_id'), table_name='ti_tests')
    op.drop_table('ti_tests')
    op.drop_table('ti_settings')
    op.drop_index(op.f('ix_ti_runs_configuration_id'), table_name='ti_runs')
    op.drop_table('ti_runs')
    op.drop_index(op.f('ix_ti_packages_configuration_id'), table_name='ti_packages')
    op.drop_table('ti_packages')
    op.drop_index(op.f('ix_ti_ml_models_configuration_id'), table_name='ti_ml_models')
    op.drop_table('ti_ml_models')
