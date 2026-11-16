"""Look at clips recorded by a locket (copied off its memory card) and say whether they are usable. Dev tool.

    python tools/inspect_clip.py D:\\pending\\*.wav
    python tools/inspect_clip.py path/to/folder

For each WAV: the format the server needs (16-bit mono PCM at 16 kHz), the duration, how loud it is (peak and
average, in dB below full scale), how much of it is clipped, and a plain verdict. It is how you check the
microphone and `kSoftwareGainShift` in the firmware's config.h on real hardware (platform/docs/
HARDWARE_ACCEPTANCE_TESTS.md). It only reads files and uses nothing but the standard library.
"""

from __future__ import annotations

import math
import sys
import wave
from array import array
from pathlib import Path

WANTED = (1, 2, 16_000)  # channels, bytes per sample, sample rate


def db(value: float) -> float:
    return 20 * math.log10(value / 32768) if value > 0 else -120.0


def inspect(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        with wave.open(str(path), "rb") as handle:
            channels, width, rate = handle.getnchannels(), handle.getsampwidth(), handle.getframerate()
            frames = handle.getnframes()
            raw = handle.readframes(frames)
    except (wave.Error, EOFError) as error:
        print(f"{path.name}: NOT A READABLE WAV ({error})")
        return ["unreadable"]

    if (channels, width, rate) != WANTED:
        problems.append(
            f"format is {channels} ch, {width * 8}-bit, {rate} Hz; the server wants 1 ch, 16-bit, 16000 Hz"
        )
    if width != 2:
        seconds = frames / rate
        print(f"{path.name}: {channels} ch {width * 8}-bit {rate} Hz, {seconds:.1f} s: cannot measure level")
        return problems
    samples: array[int] = array("h")
    samples.frombytes(raw[: len(raw) - len(raw) % 2])
    if not samples:
        print(f"{path.name}: NO AUDIO")
        return [*problems, "no audio"]

    peak = max(max(samples), -min(samples))
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    clipped = sum(1 for s in samples if s >= 32767 or s <= -32768) / len(samples)
    mean = sum(samples) / len(samples)
    seconds = len(samples) / (rate * channels)
    if peak == 0:
        problems.append("completely silent: the microphone is not delivering a signal")
    elif db(peak) < -40:
        problems.append("very quiet (peak below -40 dB): raise kSoftwareGainShift, or check the microphone")
    if clipped > 0.01:
        problems.append(f"{clipped:.1%} of samples are clipped: lower kSoftwareGainShift")
    if abs(mean) > 2000:
        problems.append(f"large DC offset ({mean:.0f}): the microphone output is not centred")
    if seconds < 0.5:
        problems.append("shorter than the server's 0.5 s minimum")

    print(
        f"{path.name}: {channels} ch {width * 8}-bit {rate} Hz, {seconds:.1f} s, "
        f"peak {db(peak):.1f} dBFS, average {db(rms):.1f} dBFS, clipped {clipped:.2%}"
        + ("   OK" if not problems else "")
    )
    for problem in problems:
        print(f"    - {problem}")
    return problems


def main(args: list[str]) -> int:
    if not args:
        print(__doc__)
        return 2
    paths: list[Path] = []
    for arg in args:
        path = Path(arg)
        paths.extend(sorted(path.glob("*.wav")) if path.is_dir() else [path])
    if not paths:
        print("No .wav files found.")
        return 2
    bad = sum(1 for path in paths if inspect(path))
    print(f"\n{len(paths)} file(s), {bad} with a problem.")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
