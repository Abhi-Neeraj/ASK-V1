"""Receiving a recording from a locket.

The contract with the device is: after a success response the recording is stored durably and its
record is committed, so the device may archive its own copy. Nothing is acknowledged earlier. The
same upload can be repeated any number of times (after a lost response, a reboot, a dropped
connection) and never creates a second recording.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import timeutil
from app.config import Settings
from app.errors import ApiError
from app.models import Device, ProcessingJob, Recording
from app.security.deps import DeviceCtx
from app.services.audio import WavInfo, inspect_wav
from app.services.audit import publish_event, recording_event
from app.services.storage import Storage

logger = logging.getLogger("ask.uploads")

CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
SESSION_REF_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Spooled:
    path: Path
    size: int
    sha256: str


async def spool_to_disk(
    stream: AsyncIterator[bytes],
    storage: Storage,
    *,
    max_bytes: int,
    read_timeout: float,
    total_timeout: float | None = None,
) -> Spooled:
    """Write the request body to a temp file while hashing it. Cleans up if anything goes wrong,
    including the client disconnecting halfway (the iterator then raises).

    `read_timeout` limits the wait for each piece; `total_timeout` limits the whole body, so a sender who
    keeps each gap short but never finishes is cut off too."""
    path = storage.new_temp_path()
    hasher = hashlib.sha256()
    size = 0
    deadline = None if total_timeout is None else time.monotonic() + total_timeout
    try:
        with path.open("wb") as handle:
            iterator = stream.__aiter__()
            while True:
                wait = read_timeout
                if deadline is not None:
                    wait = min(wait, deadline - time.monotonic())
                    if wait <= 0:
                        raise ApiError(408, "upload_timeout", "The upload took too long and was cancelled.")
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), wait)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    raise ApiError(408, "upload_timeout", "The upload stalled and was cancelled.") from None
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise ApiError(413, "upload_too_large", "The recording is larger than the allowed size.")
                hasher.update(chunk)
                await anyio.to_thread.run_sync(handle.write, chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    if size == 0:
        path.unlink(missing_ok=True)
        raise ApiError(422, "invalid_audio", "The upload was empty.")
    return Spooled(path=path, size=size, sha256=hasher.hexdigest())


def parse_recorded_at(value: str | None, now: datetime) -> datetime:
    if not value:
        raise ApiError(422, "validation_error", "X-Recorded-At is required.", {"x-recorded-at": "Required."})
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(
            422,
            "validation_error",
            "X-Recorded-At must be an ISO 8601 timestamp.",
            {"x-recorded-at": "Invalid."},
        ) from None
    if parsed.tzinfo is None:
        raise ApiError(
            422,
            "validation_error",
            "X-Recorded-At must include a timezone.",
            {"x-recorded-at": "Add a timezone."},
        )
    if parsed > now + timedelta(days=1) or parsed.year < 2020:
        raise ApiError(
            422,
            "validation_error",
            "X-Recorded-At is not a plausible time.",
            {"x-recorded-at": "Implausible."},
        )
    return parsed


@dataclass(frozen=True)
class StoredRecording:
    recording: Recording
    duplicate: bool


def finalize_upload(
    db: Session,
    settings: Settings,
    storage: Storage,
    ctx: DeviceCtx,
    *,
    client_id: str,
    spooled: Spooled,
    declared_sha256: str,
    recorded_at: datetime,
    session_ref: str | None,
    sequence: int | None,
) -> StoredRecording:
    temp = spooled.path
    try:
        if not SHA256_PATTERN.match(declared_sha256) or declared_sha256 != spooled.sha256:
            raise ApiError(
                422, "checksum_mismatch", "The received bytes do not match X-Content-SHA256. Send it again."
            )

        existing = db.scalar(
            select(Recording).where(
                Recording.device_id == ctx.device.id, Recording.client_recording_id == client_id
            )
        )
        if existing is not None:
            return _replay(existing, spooled)

        info = inspect_wav(temp, settings)
        recording_id = uuid.uuid4()
        key = Storage.key_for(ctx.company_id, recording_id, recorded_at)
        final_path = storage.commit(temp, key)  # durable on disk from here
        temp = final_path  # if the database step fails, the file must go
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    # Who has the locket right now, read from the database at the moment the clip is stored. The copy loaded
    # when the
    # request was authenticated can be minutes old, and an upload can take a while: whoever holds the locket
    # when
    # the clip arrives is who it belongs to, whatever happens to the locket afterwards.
    wearer = db.scalar(select(Device.assigned_user_id).where(Device.id == ctx.device.id))

    recording = Recording(
        id=recording_id,
        company_id=ctx.company_id,
        device_id=ctx.device.id,
        assigned_user_id=wearer,
        client_recording_id=client_id,
        session_ref=session_ref,
        sequence=sequence,
        started_at=recorded_at,
        ended_at=recorded_at + timedelta(milliseconds=info.duration_ms),
        duration_ms=info.duration_ms,
        size_bytes=spooled.size,
        sha256=spooled.sha256,
        sample_rate=info.sample_rate,
        channels=info.channels,
        bits_per_sample=info.bits_per_sample,
        format=_describe(info),
        storage_key=key,
        state="queued",
    )
    try:
        db.add(recording)
        db.flush()  # the job references the recording; insert order is not inferred without relationships
        db.add(ProcessingJob(recording_id=recording_id, company_id=ctx.company_id))
        ctx.device.last_upload_at = timeutil.utcnow()
        publish_event(
            db,
            ctx.company_id,
            "recording.uploaded",
            recording_event(recording),
        )
        db.commit()
    except IntegrityError:
        # Two uploads of the same clip raced and the other one won. Ours is redundant.
        db.rollback()
        final_path.unlink(missing_ok=True)
        winner = db.scalar(
            select(Recording).where(
                Recording.device_id == ctx.device.id, Recording.client_recording_id == client_id
            )
        )
        if winner is None:
            raise
        return _replay(winner, spooled, already_cleaned=True)
    except BaseException:
        db.rollback()
        final_path.unlink(missing_ok=True)
        raise
    return StoredRecording(recording=recording, duplicate=False)


def _describe(info: WavInfo) -> str:
    layout = "mono" if info.channels == 1 else "stereo"
    return f"WAV, {info.sample_rate // 1000} kHz, {layout}, {info.bits_per_sample}-bit"


def _replay(existing: Recording, spooled: Spooled, *, already_cleaned: bool = False) -> StoredRecording:
    """The same clip arrived again: acknowledge it without storing anything twice."""
    if not already_cleaned:
        spooled.path.unlink(missing_ok=True)
    if existing.sha256 != spooled.sha256:
        raise ApiError(
            409,
            "recording_conflict",
            "A different recording with this ID was already received. Use a new recording ID.",
        )
    return StoredRecording(recording=existing, duplicate=True)
