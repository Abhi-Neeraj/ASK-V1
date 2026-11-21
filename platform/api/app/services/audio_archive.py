"""A compressed copy of each recording's audio, kept in the database.

Why: a free server host has no permanent disk, so a recording's audio file disappears when the server
restarts, and "Play audio" with it. The database is the one thing that lasts. A WAV is 1.9 MB a minute; as
IMA ADPCM (four bits a sample, what the locket itself sends over Bluetooth) it is a quarter of that, so a
free database of 1 GB holds about 35 hours of speech. `audio_archive_max_bytes` caps the total: past it new
audio is simply not archived (the transcript and summary are unaffected).

The copy is decoded back to a WAV when someone plays it, so it sounds as the locket's ADPCM did, not as the
file that was uploaded (it is a second ADPCM pass, a little noisier). It is never used to process a recording
again: that needs the original bytes, whose size and hash are checked.

`audioop` is in Python's standard library up to 3.12 (3.11 here); without it nothing is archived.
"""

from __future__ import annotations

import logging
import struct
import warnings
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import RecordingAudio

logger = logging.getLogger("ask.audio_archive")

try:  # the module is gone from Python 3.13 (importing it before then only warns)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import audioop  # type: ignore[import-not-found,unused-ignore]
except ImportError:  # pragma: no cover
    audioop = None  # type: ignore[assignment]

_CHUNK = 32768  # PCM bytes at a time (a multiple of 4, so every chunk is a whole number of ADPCM bytes)


def available() -> bool:
    return audioop is not None


def compress(wav_path: Path) -> tuple[bytes, int, int]:
    """(ADPCM bytes, sample rate, size of the decoded sample data) for a mono 16-bit WAV."""
    if audioop is None:
        raise RuntimeError("audioop is not available")
    packed = bytearray()
    state = None
    with wave.open(str(wav_path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise ValueError("only mono 16-bit audio is archived")
        rate = reader.getframerate()
        pcm_bytes = 0
        while True:
            frames = reader.readframes(_CHUNK // 2)
            if not frames:
                break
            if len(frames) % 4:  # an odd last sample: add one silent sample so the ADPCM bytes are whole
                frames += bytes(4 - len(frames) % 4)
            chunk, state = audioop.lin2adpcm(frames, 2, state)
            packed.extend(chunk)
            pcm_bytes += len(frames)
    return bytes(packed), rate, pcm_bytes


def decode_chunks(data: bytes) -> Iterator[bytes]:
    """The archived ADPCM as PCM, a piece at a time."""
    if audioop is None:
        raise RuntimeError("audioop is not available")
    state = None
    step = _CHUNK // 4
    for start in range(0, len(data), step):
        pcm, state = audioop.adpcm2lin(data[start : start + step], 2, state)
        yield pcm


def write_wav(data: bytes, sample_rate: int, pcm_bytes: int, destination: Path) -> None:
    """Decode archived audio into a WAV file at `destination`."""
    if audioop is None:
        raise RuntimeError("audioop is not available")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as out:
        out.write(b"RIFF" + struct.pack("<I", 36 + pcm_bytes) + b"WAVEfmt ")
        out.write(struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16))
        out.write(b"data" + struct.pack("<I", pcm_bytes))
        for pcm in decode_chunks(data):
            out.write(pcm)


def archived_bytes(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(RecordingAudio.stored_bytes), 0))) or 0)


def archive(db: Session, settings: Settings, recording_id: Any, wav_path: Path) -> bool:
    """Keep a compressed copy of the audio. False (and nothing stored) when archiving is off, there is no room
    under `audio_archive_max_bytes`, or the audio cannot be archived. Never raises."""
    cap = settings.audio_archive_max_bytes
    if cap <= 0 or audioop is None:
        return False
    try:
        data, rate, pcm_bytes = compress(wav_path)
        if archived_bytes(db) + len(data) > cap:
            logger.warning("the audio archive is full (%s bytes): this recording's audio is not kept", cap)
            return False
        db.merge(
            RecordingAudio(
                recording_id=recording_id,
                sample_rate=rate,
                pcm_bytes=pcm_bytes,
                stored_bytes=len(data),
                data=data,
            )
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("could not archive the audio of recording %s", recording_id)
        return False


def restore(db: Session, recording_id: Any, destination: Path) -> bool:
    """Write the archived audio of a recording to `destination` as a WAV. False when there is none."""
    row = db.get(RecordingAudio, recording_id)
    if row is None or audioop is None:
        return False
    write_wav(row.data, row.sample_rate, row.pcm_bytes, destination)
    return True
