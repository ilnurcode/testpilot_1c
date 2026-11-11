"""Добавление реквизитов подключения к 1С в impact_projects."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('impact_projects', sa.Column('odata_url', sa.String(500), nullable=True))
    op.add_column('impact_projects', sa.Column('web_url', sa.String(500), nullable=True))
    op.add_column('impact_projects', sa.Column('auth_username', sa.String(120), nullable=True))
    op.add_column('impact_projects', sa.Column('auth_password', sa.String(120), nullable=True))

def downgrade():
    op.drop_column('impact_projects', 'auth_password')
    op.drop_column('impact_projects', 'auth_username')
    op.drop_column('impact_projects', 'web_url')
    op.drop_column('impact_projects', 'odata_url')
