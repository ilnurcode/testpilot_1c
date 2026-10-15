"""Постоянная библиотека файлов проектов и первичный анализ без базовой версии."""
from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    op.alter_column('impact_analyses','base_id',nullable=True)
    op.create_table('impact_projects',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('name',sa.String(120),nullable=False),
        sa.Column('description',sa.Text(),nullable=False),sa.Column('configuration_id',sa.String(120),nullable=True,unique=True),
        sa.Column('created_by',sa.String(100),nullable=False),sa.Column('created_at',sa.String(50),nullable=False),
        sa.Column('next_number',sa.Integer(),nullable=False))
    op.create_table('impact_uploads',
        sa.Column('id',sa.String(36),primary_key=True),sa.Column('project_id',sa.String(36),sa.ForeignKey('impact_projects.id'),nullable=False),
        sa.Column('number',sa.Integer(),nullable=False),sa.Column('filename',sa.String(240),nullable=False),
        sa.Column('kind',sa.String(30),nullable=False),sa.Column('component',sa.String(120),nullable=False),
        sa.Column('label',sa.String(120),nullable=False),sa.Column('size_bytes',sa.Integer(),nullable=False),
        sa.Column('checksum',sa.String(64),nullable=False),sa.Column('content',sa.LargeBinary(),nullable=False),
        sa.Column('status',sa.String(30),nullable=False),sa.Column('message',sa.Text(),nullable=False),
        sa.Column('version_id',sa.String(36),sa.ForeignKey('impact_versions.id'),nullable=True),
        sa.Column('baseline_id',sa.String(36),sa.ForeignKey('impact_uploads.id'),nullable=True),
        sa.Column('created_by',sa.String(100),nullable=False),sa.Column('created_at',sa.String(50),nullable=False),
        sa.Column('summary',sa.JSON(),nullable=False),
        sa.UniqueConstraint('project_id','number'),sa.UniqueConstraint('project_id','kind','component','checksum'),
        sa.UniqueConstraint('project_id','kind','component','label'),
        sa.CheckConstraint('size_bytes > 0 AND size_bytes <= 67108864',name='upload_size_limit'))
    op.create_index('ix_impact_uploads_project_id','impact_uploads',['project_id'])
    op.create_index('ix_impact_uploads_version_id','impact_uploads',['version_id'])
    op.create_table('impact_upload_analyses',
        sa.Column('analysis_id',sa.String(36),sa.ForeignKey('impact_analyses.id'),primary_key=True),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('impact_projects.id'),nullable=False),
        sa.Column('upload_id',sa.String(36),sa.ForeignKey('impact_uploads.id'),nullable=False),
        sa.Column('baseline_id',sa.String(36),sa.ForeignKey('impact_uploads.id'),nullable=True),
        sa.Column('mode',sa.String(20),nullable=False),sa.Column('created_at',sa.String(50),nullable=False))
    op.create_index('ix_impact_upload_analyses_project_id','impact_upload_analyses',['project_id'])
    op.create_index('ix_impact_upload_analyses_upload_id','impact_upload_analyses',['upload_id'])

def downgrade():
    # Полные анализы нельзя безопасно преобразовать в сравнения: не удаляем пользовательские отчёты.
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT count(*) FROM impact_analyses WHERE base_id IS NULL')).scalar():
        raise RuntimeError('Нельзя откатить миграцию при наличии первичных анализов без базовой версии')
    op.drop_table('impact_upload_analyses')
    op.drop_table('impact_uploads')
    op.drop_table('impact_projects')
    op.alter_column('impact_analyses','base_id',nullable=False)
