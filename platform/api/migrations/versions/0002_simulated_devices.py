"""mark simulated lockets

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-25 21:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Existing lockets were paired before simulation was tracked; none of them are simulated.
    # A plain ADD COLUMN, not batch mode: on SQLite batch mode rebuilds the whole table, and the rows
    # in every table that references it would go with it unless foreign keys were switched off.
    op.add_column('devices', sa.Column('is_simulated', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('devices', schema=None) as batch_op:
        batch_op.drop_column('is_simulated')
