from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import timeutil
from app.ai.registry import ProviderHolder, capabilities
from app.ai.types import Embedding, PermanentError, SpeakerEmbedder
from app.config import Settings
from app.db import rows_affected
from app.errors import ApiError, not_found
from app.models import ConsentRecord, Person, TranscriptSegment, VoiceProfile
from app.schemas.people import (
    DEFAULT_CONSENT_STATEMENT,
    ConsentIn,
    PersonCreateIn,
    PersonOut,
    PitchOut,
    SignatureCandidateOut,
    SignatureMatchOut,
    SignatureOut,
)
from app.security.deps import Authed, Ctx, DbDep, LimiterDep, Manager, SettingsDep
from app.security.ratelimit import enforce
from app.services import signatures, voice
from app.services.audio import WavInfo, inspect_wav
from app.services.audit import record_audit
from app.services.uploads import Spooled, spool_to_disk
from app.voiceid.audio_io import load_for_analysis
from app.voiceid.matching import Scored, best_signature_match, score_candidates
from app.voiceid.pitch import PitchProfile
from app.voiceid.signature import VoiceSignature, compute_signature

logger = logging.getLogger("ask.people")

router = APIRouter(prefix="/api/people", tags=["people"])

WAV_TYPES = {"audio/wav", "audio/x-wav", "audio/wave", "application/octet-stream"}


def _get_person(db: Session, ctx: Ctx, person_id: uuid.UUID) -> Person:
    person = db.scalar(select(Person).where(Person.id == person_id, Person.company_id == ctx.company_id))
    if person is None:
        raise not_found("person")
    return person


def _pitch_out(pitch: PitchProfile) -> PitchOut:
    return PitchOut(
        base_hz=round(pitch.base_hz, 1), low_hz=round(pitch.low_hz, 1), high_hz=round(pitch.high_hz, 1)
    )


def _signature_out(db: Session, settings: Settings, person: Person, *, show_pitch: bool) -> SignatureOut:
    row = signatures.get(db, person.id)
    return SignatureOut(
        ready=row is not None and signatures.is_ready(row, settings),
        sample_count=row.sample_count if row else 0,
        samples_required=settings.voice_enroll_min_samples,
        outdated=row is not None and not signatures.is_current(row),
        updated_at=row.updated_at if row else None,
        # Only a summary of the pitch ever leaves the database, and only to managers.
        pitch=_pitch_out(signatures.pitch_of(row)) if row and show_pitch else None,
    )


def _out(db: Session, settings: Settings, person: Person, *, show_pitch: bool) -> PersonOut:
    profile = db.scalar(select(VoiceProfile).where(VoiceProfile.person_id == person.id))
    return PersonOut(
        id=person.id,
        display_name=person.display_name,
        consent_status=person.consent_status,
        consent_recorded_at=person.consent_recorded_at,
        enrolled=profile is not None and voice.is_ready(profile, settings),
        enrolled_at=profile.enrolled_at if profile else None,
        # Only a count is exposed. The voice print itself never leaves the database.
        sample_count=profile.sample_count if profile else 0,
        samples_required=settings.voice_enroll_min_samples,
        voice_outdated=profile is not None and voice.is_outdated(profile, settings),
        signature=_signature_out(db, settings, person, show_pitch=show_pitch),
    )


def _forget_voice(db: Session, person: Person) -> bool:
    """Remove the voice profile and voice signature and stop attributing past transcript lines to this
    person."""
    removed = rows_affected(db.execute(delete(VoiceProfile).where(VoiceProfile.person_id == person.id)))
    removed = int(signatures.forget(db, person.id)) + removed
    db.execute(
        update(TranscriptSegment)
        .where(TranscriptSegment.person_id == person.id)
        .values(person_id=None, confidence=None)
    )
    return bool(removed)


@router.get("", response_model=list[PersonOut])
def list_people(ctx: Authed, db: DbDep, settings: SettingsDep) -> list[PersonOut]:
    people = db.scalars(
        select(Person).where(Person.company_id == ctx.company_id).order_by(Person.display_name)
    )
    return [_out(db, settings, person, show_pitch=ctx.can_manage) for person in people]


