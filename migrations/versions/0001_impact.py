"""Анализ влияния и общий аудит. Зафиксированный снимок схемы."""
from alembic import op
import sqlalchemy as sa
revision='0001'
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('audit',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('service', sa.String(length=30), nullable=False),
    sa.Column('actor', sa.String(length=100), nullable=False),
    sa.Column('action', sa.String(length=100), nullable=False),
    sa.Column('entity_id', sa.String(length=120), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(length=50), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_service'), 'audit', ['service'], unique=False)
    op.create_table('impact_configurations',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('synthetic', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('impact_versions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('configuration_id', sa.String(length=120), nullable=False),
    sa.Column('version', sa.String(length=300), nullable=False),
    sa.Column('source', sa.String(length=300), nullable=False),
    sa.Column('imported_at', sa.String(length=50), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=False),
    sa.Column('raw_checksum', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['configuration_id'], ['impact_configurations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('configuration_id', 'version')
    )
    op.create_index(op.f('ix_impact_versions_configuration_id'), 'impact_versions', ['configuration_id'], unique=False)
    op.create_table('impact_analyses',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('base_id', sa.String(length=36), nullable=False),
    sa.Column('target_id', sa.String(length=36), nullable=False),
    sa.Column('parameters', sa.JSON(), nullable=False),
    sa.Column('comparison', sa.JSON(), nullable=False),
    sa.Column('package', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(length=50), nullable=False),
    sa.ForeignKeyConstraint(['base_id'], ['impact_versions.id'], ),
    sa.ForeignKeyConstraint(['target_id'], ['impact_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('impact_objects',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version_id', sa.String(length=36), nullable=False),
    sa.Column('object_id', sa.String(length=120), nullable=False),
    sa.Column('type', sa.String(length=40), nullable=False),
    sa.Column('full_name', sa.String(length=300), nullable=False),
    sa.Column('parent_id', sa.String(length=120), nullable=True),
    sa.Column('attributes', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['version_id'], ['impact_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'object_id')
    )
    op.create_index(op.f('ix_impact_objects_object_id'), 'impact_objects', ['object_id'], unique=False)
    op.create_index(op.f('ix_impact_objects_version_id'), 'impact_objects', ['version_id'], unique=False)
    op.create_table('impact_affected',
    sa.Column('analysis_id', sa.String(length=36), nullable=False),
    sa.Column('object_id', sa.String(length=120), nullable=False),
    sa.Column('paths', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['analysis_id'], ['impact_analyses.id'], ),
    sa.PrimaryKeyConstraint('analysis_id', 'object_id')
    )
    op.create_table('impact_edges',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('version_id', sa.String(length=36), nullable=False),
    sa.Column('source', sa.String(length=120), nullable=False),
    sa.Column('target', sa.String(length=120), nullable=False),
    sa.Column('type', sa.String(length=40), nullable=False),
    sa.Column('origin', sa.String(length=300), nullable=False),
    sa.Column('confidence', sa.Float(), nullable=False),
    sa.ForeignKeyConstraint(['version_id', 'source'], ['impact_objects.version_id', 'impact_objects.object_id'], ),
    sa.ForeignKeyConstraint(['version_id', 'target'], ['impact_objects.version_id', 'impact_objects.object_id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['impact_versions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('version_id', 'source', 'target', 'type')
    )
    op.create_index(op.f('ix_impact_edges_source'), 'impact_edges', ['source'], unique=False)
    op.create_index(op.f('ix_impact_edges_target'), 'impact_edges', ['target'], unique=False)
    op.create_index(op.f('ix_impact_edges_version_id'), 'impact_edges', ['version_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_impact_edges_version_id'), table_name='impact_edges')
    op.drop_index(op.f('ix_impact_edges_target'), table_name='impact_edges')
    op.drop_index(op.f('ix_impact_edges_source'), table_name='impact_edges')
    op.drop_table('impact_edges')
    op.drop_table('impact_affected')
    op.drop_index(op.f('ix_impact_objects_version_id'), table_name='impact_objects')
    op.drop_index(op.f('ix_impact_objects_object_id'), table_name='impact_objects')
    op.drop_table('impact_objects')
    op.drop_table('impact_analyses')
    op.drop_index(op.f('ix_impact_versions_configuration_id'), table_name='impact_versions')
    op.drop_table('impact_versions')
    op.drop_table('impact_configurations')
    op.drop_index(op.f('ix_audit_service'), table_name='audit')
    op.drop_table('audit')
