"""WAV validation for uploads.

Only plain 16-bit PCM WAV is accepted, with a header that matches the bytes that follow. This is
deliberately strict: a clip whose header lies about its length usually means the locket lost power
or the card write failed, and silently transcribing a truncated clip would hide that.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.errors import ApiError

_READ_BLOCK = 64 * 1024
# Real files have a format section, the audio, and perhaps a few metadata sections. Without a limit, a file
# made
# of thousands of empty sections costs seconds of processor time to walk, from any paired locket.
_MAX_SECTIONS = 64


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    data_bytes: int
    duration_ms: int


def _bad(code: str, message: str, status: int = 422) -> ApiError:
    return ApiError(status, code, message)


def inspect_wav(path: Path, settings: Settings) -> WavInfo:
    size = path.stat().st_size
    with path.open("rb") as handle:
        header = handle.read(12)
        if len(header) < 12 or header[0:4] != b"RIFF" or header[8:12] != b"WAVE":
            raise _bad("invalid_audio", "The file is not a WAV recording.")
        riff_size = struct.unpack("<I", header[4:8])[0]
        if riff_size + 8 > size:
            raise _bad("truncated_audio", "The recording is shorter than its header says.")

        fmt: tuple[int, int, int, int, int] | None = None
        data_offset = data_len = -1
        # Walk the chunks: real files carry LIST/INFO and other chunks around "fmt " and "data".
        sections = 0
        while data_offset < 0:
            sections += 1
            if sections > _MAX_SECTIONS:
                raise _bad("invalid_audio", "The WAV file has too many sections before its audio.")
            chunk_header = handle.read(8)
            if len(chunk_header) < 8:
                break
            chunk_id, chunk_len = chunk_header[0:4], struct.unpack("<I", chunk_header[4:8])[0]
            if chunk_id == b"fmt ":
                body = handle.read(min(chunk_len, 40))
                if len(body) < 16:
                    raise _bad("invalid_audio", "The WAV format chunk is incomplete.")
                tag, channels, rate, _byte_rate, block_align, bits = struct.unpack("<HHIIHH", body[:16])
                fmt = (tag, channels, rate, block_align, bits)
                handle.seek(chunk_len - len(body) + (chunk_len & 1), 1)
            elif chunk_id == b"data":
                data_offset, data_len = handle.tell(), chunk_len
            else:
                handle.seek(chunk_len + (chunk_len & 1), 1)

        if fmt is None or data_offset < 0:
            raise _bad("invalid_audio", "The WAV file is missing its format or audio data.")

        tag, channels, rate, block_align, bits = fmt
        if tag != 1:
            raise _bad("unsupported_audio_format", "Only uncompressed PCM audio is accepted.")
        if bits != 16:
            raise _bad("unsupported_audio_format", "Only 16-bit audio is accepted.")
        if channels not in (1, 2):
            raise _bad("unsupported_audio_format", "Only mono or stereo audio is accepted.")
        if rate not in settings.allowed_sample_rates:
            allowed = ", ".join(str(r) for r in settings.allowed_sample_rates)
            raise _bad("unsupported_sample_rate", f"The sample rate must be one of: {allowed} Hz.")
        if block_align != channels * bits // 8:
            raise _bad("invalid_audio", "The WAV header is inconsistent.")

        if data_offset + data_len > size:
            raise _bad("truncated_audio", "The audio data is shorter than its header says.")
        if data_len == 0 or data_len % block_align != 0:
            raise _bad("invalid_audio", "The audio data is empty or cut mid-sample.")

        duration_ms = round(data_len / (rate * block_align) * 1000)
        if duration_ms < settings.min_clip_seconds * 1000:
            raise _bad("audio_too_short", "The recording is too short to use.")
        if duration_ms > settings.max_clip_seconds * 1000:
            raise _bad("audio_too_long", "The recording is longer than the allowed clip length.", 413)

        # A clip that is nothing but zeros is a dead microphone or a failed write, not speech.
        handle.seek(data_offset)
        remaining = data_len
        while remaining > 0:
            block = handle.read(min(_READ_BLOCK, remaining))
            if not block:
                break
            if block.count(0) != len(block):
                break
            remaining -= len(block)
        else:
            raise _bad("silent_audio", "The recording contains no audio signal.")

    return WavInfo(
        sample_rate=rate,
        channels=channels,
        bits_per_sample=bits,
        data_bytes=data_len,
        duration_ms=duration_ms,
    )