@router.post("", response_model=PersonOut, status_code=201)
def create_person(body: PersonCreateIn, ctx: Manager, db: DbDep, settings: SettingsDep) -> PersonOut:
    person = Person(company_id=ctx.company_id, display_name=body.display_name.strip(), created_by=ctx.user.id)
    db.add(person)
    db.flush()
    record_audit(
        db,
        action="person.created",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="person",
        object_id=person.id,
        ip=ctx.ip,
    )
    db.commit()
    return _out(db, settings, person, show_pitch=True)


@router.post("/{person_id}/consent", response_model=PersonOut)
def record_consent(
    person_id: uuid.UUID, body: ConsentIn, ctx: Manager, db: DbDep, settings: SettingsDep
) -> PersonOut:
    """Consent is an append-only record. Withdrawing it also deletes the person's voice profile."""
    person = _get_person(db, ctx, person_id)
    now = timeutil.utcnow()
    db.add(
        ConsentRecord(
            company_id=ctx.company_id,
            person_id=person.id,
            status="granted" if body.granted else "revoked",
            statement=(body.statement or DEFAULT_CONSENT_STATEMENT).strip(),
            recorded_by=ctx.user.id,
            recorded_at=now,
        )
    )
    person.consent_status = "granted" if body.granted else "revoked"
    person.consent_recorded_at = now
    removed_voice = False if body.granted else _forget_voice(db, person)
    record_audit(
        db,
        action="person.consent_granted" if body.granted else "person.consent_revoked",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="person",
        object_id=person.id,
        detail={"voice_profile_removed": removed_voice},
        ip=ctx.ip,
    )
    db.commit()
    return _out(db, settings, person, show_pitch=True)


def _why_not_available(request: Request, settings: Settings) -> str:
    """Say what is missing, in terms of what an administrator can do about it."""
    if settings.ai_provider == "none":
        return (
            "Voice enrollment needs a speaker model, and no processing provider is configured "
            "(ASK_AI_PROVIDER=none)."
        )
    if settings.ai_provider == "stub":
        return (
            "Voice enrollment is unavailable in placeholder mode: the development stub has no speaker model."
        )
    holder: ProviderHolder = request.app.state.ai
    reasons = [
        n for n in capabilities(settings).notes if "matching" in n.lower() or "speaker model" in n.lower()
    ]
    return holder.failure or (reasons[0] if reasons else "The speaker model is not available on this server.")


def _embed(holder: ProviderHolder, embedder: SpeakerEmbedder, path) -> Embedding:  # type: ignore[no-untyped-def]
    """Run the speaker model on one sample. Models are not thread-safe, so one at a time."""
    try:
        with holder.lock:
            return embedder.embed(path)
    except PermanentError as error:
        raise ApiError(422, "unusable_sample", str(error)) from error
    except Exception:
        logger.exception("the speaker model failed on an enrollment sample")
        raise ApiError(
            503, "speaker_model_error", "The speaker model could not process this sample. Please try again."
        ) from None


async def _receive_sample(request: Request, settings: Settings, db: Session) -> tuple[Spooled, WavInfo]:
    """Take a voice sample sent as the request body: a WAV of a usable length. The sample is on disk when this
    returns, and the caller must delete it (a sample is never kept). If it is unusable it is deleted here and
    the reason is raised."""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type not in WAV_TYPES:
        raise ApiError(415, "unsupported_media_type", "Send the sample as audio/wav.")

    # Room for the longest allowed sample at the highest allowed rate, in stereo, plus headers.
    biggest_rate = max(settings.allowed_sample_rates)
    max_bytes = int(settings.voice_sample_max_seconds * biggest_rate * 4) + 64 * 1024
    # Looking up the person opened a database transaction. Give the connection back before waiting for the
    # sample to arrive, or a slow sender holds it for the whole upload (see the device upload for why it
    # matters).
    db.commit()
    spooled = await spool_to_disk(
        request.stream(),
        request.app.state.storage,
        max_bytes=max_bytes,
        read_timeout=settings.upload_read_timeout_seconds,
        total_timeout=min(settings.upload_total_seconds, 60.0),
    )
    try:
        info = inspect_wav(spooled.path, settings)
        seconds = info.duration_ms / 1000
        if seconds < settings.voice_sample_min_seconds:
            raise ApiError(
                422,
                "sample_too_short",
                f"Record at least {settings.voice_sample_min_seconds:g} seconds of the person speaking.",
            )
        if seconds > settings.voice_sample_max_seconds:
            raise ApiError(
                422,
                "sample_too_long",
                f"Keep each sample under {settings.voice_sample_max_seconds:g} seconds.",
            )
    except BaseException:
        spooled.path.unlink(missing_ok=True)
        raise
    return spooled, info


