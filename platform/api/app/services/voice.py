"""Voice enrollment: turning a few spoken samples into a voice print that can be matched later.

Only the voice print (a list of numbers) is stored. The recording of the sample is deleted as soon as
the print has been taken, so there is no raw voice audio of enrolled people sitting in storage, and
the print itself is never returned by the API or written to a log.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.registry import capabilities
from app.ai.types import Embedding
from app.config import Settings
from app.errors import ApiError
from app.models import Person, VoiceProfile
from app.services.matching import decode_vector, encode_vector, merge_sample, normalize


def expected_model(settings: Settings) -> str | None:
    """The voice model this server would use to enroll or match, known without loading it.
    None when there is no speaker model, in which case no profile can be called out of date."""
    if capabilities(settings).speaker_matching:
        return settings.ai_speaker_model[:80]
    return None


def is_ready(profile: VoiceProfile, settings: Settings) -> bool:
    """Enough samples, and made by the model currently in use."""
    current = expected_model(settings)
    return profile.sample_count >= settings.voice_enroll_min_samples and (
        current is None or profile.model_name == current
    )


def is_outdated(profile: VoiceProfile, settings: Settings) -> bool:
    """The speaker model was changed since this person enrolled, so their print can no longer be
    compared. They need to enroll again."""
    current = expected_model(settings)
    return current is not None and profile.model_name != current


def add_sample(
    db: Session, settings: Settings, person: Person, embedding: Embedding
) -> tuple[VoiceProfile, bool]:
    """Fold one more sample into the person's print. Returns the profile and whether an older print
    from a different model had to be discarded to make room for it."""
    profile = db.scalar(select(VoiceProfile).where(VoiceProfile.person_id == person.id))
    vector = normalize(embedding.vector)

    if profile is not None and (
        profile.model_name != embedding.model or profile.embedding_dim != len(vector)
    ):
        db.delete(profile)  # prints from different models cannot be combined: start again
        db.flush()
        profile, replaced = None, True
    else:
        replaced = False

    if profile is None:
        profile = VoiceProfile(
            person_id=person.id,
            company_id=person.company_id,
            embedding=encode_vector(vector),
            embedding_dim=len(vector),
            model_name=embedding.model,
            model_version=embedding.model_version,
            sample_count=1,
        )
        db.add(profile)
        return profile, replaced

    if profile.sample_count >= settings.voice_enroll_max_samples:
        raise ApiError(
            409,
            "enough_samples",
            "This person already has plenty of voice samples. Delete the voice profile to start over.",
        )
    merged = merge_sample(
        decode_vector(profile.embedding, profile.embedding_dim), profile.sample_count, vector
    )
    profile.embedding = encode_vector(merged)
    profile.sample_count += 1
    profile.model_version = embedding.model_version
    return profile, replaced
