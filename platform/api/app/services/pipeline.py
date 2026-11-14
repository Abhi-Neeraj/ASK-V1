"""Processing one recording: validate, transcribe, separate speakers, name them, align, save.

    queued -> validating -> transcribing -> diarizing -> identifying_speakers -> aligning
           -> completed | needs_review          (or retrying / failed)

Rules this follows:

* A stage is only announced if it really runs. With no speaker model there is no
  `identifying_speakers` step, because announcing it would be a small lie.
* No database transaction is open while a model runs. Models take seconds to minutes, and a held write
  lock would freeze the whole API. Each stage commits first, then lets go.
* While a model runs, a background lease keeps the job from being mistaken for a crashed one.
* The result is written in ONE transaction, only if this worker still owns the job, so a worker that
  stalled and was replaced can never overwrite the replacement's work.
* Nothing names a person except a voice match that cleared the threshold and the margin.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import threading
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import delete, exists, or_, select
from sqlalchemy.orm import Session

from app import timeutil
from app.ai.types import Diarization, PermanentError, Provider, SpeakerEmbedder, Transcription, Turn
from app.config import Settings
from app.db import SessionFactory
from app.errors import ApiError
from app.insights.analyze import Utterance, analyze
from app.models import (
    Person,
    PersonSignature,
    Recording,
    RecordingAnalysis,
    Transcript,
    TranscriptSegment,
    VoiceProfile,
)
from app.services import insights, jobs, signatures
from app.services.alignment import AlignedSegment, align
from app.services.audio import inspect_wav
from app.services.audit import publish_event, record_audit, recording_event
from app.services.jobs import ClaimedJob, LostLeaseError
from app.services.matching import Candidate, best_match, decode_vector
from app.services.storage import Storage
from app.voiceid.audio_io import load_for_analysis
from app.voiceid.matching import SignatureCandidate, best_signature_match
from app.voiceid.speakers import SpeakerVoice, measure_speakers

logger = logging.getLogger("ask.pipeline")

# A line the recognizer was this unsure of goes to a person to check.
REVIEW_BELOW_CONFIDENCE = 0.5
# The most speech compared per speaker. More does not help and costs time.
MAX_SPEAKER_SECONDS = 30
_HASH_BLOCK = 1024 * 1024

Outcome = str  # "done" | "retry" | "failed" | "lost" | "skipped" | "released"
T = TypeVar("T")


class RecordingGoneError(Exception):
    """The recording was deleted (or never existed). Nothing to process."""


class ShutdownRequestedError(Exception):
    """The worker was asked to stop. The job goes back in the queue for another worker."""


@dataclass(frozen=True)
class _Work:
    recording_id: uuid.UUID
    company_id: uuid.UUID
    device_id: uuid.UUID
    storage_key: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class Identity:
    person_id: uuid.UUID
    score: float
    # What named them: the speaker model's voice print, or the voice signature (pitch plus voice colour).
    method: str = "voice_model"


class Pipeline:
    def __init__(
        self,
        factory: SessionFactory,
        settings: Settings,
        provider: Provider,
        storage: Storage,
        worker_id: str,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        self.factory = factory
        self.settings = settings
        self.provider = provider
        self.storage = storage
        self.worker_id = worker_id
        self.should_stop = should_stop

    # --- the whole job ------------------------------------------------------------------------------

    def run(self, job: ClaimedJob) -> Outcome:
        """Process one claimed job to a conclusion. Never raises: every failure is recorded."""
        try:
            work = self._load(job)
            with self._lease(job) as lost:
                path = self._stage(job, "validating", lambda: self._validate(work))
                transcription = self._stage(
                    job, "transcribing", lambda: self.provider.transcriber.transcribe(path)
                )
                self._check(lost)

                diarization: Diarization | None = None
                if self.provider.diarizer is not None:
                    diarizer = self.provider.diarizer
                    diarization = self._stage(job, "diarizing", lambda: diarizer.diarize(path))
                    self._check(lost)
                turns = list(diarization.turns) if diarization else []

                identities: dict[str, Identity] = {}
                voices: dict[str, SpeakerVoice] = {}
                candidates = self._candidates(work) if self.provider.embedder and turns else []
                # Voice signatures (pitch plus voice colour) are compared too, for speakers the speaker model
                # did not name. Like the speaker model, only when a separator said who spoke when.
                signature_candidates = self._signature_candidates(work) if turns else []
                if (self.provider.embedder is not None and candidates) or signature_candidates:
                    identities = self._stage(
                        job,
                        "identifying_speakers",
                        lambda: self._identify(path, turns, candidates, signature_candidates, voices),
                    )
                    self._check(lost)

                aligned = self._stage(job, "aligning", lambda: align(transcription.segments, turns))
                self._check(lost)
                # What was said, worked out by fixed rules, and each speaker's pitch. Part of finishing the
                # transcript; if it cannot be made the transcript is still saved without it.
                analysis = self._analyse(path, work, turns, aligned, identities, voices)
                self._check(lost)
            self._save(job, work, transcription, diarization, aligned, identities, analysis)
            return "done"
        except RecordingGoneError:
            with self.factory() as db:
                jobs.finish_unneeded(db, job, self.worker_id, "The recording was deleted.")
            return "skipped"
        except ShutdownRequestedError:
            with self.factory() as db:
                jobs.release(db, job, self.worker_id)
            return "released"
        except LostLeaseError:
            logger.warning("job %s was taken over by another worker; discarding this attempt", job.id)
            return "lost"
        except PermanentError as error:
            return self._failed(job, str(error), permanent=True)
        except Exception as error:
            logger.exception(
                "processing job %s failed (attempt %s of %s)", job.id, job.attempts, job.max_attempts
            )
            return self._failed(job, f"{type(error).__name__}: {error}", permanent=False)

    def _failed(self, job: ClaimedJob, message: str, *, permanent: bool) -> Outcome:
        with self.factory() as db:
            return jobs.record_failure(
                db,
                self.settings,
                job,
                self.worker_id,
                message,
                permanent=permanent,
                storage_root=self.storage.root,
            )

    # --- stages -------------------------------------------------------------------------------------

    def _stage(self, job: ClaimedJob, state: str, work: Callable[[], T]) -> T:
        """Announce a stage (committing, so no lock is held), then run it."""
        with self.factory() as db:
            row = jobs.require_lease(db, job, self.worker_id)
            recording = db.get(Recording, job.recording_id)
            if recording is None or recording.deleted_at is not None:
                db.rollback()
                raise RecordingGoneError
            now = timeutil.utcnow()
            row.locked_at = now
            row.updated_at = now
            recording.state = state
            recording.error = None
            publish_event(
                db,
                recording.company_id,
                "recording.state",
                recording_event(recording, state=state),
            )
            db.commit()
        return work()

    def _check(self, lost: threading.Event) -> None:
        """Between stages: stop if the job was taken over, or if the worker is shutting down."""
        if lost.is_set():
            raise LostLeaseError("lease lost while a model was running")
        if self.should_stop():
            raise ShutdownRequestedError

    def _load(self, job: ClaimedJob) -> _Work:
        with self.factory() as db:
            recording = db.get(Recording, job.recording_id)
            if recording is None or recording.deleted_at is not None:
                raise RecordingGoneError
            return _Work(
                recording_id=recording.id,
                company_id=recording.company_id,
                device_id=recording.device_id,
                storage_key=recording.storage_key,
                sha256=recording.sha256,
                size_bytes=recording.size_bytes,
            )

    def _validate(self, work: _Work) -> Path:
        """The file is there, is the file that was uploaded, and is still a WAV we can use."""
        path = self.storage.path_for(work.storage_key)
        if not path.is_file():
            raise PermanentError("The audio file is missing from storage.")
        if path.stat().st_size != work.size_bytes:
            raise PermanentError("The stored audio is not the size that was uploaded.")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(_HASH_BLOCK):
                digest.update(block)
        if digest.hexdigest() != work.sha256:
            raise PermanentError("The stored audio no longer matches its checksum, so it may be damaged.")
        try:
            inspect_wav(path, self.settings)
        except ApiError as error:
            raise PermanentError(error.message) from error
        return path

    @contextlib.contextmanager
    def _lease(self, job: ClaimedJob) -> Iterator[threading.Event]:
        """Keep the job's lease alive while a model runs for longer than the stall timeout."""
        stop, lost = threading.Event(), threading.Event()
        interval = max(self.settings.job_lock_timeout_seconds / 3, 0.05)

        def keep_alive() -> None:
            while not stop.wait(interval):
                try:
                    with self.factory() as db:
                        if not jobs.touch(db, job, self.worker_id):
                            lost.set()
                            return
                except Exception:
                    logger.exception("could not renew the lease for job %s", job.id)

        thread = threading.Thread(target=keep_alive, name=f"lease-{job.id}", daemon=True)
        thread.start()
        try:
            yield lost
        finally:
            stop.set()
            thread.join(timeout=5)

    # --- speaker names ------------------------------------------------------------------------------

    def _candidates(self, work: _Work) -> list[Candidate]:
        """Voices this company may be matched against: consented, fully enrolled, same model, same company."""
        embedder = self.provider.embedder
        if embedder is None:
            return []
        with self.factory() as db:
            rows = db.execute(
                select(VoiceProfile, Person.id)
                .join(Person, Person.id == VoiceProfile.person_id)
                .where(
                    Person.company_id == work.company_id,
                    VoiceProfile.company_id == work.company_id,
                    Person.consent_status == "granted",
                    VoiceProfile.sample_count >= self.settings.voice_enroll_min_samples,
                    # Voice prints from a different model cannot be compared, so they are not tried.
                    VoiceProfile.model_name == embedder.model,
                )
            ).all()
            return [
                Candidate(person_id, decode_vector(p.embedding, p.embedding_dim)) for p, person_id in rows
            ]

    def _signature_candidates(self, work: _Work) -> list[SignatureCandidate]:
        with self.factory() as db:
            return signatures.candidates(db, work.company_id, self.settings)

    def _identify(
        self,
        path: Path,
        turns: Sequence[Turn],
        candidates: Sequence[Candidate],
        signature_candidates: Sequence[SignatureCandidate],
        voices: dict[str, SpeakerVoice],
    ) -> dict[str, Identity]:
        """The speaker model first; then, for speakers it left unnamed, the voice signature. The speaker
        measurements are kept in `voices` so the analysis does not make them again."""
        found: dict[str, Identity] = {}
        embedder = self.provider.embedder
        if embedder is not None and candidates:
            found = identify_speakers(self.settings, embedder, path, turns, candidates)
        if signature_candidates:
            spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
            for turn in turns:
                spans[turn.speaker].append((turn.start_ms, turn.end_ms))
            voices.update(
                measure_speakers(
                    load_for_analysis(path),
                    spans,
                    min_speech_seconds=self.settings.speaker_min_speech_seconds,
                )
            )
            found.update(identify_by_signature(self.settings, voices, signature_candidates, skip=set(found)))
        return found

    def _analyse(
        self,
        path: Path,
        work: _Work,
        turns: Sequence[Turn],
        aligned: Sequence[AlignedSegment],
        identities: dict[str, Identity],
        voices: dict[str, SpeakerVoice],
    ) -> dict[str, Any] | None:
        """The voices of the recording (pitch, talk time, how each was named) and what was said in it, as data
        to be saved with the transcript. None for placeholder transcripts (a summary of text that is not what
        was said would mislead) and when anything goes wrong: a failed summary never fails the recording."""
        if self.provider.placeholder or not aligned:
            return None
        try:
            labels = {s.speaker for s in aligned}
            if not labels <= set(voices):
                spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
                for part in turns or aligned:
                    spans[part.speaker].append((part.start_ms, part.end_ms))
                voices = measure_speakers(
                    load_for_analysis(path),
                    spans,
                    min_speech_seconds=self.settings.speaker_min_speech_seconds,
                )
            rows = [
                _voice_row(voices[label], identities.get(label))
                for label in sorted(labels)
                if label in voices
            ]
            with self.factory() as db:
                names = insights.given_names(db, work.company_id)
            utterances = [
                Utterance(i, s.start_ms, s.end_ms, s.speaker, s.text) for i, s in enumerate(aligned)
            ]
            return analyze(
                utterances,
                rows,
                insights.classifier_for(self.settings),
                names,
                self.settings.context_similarity,
            )
        except Exception:
            logger.exception(
                "could not analyse recording %s; saving its transcript without a summary", work.recording_id
            )
            return None

    # --- saving -------------------------------------------------------------------------------------

    def _save(
        self,
        job: ClaimedJob,
        work: _Work,
        transcription: Transcription,
        diarization: Diarization | None,
        aligned: Sequence[AlignedSegment],
        identities: dict[str, Identity],
        analysis: dict[str, Any] | None = None,
    ) -> None:
        """Write the transcript (and its analysis) and finish the job together, or not at all."""
        placeholder = self.provider.placeholder
        with self.factory() as db:
            row = jobs.require_lease(db, job, self.worker_id)
            recording = db.get(Recording, work.recording_id)
            if recording is None or recording.deleted_at is not None:
                db.rollback()
                raise RecordingGoneError

            _remove_transcript(db, recording.id)  # reprocessing replaces the old one

            # Names were worked out earlier, possibly minutes ago. Someone whose consent was withdrawn since,
            # or
            # whose voice print was deleted, must not be named by a transcript written now. The rows are held
            # (a shared lock on databases that have one) until this commits, so it cannot change underneath
            # us.
            wanted = {i.person_id for i in identities.values()}
            allowed: set[uuid.UUID] = set()
            if wanted:
                allowed = set(
                    db.scalars(
                        select(Person.id)
                        .where(
                            Person.id.in_(wanted),
                            Person.company_id == recording.company_id,
                            Person.consent_status == "granted",
                            # Still has the voice data they were recognised by (a voice print or a signature).
                            or_(
                                exists().where(VoiceProfile.person_id == Person.id),
                                exists().where(PersonSignature.person_id == Person.id),
                            ),
                        )
                        .with_for_update(read=True, of=Person)
                    )
                )
            identities = {k: v for k, v in identities.items() if v.person_id in allowed}
            needs_review = [
                (not placeholder)
                and (s.uncertain or (s.confidence is not None and s.confidence < REVIEW_BELOW_CONFIDENCE))
                for s in aligned
            ]
            now = timeutil.utcnow()
            transcript = Transcript(
                recording_id=recording.id,
                company_id=recording.company_id,
                full_text="\n".join(s.text for s in aligned),
                transcription_model=transcription.model,
                transcription_model_version=transcription.model_version,
                diarization_model=diarization.model if diarization else None,
                diarization_model_version=diarization.model_version if diarization else None,
                is_placeholder=placeholder,
                language=transcription.language,
                review_status="needs_review" if any(needs_review) else "none",
                created_at=now,
                updated_at=now,
            )
            db.add(transcript)
            db.flush()
            for index, (segment, review) in enumerate(zip(aligned, needs_review, strict=True)):
                who = identities.get(segment.speaker)
                db.add(
                    TranscriptSegment(
                        transcript_id=transcript.id,
                        company_id=recording.company_id,
                        idx=index,
                        start_ms=segment.start_ms,
                        end_ms=segment.end_ms,
                        speaker_label=segment.speaker,
                        person_id=who.person_id if who else None,
                        confidence=who.score if who else None,
                        text=segment.text,
                        needs_review=review,
                        corrected=False,
                    )
                )

            if analysis is not None:
                # Drop what a name was found by if the name did not survive the consent check above.
                for voice in analysis["voices"]:
                    if voice["label"] not in identities:
                        voice["match"] = None
                insights.store(db, transcript, analysis)

            recording.state = "needs_review" if any(needs_review) else "completed"
            recording.error = None
            row.status = "done"
            row.completed_at = now
            row.locked_by = None
            row.locked_at = None
            row.updated_at = now
            record_audit(
                db,
                action="recording.processed",
                company_id=recording.company_id,
                object_type="recording",
                object_id=recording.id,
                detail={
                    "provider": self.provider.name,
                    "placeholder": placeholder,
                    "segments": len(aligned),
                    "diarized": diarization is not None,
                    "speakersNamed": len({i.person_id for i in identities.values()}),
                },
            )
            publish_event(
                db,
                recording.company_id,
                "recording.state",
                recording_event(recording, state=recording.state),
            )
            db.commit()


