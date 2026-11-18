"""A compressed copy of each recording's audio, kept in the database

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-18 20:00:00.000000

Add-only: one new table, `recording_audio`, one row per recording that was archived (see
app/services/audio_archive.py). It points at `recordings` with ON DELETE CASCADE, so purging a recording takes its
copy with it. No existing table is touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('recording_audio',
    sa.Column('recording_id', sa.Uuid(), nullable=False),
    sa.Column('sample_rate', sa.Integer(), nullable=False),
    sa.Column('pcm_bytes', sa.Integer(), nullable=False),
    sa.Column('stored_bytes', sa.Integer(), nullable=False),
    sa.Column('data', sa.LargeBinary(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], name=op.f('fk_recording_audio_recording_id_recordings'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('recording_id', name=op.f('pk_recording_audio'))
    )


def downgrade() -> None:
    op.drop_table('recording_audio')