def _measure(path: Path) -> VoiceSignature:
    """The signature of one sample. Pure arithmetic on the samples: no model, no network."""
    try:
        audio = load_for_analysis(path)
    except (ValueError, EOFError) as error:
        raise ApiError(422, "invalid_audio", "The file could not be read as a WAV recording.") from error
    made = compute_signature([audio.samples], audio.rate)
    if made is None:
        raise ApiError(
            422,
            "not_enough_speech",
            "There is not enough clear speech in this sample to measure a voice. Record a few seconds of "
            "someone speaking normally, close to the microphone.",
        )
    return made


@router.post("/{person_id}/enroll", response_model=PersonOut)
async def enroll_voice(
    person_id: uuid.UUID,
    request: Request,
    ctx: Manager,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> PersonOut:
    """Add one voice sample (the body is a WAV recording of the person speaking).

    Send three or more over a minute or two, in the person's normal voice. The audio is used to take
    a voice print and then deleted. Needs the person's recorded consent.
    """
    enforce(limiter, f"enroll:{ctx.user.id}", 30, 60)
    person = _get_person(db, ctx, person_id)
    if person.consent_status != "granted":
        raise ApiError(409, "consent_required", "Record this person's consent before enrolling a voice.")

    holder: ProviderHolder = request.app.state.ai
    provider = holder.get()
    embedder = provider.embedder if provider else None
    if provider is None or embedder is None:
        raise ApiError(501, "not_available", _why_not_available(request, settings))

    spooled, _ = await _receive_sample(request, settings, db)
    try:
        embedding = await run_in_threadpool(_embed, holder, embedder, spooled.path)
        # Consent was checked before all of that. Check it again now, and hold the person's row (a shared lock
        # on
        # databases that have them) until this commits, so withdrawing consent cannot slip in between and
        # leave
        # a voice print for someone who has just said no.
        current = db.scalar(
            select(Person)
            .where(Person.id == person.id, Person.company_id == ctx.company_id)
            .with_for_update(read=True)
            # Sessions here keep objects after a commit, so without this the person already in memory would be
            # handed back as it was when consent was first checked, and the re-check would see nothing new.
            .execution_options(populate_existing=True)
        )
        if current is None or current.consent_status != "granted":
            raise ApiError(
                409, "consent_required", "This person's consent was withdrawn, so no voice was saved."
            )
        profile, replaced = voice.add_sample(db, settings, current, embedding)
        record_audit(
            db,
            action="person.voice_sample_added",
            company_id=ctx.company_id,
            actor_user_id=ctx.user.id,
            object_type="person",
            object_id=person.id,
            detail={"sample_count": profile.sample_count, "replaced_older_model": replaced},
            ip=ctx.ip,
        )
        db.commit()
    finally:
        spooled.path.unlink(missing_ok=True)  # the recording is never kept
    return _out(db, settings, person, show_pitch=True)


@router.post("/{person_id}/signature", response_model=PersonOut)
async def add_signature_sample(
    person_id: uuid.UUID,
    request: Request,
    ctx: Manager,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> PersonOut:
    """Add one voice sample (the body is a WAV file of the person speaking) to their voice signature: their
    pitch (base, low and high) and the colour of their voice. Send three or more in their normal voice. The
    audio is measured and then deleted. Needs the person's recorded consent.

    Works without any model: it is arithmetic on the audio, so it is available whatever the processing
    provider is.
    """
    enforce(limiter, f"signature:{ctx.user.id}", 30, 60)
    person = _get_person(db, ctx, person_id)
    if person.consent_status != "granted":
        raise ApiError(
            409, "consent_required", "Record this person's consent before adding a voice signature."
        )

    spooled, _ = await _receive_sample(request, settings, db)
    try:
        sample = await run_in_threadpool(_measure, spooled.path)
        # Same care as enrollment: consent was checked before all of that, so check it again now and hold the
        # person's row until this commits, so withdrawing consent cannot slip in between.
        current = db.scalar(
            select(Person)
            .where(Person.id == person.id, Person.company_id == ctx.company_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if current is None or current.consent_status != "granted":
            raise ApiError(
                409, "consent_required", "This person's consent was withdrawn, so no voice was saved."
            )
        row, replaced = signatures.add_sample(db, settings, current, sample)
        record_audit(
            db,
            action="person.signature_sample_added",
            company_id=ctx.company_id,
            actor_user_id=ctx.user.id,
            object_type="person",
            object_id=person.id,
            detail={"sample_count": row.sample_count, "replaced_older_version": replaced},
            ip=ctx.ip,
        )
        db.commit()
    finally:
        spooled.path.unlink(missing_ok=True)  # the recording is never kept
    return _out(db, settings, person, show_pitch=True)


NO_PROOF = (
    "A similarity score says how alike two voices sound. It is not identity verification: voices can be "
    "alike, and this method has not been calibrated on real people. Treat a name here as a lead to check, "
    "not a fact."
)


@router.post("/signature-matches", response_model=SignatureMatchOut)
async def match_signature(
    request: Request,
    ctx: Manager,
    db: DbDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> SignatureMatchOut:
    """Upload a WAV of someone speaking and see whom it is most like among the people who have consented and
    have a voice signature. Pitch is compared first (a much higher or lower voice is never a candidate), then
    the colour of the voice. A name is returned as the match only if it is clearly closer than everyone else;
    otherwise there is no match. The upload is deleted straight away and the lookup is written to the audit
    log."""
    enforce(limiter, f"signature-match:{ctx.user.id}", 20, 60)
    spooled, _ = await _receive_sample(request, settings, db)
    try:
        sample = await run_in_threadpool(_measure, spooled.path)
    finally:
        spooled.path.unlink(missing_ok=True)

    candidates = signatures.candidates(db, ctx.company_id, settings)
    gate = settings.signature_pitch_gate_octaves
    ranked = score_candidates(sample, candidates, pitch_gate_octaves=gate)
    best = best_signature_match(
        sample,
        candidates,
        threshold=settings.signature_match_threshold,
        margin=settings.signature_match_margin,
        pitch_gate_octaves=gate,
    )
    wanted = [c.person_id for c in ranked]
    names = {
        person.id: person.display_name
        for person in db.scalars(
            select(Person).where(Person.company_id == ctx.company_id, Person.id.in_(wanted))
        )
    }

    def describe(scored: Scored) -> SignatureCandidateOut:
        return SignatureCandidateOut(
            person_id=scored.person_id,
            name=names.get(scored.person_id, "Unknown"),
            score=round(scored.score, 3),
            pitch_gap_octaves=round(scored.pitch_gap_octaves, 2),
            passed_pitch_gate=scored.passed_gate,
        )

    closest = [describe(s) for s in ranked[:3]]
    chosen = next((c for c in closest if best is not None and c.person_id == best.person_id), None)
    outcome: Literal["matched", "no_confident_match", "nobody_enrolled"] = (
        "nobody_enrolled" if not candidates else "matched" if chosen else "no_confident_match"
    )
    record_audit(
        db,
        action="voice_signature.matched",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="company",
        object_id=ctx.company_id,
        detail={"outcome": outcome, "candidates": len(candidates)},
        ip=ctx.ip,
    )
    db.commit()
    return SignatureMatchOut(
        outcome=outcome,
        pitch=_pitch_out(sample.pitch),
        voiced_seconds=round(sample.pitch.voiced_seconds, 1),
        candidates_considered=len(candidates),
        match=chosen,
        closest=closest,
        notice=NO_PROOF,
    )


@router.delete("/{person_id}/voice-profile", status_code=204)
def delete_voice_profile(person_id: uuid.UUID, ctx: Manager, db: DbDep) -> Response:
    person = _get_person(db, ctx, person_id)
    removed = _forget_voice(db, person)
    record_audit(
        db,
        action="person.voice_profile_deleted",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="person",
        object_id=person.id,
        detail={"existed": removed},
        ip=ctx.ip,
    )
    db.commit()
    return Response(status_code=204)


@router.delete("/{person_id}", status_code=204)
def delete_person(person_id: uuid.UUID, ctx: Manager, db: DbDep) -> Response:
    person = _get_person(db, ctx, person_id)
    _forget_voice(db, person)
    record_audit(
        db,
        action="person.deleted",
        company_id=ctx.company_id,
        actor_user_id=ctx.user.id,
        object_type="person",
        object_id=person.id,
        ip=ctx.ip,
    )
    db.delete(person)
    db.commit()
    return Response(status_code=204)
