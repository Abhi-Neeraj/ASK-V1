"""voice signatures and recording analyses

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-12 22:54:14.211748

Two new tables and nothing else: `person_signatures` (a person's voice signature: pitch plus voice colour) and
`recording_analyses` (what a transcript was worked out to say). No existing table is altered, so no existing row
can be lost; both new tables point at older ones (people, transcripts) with ON DELETE CASCADE, so deleting a
person or a transcript takes its signature or analysis with it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('person_signatures',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('person_id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('timbre', sa.LargeBinary(), nullable=False),
    sa.Column('version', sa.String(length=24), nullable=False),
    sa.Column('pitch_base_hz', sa.Float(), nullable=False),
    sa.Column('pitch_low_hz', sa.Float(), nullable=False),
    sa.Column('pitch_high_hz', sa.Float(), nullable=False),
    sa.Column('voiced_seconds', sa.Float(), nullable=False),
    sa.Column('sample_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('sample_count >= 1', name=op.f('ck_person_signatures_samples_positive')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_person_signatures_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['person_id'], ['people.id'], name=op.f('fk_person_signatures_person_id_people'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_person_signatures')),
    sa.UniqueConstraint('person_id', name=op.f('uq_person_signatures_person_id'))
    )
    op.create_index('ix_person_signatures_company', 'person_signatures', ['company_id'], unique=False)

    op.create_table('recording_analyses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('transcript_id', sa.Uuid(), nullable=False),
    sa.Column('company_id', sa.Uuid(), nullable=False),
    sa.Column('engine', sa.String(length=16), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('content', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("engine IN ('semantic','lexical')", name=op.f('ck_recording_analyses_engine_valid')),
    sa.ForeignKeyConstraint(['company_id'], ['companies.id'], name=op.f('fk_recording_analyses_company_id_companies'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transcript_id'], ['transcripts.id'], name=op.f('fk_recording_analyses_transcript_id_transcripts'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recording_analyses')),
    sa.UniqueConstraint('transcript_id', name=op.f('uq_recording_analyses_transcript_id'))
    )
    op.create_index('ix_recording_analyses_company', 'recording_analyses', ['company_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_recording_analyses_company', table_name='recording_analyses')
    op.drop_table('recording_analyses')
    op.drop_index('ix_person_signatures_company', table_name='person_signatures')
    op.drop_table('person_signatures')
