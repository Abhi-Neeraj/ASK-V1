"""Make a small set of REAL speech recordings for testing the processing pipeline. Development tool.

    python tools/make_sample_audio.py [--out ../data/samples/real-audio]

The audio is **synthetic**: spoken by the text-to-speech voices that ship with Windows (so Windows only,
and no model or network is needed). It is real speech audio, so a real transcriber and a real speaker
model have genuine work to do, and every file's content is known, so results can be checked. It is NOT
a stand-in for recordings of real people: it says nothing about accuracy on real voices, real rooms or
real microphones, and two synthetic voices are an easy case for speaker separation. Calibrate on
consented recordings of your own people (see platform/docs/AI.md).

Writes, under the output folder (which is git-ignored):

    voices/<voice>/enroll-1..3.wav   three enrollment samples per voice (different sentences)
    conversation.wav                 two voices taking turns, ground truth in manifest.json
    conversation-noisy.wav           the same with added noise (about 10 dB signal-to-noise)
    overlap.wav                      the second voice starts while the first is still speaking
    single-<voice>.wav               one voice alone, a sentence that was not used for enrollment
    short.wav                        a clip too short to judge a voice by
    silence.wav, quiet-noise.wav     nothing to transcribe
    bad-*.wav                        files the server must refuse (empty, truncated, not audio)
    manifest.json                    what is in each file, with the words and times that are known
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import tempfile
import wave
from array import array
from pathlib import Path

RATE = 16_000
VOICES = {"hazel": "Microsoft Hazel Desktop", "zira": "Microsoft Zira Desktop"}

ENROLL = {
    "hazel": [
        "The quarterly review meeting has been moved to Thursday afternoon, so please update your calendars.",
        "I will send the revised budget spreadsheet to everyone before the end of the day tomorrow.",
        "Our main priority this month is finishing the onboarding checklist for the new office.",
    ],
    "zira": [
        "Good morning everyone, let us go through the agenda and then review last week's action items.",
        "The customer feedback survey shows that response times have improved over the last two months.",
        "I think we should schedule a short follow up call to discuss the remaining open questions.",
    ],
}
# Sentences that are never used for enrollment, so identifying them is a real test.
CONVERSATION = [
    ("hazel", "Could you confirm whether the delivery arrives on Monday or on Tuesday morning?"),
    ("zira", "Please remember to submit your expense reports before the end of the month."),
    ("hazel", "Let us meet at the front entrance at nine o'clock and walk to the conference room together."),
    ("zira", "That sounds great, and I will bring the printed handouts for the whole team."),
]
SHORT = ("zira", "Yes, that works.")
GAP_SECONDS = 0.6

# Passed to PowerShell as an inline command (no script file, so no execution-policy change is needed). The
# sentences are handed over as lines of "voice<TAB>path<TAB>text" in a file named by an environment variable.
_SPEAK = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000,
    [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
foreach ($line in Get-Content -Encoding UTF8 $env:ASK_TTS_MANIFEST) {
    $voice, $path, $text = $line -split "`t", 3
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SelectVoice($voice)
    $synth.SetOutputToWaveFile($path, $format)
    $synth.Speak($text)
    $synth.Dispose()
}
"""


def read_wav(path: Path) -> array[int]:
    with wave.open(str(path), "rb") as handle:
        if (handle.getnchannels(), handle.getsampwidth(), handle.getframerate()) != (1, 2, RATE):
            raise SystemExit(f"{path.name} is not 16 kHz mono 16-bit")
        samples: array[int] = array("h")
        samples.frombytes(handle.readframes(handle.getnframes()))
    return samples


