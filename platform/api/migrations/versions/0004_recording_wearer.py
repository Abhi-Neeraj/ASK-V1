"""record who wore the locket on each recording

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-26 17:00:00.000000

Until now an employee saw a recording because its locket was assigned to them *now*, so reassigning a locket
handed its whole history to the next wearer. Each recording now carries who the locket was assigned to when it
arrived, and that is what an employee sees by.

Recordings that already exist are given their locket's current assignee. That is exactly what decided who saw
them before this migration, so nobody gains or loses access to anything they could already see; only
recordings that arrive from now on are attributed as they come in.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Plain ADD COLUMN, never batch mode: on SQLite batch mode rebuilds the table, and every transcript, job and
    # segment that references a recording would be deleted along with the old copy.
    if op.get_context().dialect.name == 'sqlite':
        # Alembic cannot add a constraint to an existing SQLite table, but SQLite itself accepts a new column that
        # carries its REFERENCES clause as long as it defaults to NULL, which this does.
        op.execute(
            sa.text(
                'ALTER TABLE recordings ADD COLUMN assigned_user_id CHAR(32) '
                'REFERENCES users (id) ON DELETE SET NULL'
            )
        )
    else:
        op.add_column('recordings', sa.Column('assigned_user_id', sa.Uuid(), nullable=True))
        op.create_foreign_key(
            op.f('fk_recordings_assigned_user_id_users'),
            'recordings',
            'users',
            ['assigned_user_id'],
            ['id'],
            ondelete='SET NULL',
        )
    op.create_index('ix_recordings_assigned_user', 'recordings', ['assigned_user_id'], unique=False)

    # The backfill described above, in one statement so it is the same on SQLite and PostgreSQL.
    op.execute(
        sa.text(
            "UPDATE recordings SET assigned_user_id = "
            "(SELECT devices.assigned_user_id FROM devices WHERE devices.id = recordings.device_id)"
        )
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_recordings_assigned_user', table_name='recordings')
    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.drop_column('assigned_user_id')