def identify_speakers(
    settings: Settings,
    embedder: SpeakerEmbedder,
    path: Path,
    turns: Sequence[Turn],
    candidates: Sequence[Candidate],
) -> dict[str, Identity]:
    """For each separated speaker with enough speech, who (if anyone) the voice clearly belongs to.

    Shared by the worker and by `app.cli process-file`, so what is tried on a file by hand is exactly what
    runs on a recording.
    """
    spans: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for turn in turns:
        spans[turn.speaker].append((turn.start_ms, turn.end_ms))

    found: dict[str, Identity] = {}
    minimum_ms = int(settings.speaker_min_speech_seconds * 1000)
    for speaker, speaker_spans in spans.items():
        if sum(end - start for start, end in speaker_spans) < minimum_ms:
            continue  # too little to judge a voice by: left as an unknown speaker
        embedding = embedder.embed(path, _longest_spans(speaker_spans, MAX_SPEAKER_SECONDS * 1000))
        match = best_match(
            embedding.vector,
            candidates,
            threshold=settings.speaker_match_threshold,
            margin=settings.speaker_match_margin,
        )
        if match is not None:
            found[speaker] = Identity(match.person_id, max(0.0, min(1.0, match.score)))
    return found


def identify_by_signature(
    settings: Settings,
    voices: dict[str, SpeakerVoice],
    candidates: Sequence[SignatureCandidate],
    *,
    skip: set[str],
) -> dict[str, Identity]:
    """For each measured speaker the speaker model did not name, who (if anyone) their voice signature clearly
    belongs to: within the pitch gate, over the similarity threshold, and clear of the next person by the
    margin.
    Speakers who spoke too little to have a signature are left as unknown speakers."""
    found: dict[str, Identity] = {}
    for label, voice in voices.items():
        if label in skip or voice.signature is None:
            continue
        match = best_signature_match(
            voice.signature,
            candidates,
            threshold=settings.signature_match_threshold,
            margin=settings.signature_match_margin,
            pitch_gate_octaves=settings.signature_pitch_gate_octaves,
        )
        if match is not None:
            found[label] = Identity(match.person_id, max(0.0, min(1.0, match.score)), "signature")
    return found