def write_wav(path: Path, samples: array[int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        handle.writeframes(samples.tobytes())


def silence(seconds: float) -> array[int]:
    return array("h", [0]) * int(seconds * RATE)


def trim(samples: array[int], threshold: int = 200, pad_ms: int = 80) -> array[int]:
    """Without the quiet lead-in and tail the text-to-speech engine puts around a sentence, so the times
    recorded in manifest.json are the times someone is actually speaking."""
    loud = [i for i, s in enumerate(samples) if abs(s) > threshold]
    if not loud:
        return samples
    pad = pad_ms * RATE // 1000
    return samples[max(0, loud[0] - pad) : min(len(samples), loud[-1] + 1 + pad)]


def clip(value: float) -> int:
    return max(-32768, min(32767, round(value)))


def rms(samples: array[int]) -> float:
    return (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5


def with_noise(samples: array[int], snr_db: float, seed: int) -> array[int]:
    rng = random.Random(seed)  # noqa: S311  (test audio, not security)
    sigma = rms(samples) / (10 ** (snr_db / 20))
    return array("h", (clip(s + rng.gauss(0, sigma)) for s in samples))


def overlay(first: array[int], second: array[int], offset_seconds: float) -> array[int]:
    start = int(offset_seconds * RATE)
    total = max(len(first), start + len(second))
    mixed = array("h", [0]) * total
    for i, s in enumerate(first):
        mixed[i] = s
    for i, s in enumerate(second):
        mixed[start + i] = clip(mixed[start + i] + s)
    return mixed


def synthesize(items: list[dict[str, str]]) -> None:
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / "items.json"
        manifest.write_text(
            "".join(f"{item['voice']}\t{item['path']}\t{item['text']}\n" for item in items), encoding="utf-8"
        )
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SPEAK],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "ASK_TTS_MANIFEST": str(manifest)},
        )
    if done.returncode != 0:
        raise SystemExit(f"Text-to-speech failed (Windows voices are required): {done.stderr.strip()[:400]}")
    missing = [Path(item["path"]).name for item in items if not Path(item["path"]).is_file()]
    if missing:
        raise SystemExit(f"Text-to-speech produced no file for: {', '.join(missing)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).resolve().parents[2] / "data/samples/real-audio"
    )
    out: Path = parser.parse_args().out
    if sys.platform != "win32":
        raise SystemExit("This tool uses the Windows text-to-speech voices, so it only runs on Windows.")

    work = out / "_utterances"
    items: list[dict[str, str]] = []
    for name, sentences in ENROLL.items():
        for n, text in enumerate(sentences, start=1):
            items.append(
                {"voice": VOICES[name], "text": text, "path": str(out / "voices" / name / f"enroll-{n}.wav")}
            )
    for n, (name, text) in enumerate(CONVERSATION):
        items.append({"voice": VOICES[name], "text": text, "path": str(work / f"turn-{n}.wav")})
    items.append({"voice": VOICES[SHORT[0]], "text": SHORT[1], "path": str(out / "short.wav")})
    for name in VOICES:
        (out / "voices" / name).mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    synthesize(items)

    # A conversation with known turns.
    pieces: list[dict[str, object]] = []
    audio: array[int] = array("h")
    for n, (name, text) in enumerate(CONVERSATION):
        speech = trim(read_wav(work / f"turn-{n}.wav"))
        start = len(audio) * 1000 // RATE
        audio.extend(speech)
        pieces.append({"speaker": name, "startMs": start, "endMs": len(audio) * 1000 // RATE, "text": text})
        audio.extend(silence(GAP_SECONDS))
    write_wav(out / "conversation.wav", audio)
    write_wav(out / "conversation-noisy.wav", with_noise(audio, 10, seed=1))

    # Two voices at once for part of the time.
    first, second = trim(read_wav(work / "turn-0.wav")), trim(read_wav(work / "turn-1.wav"))
    write_wav(out / "overlap.wav", overlay(first, second, 1.5))

    # One voice alone, saying something it never said in enrollment.
    write_wav(out / "single-hazel.wav", first)
    write_wav(out / "single-zira.wav", second)

    # Nothing to hear.
    write_wav(out / "silence.wav", silence(10))
    rng = random.Random(7)  # noqa: S311
    write_wav(out / "quiet-noise.wav", array("h", (clip(rng.gauss(0, 40)) for _ in range(RATE * 10))))

    # Files that must be refused.
    (out / "bad-empty.wav").write_bytes(b"")
    (out / "bad-not-audio.wav").write_bytes(b"This is not a recording. " * 200)
    good = (out / "single-hazel.wav").read_bytes()
    (out / "bad-truncated.wav").write_bytes(good[: len(good) // 2])  # header promises more than is there
    write_wav(out / "bad-no-frames.wav", array("h"))

    manifest = {
        "synthetic": True,
        "voices": "Windows text-to-speech voices; not real people",
        "sampleRate": RATE,
        "enrollment": {
            name: [str(Path("voices") / name / f"enroll-{n}.wav") for n in (1, 2, 3)] for name in VOICES
        },
        "conversation": {"file": "conversation.wav", "turns": pieces},
        "files": {
            "conversation-noisy.wav": "conversation.wav with noise at about 10 dB signal-to-noise",
            "overlap.wav": f"{CONVERSATION[0][0]} and {CONVERSATION[1][0]} overlapping from 1.5 s",
            "single-hazel.wav": CONVERSATION[0][1],
            "single-zira.wav": CONVERSATION[1][1],
            "short.wav": SHORT[1],
            "silence.wav": "ten seconds of digital silence",
            "quiet-noise.wav": "ten seconds of very quiet noise",
        },
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    for file in sorted(out.rglob("*.wav")):
        if "_utterances" not in file.parts:
            print(f"{file.relative_to(out)}  {file.stat().st_size:>9} bytes")
    print(f"\nWrote synthetic test audio under {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
