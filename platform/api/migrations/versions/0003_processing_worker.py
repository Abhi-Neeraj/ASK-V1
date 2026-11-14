"""processing worker

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-25 23:16:31.620829

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('workers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('worker_id', sa.String(length=80), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('placeholder', sa.Boolean(), nullable=False),
    sa.Column('capabilities', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_workers')),
    sa.UniqueConstraint('worker_id', name=op.f('uq_workers_worker_id'))
    )
    op.create_index('ix_workers_last_seen', 'workers', ['last_seen_at'], unique=False)

    # Plain ADD COLUMN, never batch mode: on SQLite batch mode rebuilds the table, and every transcript
    # segment (which references it) would be deleted along with the old copy.
    op.add_column('transcripts', sa.Column('is_placeholder', sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column('transcripts', sa.Column('language', sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('transcripts', schema=None) as batch_op:
        batch_op.drop_column('language')
        batch_op.drop_column('is_placeholder')

    op.drop_index('ix_workers_last_seen', table_name='workers')
    op.drop_table('workers')
