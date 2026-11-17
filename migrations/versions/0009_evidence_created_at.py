"""created_at у evidence: время создания снимка для аудита и ретенции."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    # Без бэкфилла: колонка добавляется пустой, старые строки остаются NULL («время неизвестно»).
    # DEFAULT ставим отдельно, чтобы он применялся только к новым вставкам.
    op.add_column('ti_ai_evidence', sa.Column('created_at', sa.DateTime(timezone=True), nullable=True))
    op.alter_column('ti_ai_evidence', 'created_at', server_default=sa.func.now())


def downgrade():
    op.drop_column('ti_ai_evidence', 'created_at')
