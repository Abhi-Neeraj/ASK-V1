"""Bluetooth lockets: people per locket, emailed pairing codes, per-arrival access, summaries, firmware

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-14 20:00:00.000000

Add-only. Two existing tables gain columns (`devices.max_users`, `devices.transport`, `transcripts.summary_text/summary_model`),
both with plain ADD COLUMN, never a batch rebuild (on SQLite a rebuild would delete every row that points at
them). New tables: `device_members`, `recording_access`, `locket_pairing_requests`, `firmware_releases`.

Carried over exactly, so nobody gains or loses access:
- each locket's current holder (`devices.assigned_user_id`) becomes its one member;
- each recording's wearer (`recordings.assigned_user_id`) becomes the one person with access to it.

A trigger makes `devices.public_id` (the locket's product ID) permanent: any update that changes it is refused.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMANENT = 'product ID is permanent'


def upgrade() -> None:
    """Upgrade schema."""
    sqlite = op.get_context().dialect.name == 'sqlite'

    if sqlite:
        op.execute(
            sa.text(
                'ALTER TABLE devices ADD COLUMN max_users INTEGER NOT NULL DEFAULT 1 '
                'CONSTRAINT ck_devices_max_users_range CHECK (max_users >= 1 AND max_users <= 50)'
            )
        )
    else:
        op.add_column('devices', sa.Column('max_users', sa.Integer(), server_default='1', nullable=False))
        op.create_check_constraint(
            op.f('ck_devices_max_users_range'), 'devices', 'max_users >= 1 AND max_users <= 50'
        )
    if sqlite:
        op.execute(
            sa.text(
                "ALTER TABLE devices ADD COLUMN transport VARCHAR(12) NOT NULL DEFAULT 'wifi' "
                "CONSTRAINT ck_devices_transport_valid CHECK (transport IN ('wifi','bluetooth'))"
            )
        )
    else:
        op.add_column('devices', sa.Column('transport', sa.String(length=12), server_default='wifi', nullable=False))
        op.create_check_constraint(
            op.f('ck_devices_transport_valid'), 'devices', "transport IN ('wifi','bluetooth')"
        )
    op.add_column('transcripts', sa.Column('summary_text', sa.Text(), nullable=True))
    op.add_column('transcripts', sa.Column('summary_model', sa.String(length=80), nullable=True))

    op.create_table('device_members',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('added_by', sa.Uuid(), nullable=True),
    sa.Column('added_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['added_by'], ['users.id'], name=op.f('fk_device_members_added_by_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_device_members_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_device_members_device_id_devices'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_device_members_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_device_members')),
    sa.UniqueConstraint('device_id', 'user_id', name='uq_device_members_device_user')
    )
    op.create_index('ix_device_members_user', 'device_members', ['user_id'], unique=False)

    op.create_table('recording_access',
    sa.Column('recording_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.ForeignKeyConstraint(['recording_id'], ['recordings.id'], name=op.f('fk_recording_access_recording_id_recordings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_recording_access_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('recording_id', 'user_id', name=op.f('pk_recording_access'))
    )
    op.create_index('ix_recording_access_user', 'recording_access', ['user_id'], unique=False)

    op.create_table('locket_pairing_requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('device_id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('nonce_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_locket_pairing_requests_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], name=op.f('fk_locket_pairing_requests_device_id_devices'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_locket_pairing_requests_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_locket_pairing_requests'))
    )
    op.create_index('ix_locket_pairing_requests_device_user', 'locket_pairing_requests', ['device_id', 'user_id'], unique=False)

    op.create_table('firmware_releases',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('storage_key', sa.String(length=300), nullable=False),
    sa.Column('uploaded_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_firmware_releases_uploaded_by_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_firmware_releases')),
    sa.UniqueConstraint('version', name=op.f('uq_firmware_releases_version'))
    )

    # Carry over who holds which locket, and who sees which recording. New ids are made in SQL so the
    # timestamps are copied as stored, untouched by Python.
    new_id = 'lower(hex(randomblob(16)))' if sqlite else 'gen_random_uuid()'
    op.execute(
        sa.text(
            f'INSERT INTO device_members (id, company_id, device_id, user_id, added_at) '
            f'SELECT {new_id}, company_id, id, assigned_user_id, COALESCE(paired_at, created_at) '
            'FROM devices WHERE assigned_user_id IS NOT NULL'
        )
    )
    op.execute(
        sa.text(
            'INSERT INTO recording_access (recording_id, user_id) '
            'SELECT id, assigned_user_id FROM recordings WHERE assigned_user_id IS NOT NULL'
        )
    )

    if sqlite:
        op.execute(
            sa.text(
                'CREATE TRIGGER devices_public_id_permanent BEFORE UPDATE OF public_id ON devices '
                'WHEN NEW.public_id IS NOT OLD.public_id '
                f"BEGIN SELECT RAISE(ABORT, '{PERMANENT}'); END"
            )
        )
    else:
        op.execute(
            sa.text(
                'CREATE FUNCTION devices_public_id_permanent() RETURNS trigger AS $$ '
                'BEGIN IF NEW.public_id IS DISTINCT FROM OLD.public_id THEN '
                f"RAISE EXCEPTION '{PERMANENT}'; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"
            )
        )
        op.execute(
            sa.text(
                'CREATE TRIGGER devices_public_id_permanent BEFORE UPDATE OF public_id ON devices '
                'FOR EACH ROW EXECUTE FUNCTION devices_public_id_permanent()'
            )
        )


def downgrade() -> None:
    """Downgrade schema."""
    sqlite = op.get_context().dialect.name == 'sqlite'
    op.execute(sa.text('DROP TRIGGER IF EXISTS devices_public_id_permanent' + ('' if sqlite else ' ON devices')))
    if not sqlite:
        op.execute(sa.text('DROP FUNCTION IF EXISTS devices_public_id_permanent()'))
    op.drop_table('firmware_releases')
    op.drop_index('ix_locket_pairing_requests_device_user', table_name='locket_pairing_requests')
    op.drop_table('locket_pairing_requests')
    op.drop_index('ix_recording_access_user', table_name='recording_access')
    op.drop_table('recording_access')
    op.drop_index('ix_device_members_user', table_name='device_members')
    op.drop_table('device_members')
    # Plain DROP COLUMN (SQLite 3.35+ and PostgreSQL): never a batch rebuild of a referenced table.
    op.execute(sa.text('ALTER TABLE transcripts DROP COLUMN summary_model'))
    op.execute(sa.text('ALTER TABLE transcripts DROP COLUMN summary_text'))
    if not sqlite:
        op.drop_constraint(op.f('ck_devices_transport_valid'), 'devices', type_='check')
    op.execute(sa.text('ALTER TABLE devices DROP COLUMN transport'))
    if not sqlite:
        op.drop_constraint(op.f('ck_devices_max_users_range'), 'devices', type_='check')
    op.execute(sa.text('ALTER TABLE devices DROP COLUMN max_users'))