def _voice_row(voice: SpeakerVoice, identity: Identity | None) -> dict[str, Any]:
    """One speaker as it is stored with the analysis: talk time, pitch summary, and how a name was found.
    The voice signature itself is never part of it."""
    return {
        "label": voice.label,
        "talkMs": voice.talk_ms,
        "turns": voice.turns,
        "pitch": None
        if voice.pitch is None
        else {
            "baseHz": round(voice.pitch.base_hz, 1),
            "lowHz": round(voice.pitch.low_hz, 1),
            "highHz": round(voice.pitch.high_hz, 1),
            "voicedSeconds": round(voice.pitch.voiced_seconds, 1),
        },
        "match": None if identity is None else {"method": identity.method, "score": round(identity.score, 3)},
    }


def _remove_transcript(db: Session, recording_id: uuid.UUID) -> None:
    transcript = db.scalar(select(Transcript).where(Transcript.recording_id == recording_id))
    if transcript is None:
        return
    db.execute(delete(RecordingAnalysis).where(RecordingAnalysis.transcript_id == transcript.id))
    db.execute(delete(TranscriptSegment).where(TranscriptSegment.transcript_id == transcript.id))
    db.delete(transcript)
    db.flush()


def _longest_spans(spans: Sequence[tuple[int, int]], budget_ms: int) -> list[tuple[int, int]]:
    """The longest stretches first until the budget is used, put back in time order."""
    chosen: list[tuple[int, int]] = []
    used = 0
    for start, end in sorted(spans, key=lambda s: s[1] - s[0], reverse=True):
        if used >= budget_ms:
            break
        take = min(end - start, budget_ms - used)
        chosen.append((start, start + take))
        used += take
    return sorted(chosen)
