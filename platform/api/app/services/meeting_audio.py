"""The audio of a recording that reached the server in parts, played as ONE file.

The locket's parts (half a minute each while it is being sent) are separate recordings on the server, but
the website shows them as one item, so "Play audio" on it plays them one after the other. Each part's sound
comes from its file on the server's disk, or, when the disk no longer has it, from the compressed copy in
the database (see audio_archive.py). Nothing is written to disk: the WAV is made as it is sent, so a
recording of hours does not need hours of space, and a seek (a Range request) reads and skips what came
before it.
"""

from __future__ import annotations

import re
import struct
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from fastapi import Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionFactory
from app.models import Recording, RecordingAudio
from app.services import audio_archive
from app.services.storage import Storage

_CHUNK = 65536


@dataclass(frozen=True)
class _Part:
    pcm_bytes: int
    read: Callable[[], Iterator[bytes]]


@dataclass(frozen=True)
class MergedAudio:
    sample_rate: int
    pcm_bytes: int  # sample data of every part together
    parts: tuple[_Part, ...]

    @property
    def size(self) -> int:
        return 44 + self.pcm_bytes

    def header(self) -> bytes:
        rate = self.sample_rate
        return (
            b"RIFF"
            + struct.pack("<I", 36 + self.pcm_bytes)
            + b"WAVEfmt "
            + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
            + b"data"
            + struct.pack("<I", self.pcm_bytes)
        )

    def stream(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        """The bytes of the WAV from `start` to `end` (inclusive; the whole file when not given)."""
        last = self.size - 1 if end is None else min(end, self.size - 1)
        position = 0

        def window(chunk: bytes) -> bytes:
            nonlocal position
            first = position
            position += len(chunk)
            if position - 1 < start or first > last:
                return b""
            return chunk[max(start - first, 0) : last - first + 1]

        out = window(self.header())
        if out:
            yield out
        for part in self.parts:
            if position > last:
                return
            if position + part.pcm_bytes <= start:  # a part wholly before the start: skip it without reading
                position += part.pcm_bytes
                continue
            for chunk in part.read():
                out = window(chunk)
                if out:
                    yield out
                if position > last:
                    return


def _local_part(path: Path) -> tuple[int, int, Callable[[], Iterator[bytes]]] | None:
    try:
        with wave.open(str(path), "rb") as reader:
            if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
                return None
            rate, frames = reader.getframerate(), reader.getnframes()
    except (OSError, wave.Error, EOFError):
        return None

    def read() -> Iterator[bytes]:
        with wave.open(str(path), "rb") as handle:
            while chunk := handle.readframes(_CHUNK // 2):
                yield chunk

    return rate, frames * 2, read


def _archived_part(
    factory: SessionFactory, recording_id: object, pcm_bytes: int
) -> Callable[[], Iterator[bytes]]:
    def read() -> Iterator[bytes]:
        # Its own session: the request's is closed by the time a long file is being sent.
        with factory() as db:
            row = db.get(RecordingAudio, recording_id)
        if row is None or not audio_archive.available():
            yield bytes(pcm_bytes)  # nothing to play for this part: silence of the right length
            return
        yield from audio_archive.decode_chunks(row.data)

    return read


def merge(
    db: Session, factory: SessionFactory, storage: Storage, parts: list[Recording]
) -> MergedAudio | None:
    """Everything needed to send the parts as one WAV, or None when none of them has any audio left."""
    archived = {
        recording_id: (rate, pcm_bytes)
        for recording_id, rate, pcm_bytes in db.execute(
            select(RecordingAudio.recording_id, RecordingAudio.sample_rate, RecordingAudio.pcm_bytes).where(
                RecordingAudio.recording_id.in_([p.id for p in parts])
            )
        )
    }
    pieces: list[_Part] = []
    rate = 0
    for part in parts:
        found = _local_part(storage.path_for(part.storage_key)) if storage.exists(part.storage_key) else None
        if found is not None:
            part_rate, pcm_bytes, read = found
        elif part.id in archived:
            part_rate, pcm_bytes = archived[part.id]
            read = _archived_part(factory, part.id, pcm_bytes)
        else:
            continue
        if rate == 0:
            rate = part_rate
        if part_rate != rate:
            continue  # a part at another sample rate cannot be joined into one WAV
        pieces.append(_Part(pcm_bytes, read))
    if not pieces:
        return None
    return MergedAudio(rate, sum(p.pcm_bytes for p in pieces), tuple(pieces))


_RANGE = re.compile(r"^\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*$", re.IGNORECASE)


def response(merged: MergedAudio, range_header: str | None, headers: dict[str, str]) -> Response:
    """The parts of a recording as one WAV, with the byte range a player asks for (one range at a time)."""
    size = merged.size
    headers = {**headers, "Accept-Ranges": "bytes"}
    found = _RANGE.match(range_header) if range_header else None
    if found is None:
        return StreamingResponse(
            merged.stream(), media_type="audio/wav", headers={**headers, "Content-Length": str(size)}
        )
    first, last = found.groups()
    if first == "" and last == "":
        start, end = 0, size - 1
    elif first == "":  # the last N bytes
        start, end = max(size - int(last), 0), size - 1
    else:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    if start >= size or start > end:
        return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
    return StreamingResponse(
        merged.stream(start, end),
        status_code=206,
        media_type="audio/wav",
        headers={
            **headers,
            "Content-Range": f"bytes {start}-{end}/{size}",
            "Content-Length": str(end - start + 1),
        },
    )
