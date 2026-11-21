"""A4: хранение секрета подключения (зашифрованный envelope вместо открытого текста).

Столбцы nullable: существующие строки не меняются, legacy auth_password остаётся до
явной проверенной миграции (scripts/secrets_key.py --migrate-legacy --confirmed).
"""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('impact_projects', sa.Column('auth_secret', sa.Text(), nullable=True))
    op.add_column('impact_projects', sa.Column('auth_secret_scheme', sa.String(30), nullable=True))
    op.add_column('impact_projects', sa.Column('auth_secret_updated_at', sa.String(50), nullable=True))


def downgrade():
    op.drop_column('impact_projects', 'auth_secret_updated_at')
    op.drop_column('impact_projects', 'auth_secret_scheme')
    op.drop_column('impact_projects', 'auth_secret')
