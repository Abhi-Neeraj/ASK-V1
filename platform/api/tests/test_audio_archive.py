"""Audio is kept in the database (compressed), so playback survives a server whose disk does not last."""

from __future__ import annotations

import math
import struct
import uuid
import wave
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.types import AsrSegment, Provider
from app.models import Recording, RecordingAudio
from app.services import audio_archive
from tests.ai_doubles import FakeTranscriber
from tests.helpers import make_wav
from tests.test_meeting_parts import upload_part
from tests.test_phone_upload import make_setup
from tests.test_pipeline_summary import FakeSummarizer
from tests.test_processing import worker_for

pytestmark = pytest.mark.skipif(not audio_archive.available(), reason="audioop is not in this Python")


def provider() -> Provider:
    return Provider(
        name="test",
        placeholder=False,
        transcriber=FakeTranscriber([AsrSegment(1000, 3000, "We will ship on Friday.", (), 0.95)]),
        summarizer=FakeSummarizer("A SUMMARY"),
    )


def samples_of(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as reader:
        raw = reader.readframes(reader.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def test_compressed_audio_comes_back_as_the_same_sound_in_a_quarter_of_the_space(tmp_path: Path) -> None:
    source = tmp_path / "in.wav"
    source.write_bytes(make_wav(3.0))
    data, rate, pcm_bytes = audio_archive.compress(source)
    assert rate == 16000 and pcm_bytes == 3 * 16000 * 2
    assert len(data) * 4 <= pcm_bytes + 8  # four bits a sample
    restored = tmp_path / "out.wav"
    audio_archive.write_wav(data, rate, pcm_bytes, restored)
    before, after = samples_of(source), samples_of(restored)
    assert len(before) == len(after)
    # The sound is the same tone, a little noisier: the error is small next to the signal.
    signal = math.sqrt(sum(s * s for s in before) / len(before))
    error = math.sqrt(sum((a - b) ** 2 for a, b in zip(before, after, strict=True)) / len(before))
    assert error < 0.2 * signal, (error, signal)


def test_processing_keeps_a_copy_in_the_database_and_playback_works_without_the_file(
    app,
    acme,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    app.state.settings.audio_archive_max_bytes = 50_000_000
    setup = make_setup(app)
    recording_id = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    assert worker_for(app, provider()).run(once=True) == 1
    recording = db.get(Recording, recording_id)
    assert recording is not None
    assert not app.state.storage.exists(
        recording.storage_key
    )  # the file on the disk is gone: the copy is safe
    assert db.scalar(select(RecordingAudio).where(RecordingAudio.recording_id == recording_id)) is not None

    played = setup["manager"].get(f"/api/recordings/{recording_id}/audio")
    assert played.status_code == 200, played.text
    assert played.content[:4] == b"RIFF" and played.headers["content-type"] == "audio/wav"
    assert len(played.content) == 44 + 2 * 16000 * 2  # the 2 seconds that were uploaded
    part = setup["manager"].get(f"/api/recordings/{recording_id}/audio", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert not list(app.state.storage.tmp.glob("*.part"))  # the temporary decoded copies were removed


def test_when_the_archive_is_full_the_audio_is_not_kept_and_nothing_else_changes(
    app,
    acme,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    app.state.settings.audio_archive_max_bytes = 1000  # smaller than any recording
    setup = make_setup(app)
    recording_id = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    assert worker_for(app, provider()).run(once=True) == 1
    recording = db.get(Recording, recording_id)
    assert recording is not None and recording.state == "completed"
    assert app.state.storage.exists(recording.storage_key)  # the file stays where it was
    assert db.scalar(select(RecordingAudio).where(RecordingAudio.recording_id == recording_id)) is None


def test_archiving_is_off_by_default(app, acme, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert app.state.settings.audio_archive_max_bytes == 0
    setup = make_setup(app)
    recording_id = upload_part(setup, 0, datetime.now(UTC) - timedelta(minutes=5))
    assert worker_for(app, provider()).run(once=True) == 1
    assert db.scalar(select(RecordingAudio).where(RecordingAudio.recording_id == recording_id)) is None
    assert isinstance(uuid.uuid4(), uuid.UUID)
