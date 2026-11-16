"""Stored voice signatures: folding samples into a person's signature, and choosing who a voice may be matched
against.

A signature is sensitive in the same way as a voice print: it is only ever made for a person who has given
consent, it is deleted when consent is withdrawn, and the timbre is never returned by the API or written to a
log. Only the pitch summary (base, low and high) may be shown, and only to managers.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.db import rows_affected
from app.errors import ApiError
from app.models import Person, PersonSignature
from app.voiceid.matching import SignatureCandidate
from app.voiceid.pitch import PitchProfile
from app.voiceid.signature import (
    SIGNATURE_VERSION,
    VoiceSignature,
    decode_timbre,
    encode_timbre,
    merge_signatures,
)


def is_current(row: PersonSignature) -> bool:
    """Made by the version of the measurement this server runs, so it can be compared."""
    return row.version == SIGNATURE_VERSION


def is_ready(row: PersonSignature, settings: Settings) -> bool:
    return row.sample_count >= settings.voice_enroll_min_samples and is_current(row)


def pitch_of(row: PersonSignature) -> PitchProfile:
    return PitchProfile(
        base_hz=row.pitch_base_hz,
        low_hz=min(row.pitch_low_hz, row.pitch_base_hz),
        high_hz=max(row.pitch_high_hz, row.pitch_base_hz),
        voiced_seconds=row.voiced_seconds,
        voiced_ratio=1.0,
    )


def to_signature(row: PersonSignature) -> VoiceSignature:
    return VoiceSignature(
        pitch=pitch_of(row),
        timbre=decode_timbre(row.timbre),
        frames=0,
        version=row.version,
    )


def get(db: Session, person_id: uuid.UUID) -> PersonSignature | None:
    return db.scalar(select(PersonSignature).where(PersonSignature.person_id == person_id))


def add_sample(
    db: Session, settings: Settings, person: Person, sample: VoiceSignature
) -> tuple[PersonSignature, bool]:
    """Fold one more sample into the person's signature. Returns the row and whether an older signature (made
    by a different version of the measurement) had to be discarded to make room for it."""
    row = get(db, person.id)
    replaced = False
    if row is not None and not is_current(row):
        db.delete(row)  # signatures from different versions cannot be combined: start again
        db.flush()
        row, replaced = None, True

    now = timeutil.utcnow()
    if row is None:
        row = PersonSignature(
            person_id=person.id,
            company_id=person.company_id,
            timbre=encode_timbre(sample.timbre),
            version=sample.version,
            pitch_base_hz=sample.pitch.base_hz,
            pitch_low_hz=sample.pitch.low_hz,
            pitch_high_hz=sample.pitch.high_hz,
            voiced_seconds=sample.pitch.voiced_seconds,
            sample_count=1,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        return row, replaced

    if row.sample_count >= settings.voice_enroll_max_samples:
        raise ApiError(
            409,
            "enough_samples",
            "This person already has plenty of voice signature samples. Delete the voice data to start over.",
        )
    merged = merge_signatures(to_signature(row), row.sample_count, sample)
    row.timbre = encode_timbre(merged.timbre)
    row.pitch_base_hz = merged.pitch.base_hz
    row.pitch_low_hz = merged.pitch.low_hz
    row.pitch_high_hz = merged.pitch.high_hz
    row.voiced_seconds = merged.pitch.voiced_seconds
    row.sample_count += 1
    row.updated_at = now
    return row, replaced


def candidates(db: Session, company_id: uuid.UUID, settings: Settings) -> list[SignatureCandidate]:
    """Whose signature a voice may be compared with: people of this company with consent on record, enough
    samples, and a signature made by the current version of the measurement."""
    rows = db.scalars(
        select(PersonSignature)
        .join(Person, Person.id == PersonSignature.person_id)
        .where(
            Person.company_id == company_id,
            PersonSignature.company_id == company_id,
            Person.consent_status == "granted",
            PersonSignature.sample_count >= settings.voice_enroll_min_samples,
            PersonSignature.version == SIGNATURE_VERSION,
        )
    ).all()
    return [SignatureCandidate(person_id=row.person_id, signature=to_signature(row)) for row in rows]


def forget(db: Session, person_id: uuid.UUID) -> bool:
    """Delete a person's signature. True if there was one."""
    return bool(
        rows_affected(db.execute(delete(PersonSignature).where(PersonSignature.person_id == person_id)))
    )
