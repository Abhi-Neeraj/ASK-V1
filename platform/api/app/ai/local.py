"""Real models, run on this machine: faster-whisper, pyannote.audio and SpeechBrain.

Every heavy import happens inside a function, so importing this module (and running the API and its
tests) never needs torch. Each capability is separate: a missing token costs speaker separation, a
missing speaker model costs speaker matching, and transcription carries on either way.

Nothing here sends audio anywhere. Weights are downloaded from their model hub on first use (unless
`ASK_AI_OFFLINE` is set, which forbids even that); recordings stay on this machine.

What has actually been run against real models, on which versions and which audio, is recorded in
platform/docs/AI.md and platform/docs/FINAL_COMPLETION_REPORT.md. Read those, not this docstring.
"""

from __future__ import annotations

import importlib.util
import math
import sys
import threading
import wave
from array import array
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.ai.types import (
    AsrSegment,
    Capabilities,
    Diarization,
    Diarizer,
    Embedding,
    PermanentError,
    Provider,
    ProviderUnavailableError,
    Transcription,
    Turn,
    Word,
)
from app.config import Settings

EXPECTED_RATE = 16_000
EXTRA_HINT = 'Install the processing extra: pip install -e ".[ai]" (from platform/api).'


def _importable(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _ms(seconds: float) -> int:
    return max(0, round(seconds * 1000))


def local_capabilities(settings: Settings) -> Capabilities:
    """What would work, from what is installed and configured. Loads nothing."""
    notes: list[str] = []

    transcription = _importable("faster_whisper")
    if not transcription:
        notes.append(f"Transcription is unavailable: faster-whisper is not installed. {EXTRA_HINT}")

    matching = _importable("speechbrain") and _importable("torch") and _importable("numpy")
    if not matching:
        notes.append(
            "Speaker matching is unavailable: speechbrain, torch or numpy is not installed. "
            "Nobody is identified by voice."
        )

    if settings.ai_diarizer == "pitch":
        # Needs only numpy (part of the base install): no model, no token, no download.
        diarization = _importable("numpy")
        if not diarization:
            notes.append("Speaker separation by pitch is unavailable: numpy is not installed.")
        else:
            notes.append(
                "Speaker separation is the EXPERIMENTAL pitch method, not pyannote: it tells voices apart "
                "by pitch first and by the colour of the voice second. Check its turns by listening."
            )
    elif settings.ai_diarizer == "clustering":
        diarization = matching and transcription and _importable("scipy")
        if not diarization:
            notes.append(
                "Speaker separation by clustering is unavailable: it needs faster-whisper, speechbrain, "
                "torch and scipy. Every recording is treated as one speaker."
            )
        else:
            notes.append(
                "Speaker separation is the EXPERIMENTAL clustering method, not pyannote: "
                "check its turns by listening."
            )
    else:
        has_token = settings.hf_token is not None and bool(settings.hf_token.get_secret_value())
        diarization = _importable("pyannote.audio") and has_token
        if not _importable("pyannote.audio"):
            notes.append(
                "Speaker separation is unavailable: pyannote.audio is not installed. "
                "Every recording is treated as one speaker."
            )
        elif not has_token:
            notes.append(
                "Speaker separation is unavailable: no Hugging Face token (ASK_HF_TOKEN). "
                "Every recording is treated as one speaker."
            )

    return Capabilities(
        provider="local",
        placeholder=False,
        transcription=transcription,
        diarization=diarization,
        speaker_matching=matching,
        notes=tuple(notes),
    )


# --- reading audio ------------------------------------------------------------------------------------


def read_pcm16_mono(path: Path) -> tuple[array[int], int]:
    """The samples of a 16-bit WAV as mono (stereo is averaged), and its sample rate.

    Uploads may be mono or stereo, so both must work. Anything that is not 16-bit PCM cannot be used.
    """
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            if handle.getsampwidth() != 2 or channels not in (1, 2):
                raise PermanentError("The speaker model needs 16-bit mono or stereo audio.")
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (wave.Error, EOFError) as error:
        raise PermanentError("The audio file could not be read as a WAV.") from error
    raw: array[int] = array("h")
    raw.frombytes(frames[: len(frames) - len(frames) % 2])
    if sys.byteorder == "big":  # WAV is little-endian
        raw.byteswap()
    if channels == 1:
        return raw, rate
    mixed: array[int] = array(
        "h", ((left + right) // 2 for left, right in zip(raw[0::2], raw[1::2], strict=False))
    )
    return mixed, rate


def slice_spans(samples: Sequence[int], rate: int, spans: Sequence[tuple[int, int]] | None) -> list[int]:
    """The chosen stretches (milliseconds) joined together. No spans means all of it."""
    if not spans:
        return list(samples)
    joined: list[int] = []
    for start_ms, end_ms in spans:
        first = max(0, start_ms * rate // 1000)
        last = min(len(samples), end_ms * rate // 1000)
        if last > first:
            joined.extend(samples[first:last])
    return joined


def _waveform_16k(samples: Sequence[int], rate: int) -> Any:
    """A (1, samples) float tensor at 16 kHz, which is what the speaker models take.

    Decoding here, rather than letting a library open the file, keeps the models independent of
    FFmpeg and of whichever audio backend happens to be installed.
    """
    import numpy
    import torch

    tensor = torch.from_numpy(numpy.asarray(samples, dtype=numpy.float32) / 32768.0).unsqueeze(0)
    if rate == EXPECTED_RATE:
        return tensor
    import torchaudio

    return torchaudio.functional.resample(tensor, rate, EXPECTED_RATE)


# --- explaining failures ------------------------------------------------------------------------------


def _explain(kind: str, name: str, error: BaseException) -> Exception:
    """What an operator needs to know when a model could not load or run, in place of a bare traceback line.

    Missing weights, no memory, a full disk and an unusable device are the usual causes, and each has a
    different cure. Returned (not raised) so the caller chains it to the original with `from`.
    """
    text = f"{type(error).__name__}: {error}".lower()
    if isinstance(error, MemoryError) or any(
        hint in text
        for hint in (
            "out of memory",
            "bad_alloc",
            "not enough memory",
            "defaultcpuallocator",
            "failed to allocate",  # what the math library said under a memory cap: "mkl_malloc: failed ..."
            "cannot allocate",
            "memory allocation",
        )
    ):
        return ProviderUnavailableError(
            f"There is not enough memory to run the {kind} model ({name}). Close other programs, use a "
            "smaller model (ASK_AI_ASR_MODEL), or run it on a machine with more memory."
        )
    if "no space left" in text or getattr(error, "errno", None) == 28:
        return ProviderUnavailableError(
            f"The disk is full, so the {kind} model ({name}) could not be saved or run. Free some space."
        )
    if any(hint in text for hint in ("cuda", "cudnn", "cublas", "no gpu")):
        return ProviderUnavailableError(
            f"The {kind} model ({name}) could not use the chosen device. Set the device to cpu "
            "(ASK_AI_ASR_DEVICE and ASK_AI_TORCH_DEVICE), or fix the GPU driver."
        )
    if any(
        hint in text
        for hint in (
            "offline",
            "localentrynotfound",
            "connection",
            "name resolution",
            "timed out",
            "client error",
            "gated",
            "repository not found",
            "cannot find the requested files",
            "not found in the local cache",
        )
    ):
        return ProviderUnavailableError(
            f"The {kind} model ({name}) is not on this machine and could not be downloaded. Check the "
            "network, the model name, any access token and its terms, or copy the weights into "
            "ASK_AI_MODEL_DIR and set ASK_AI_OFFLINE=true."
        )
    return ProviderUnavailableError(
        f"The {kind} model ({name}) failed ({type(error).__name__}). The server log has the details."
    )


def _is_decode_failure(error: BaseException) -> bool:
    """The audio itself could not be decoded: no retry will change that."""
    module = type(error).__module__
    return module == "av" or module.startswith("av.") or isinstance(error, (wave.Error, EOFError))


# --- transcription ------------------------------------------------------------------------------------


class FasterWhisperTranscriber:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._model: Any = None
        self._lock = threading.Lock()

    def load(self) -> Any:
        """Load the weights (downloading them first if allowed). Called once; a worker does it at start-up
        so that the first recording does not pay for it, and a missing model is found before a customer is."""
        with self._lock:
            if self._model is None:
                s = self._settings
                try:
                    from faster_whisper import WhisperModel  # heavy: imported on first use

                    self._model = WhisperModel(
                        s.ai_asr_model,
                        device=s.ai_asr_device,
                        compute_type=s.ai_asr_compute_type,
                        cpu_threads=s.ai_cpu_threads,
                        download_root=str(s.ai_model_dir) if s.ai_model_dir else None,
                        local_files_only=s.ai_offline,
                    )
                except Exception as error:
                    raise _explain("speech recognition", s.ai_asr_model, error) from error
            return self._model

    def transcribe(self, audio: Path) -> Transcription:
        import faster_whisper

        model = self.load()
        s = self._settings
        try:
            raw_segments, info = model.transcribe(
                str(audio),
                language=s.ai_asr_language,
                beam_size=s.ai_asr_beam_size,
                word_timestamps=True,
                vad_filter=True,  # skip silence rather than inventing words for it
            )
            segments = [self._segment(raw) for raw in raw_segments]  # a generator: the decoding happens here
        except Exception as error:
            if _is_decode_failure(error):
                raise PermanentError(
                    "The audio could not be decoded, so it cannot be transcribed."
                ) from error
            raise _explain("speech recognition", s.ai_asr_model, error) from error
        return Transcription(
            segments=tuple(seg for seg in segments if seg is not None),
            model=f"faster-whisper {s.ai_asr_model}"[:80],
            model_version=str(faster_whisper.__version__)[:80],
            language=getattr(info, "language", None),
        )

    @staticmethod
    def _segment(raw: Any) -> AsrSegment | None:
        text = str(raw.text).strip()
        if not text:
            return None
        words = tuple(
            Word(
                start_ms=_ms(word.start),
                end_ms=_ms(word.end),
                text=str(word.word),
                probability=float(word.probability) if word.probability is not None else None,
            )
            for word in (raw.words or ())
        )
        return AsrSegment(
            start_ms=_ms(raw.start),
            end_ms=max(_ms(raw.end), _ms(raw.start)),
            text=text,
            words=words,
            confidence=_confidence(words, getattr(raw, "avg_logprob", None)),
        )


def _confidence(words: Sequence[Word], avg_logprob: object) -> float | None:
    """How sure the model was: the mean word probability, or failing that exp(average log-probability)."""
    probabilities = [w.probability for w in words if w.probability is not None]
    if probabilities:
        return max(0.0, min(1.0, sum(probabilities) / len(probabilities)))
    if isinstance(avg_logprob, (int, float)):
        return max(0.0, min(1.0, math.exp(avg_logprob)))
    return None


# --- shared torch settings ------------------------------------------------------------------------------


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def _torch_device(settings: Settings) -> str:
    """cpu or cuda, as configured. `auto` uses a GPU when there is one."""
    if settings.ai_torch_device == "cuda" and not _cuda_available():
        raise ProviderUnavailableError("ASK_AI_TORCH_DEVICE=cuda was chosen but no usable GPU was found.")
    if settings.ai_torch_device == "auto":
        return "cuda" if _cuda_available() else "cpu"
    return settings.ai_torch_device


def _limit_threads(settings: Settings) -> None:
    if settings.ai_cpu_threads:
        import torch

        torch.set_num_threads(settings.ai_cpu_threads)


# --- speaker separation -----------------------------------------------------------------------------------


class PyannoteDiarizer:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pipeline: Any = None
        self._lock = threading.Lock()

    def load(self) -> Any:
        with self._lock:
            if self._pipeline is None:
                s = self._settings
                try:
                    import torch
                    from pyannote.audio import Pipeline

                    _limit_threads(s)
                    token = s.hf_token.get_secret_value() if s.hf_token else None
                    cache = str(s.ai_model_dir / "huggingface") if s.ai_model_dir else None
                    if s.ai_offline:  # the hub client reads this when it is first used
                        import os

                        os.environ.setdefault("HF_HUB_OFFLINE", "1")
                    try:
                        pipeline = Pipeline.from_pretrained(
                            s.ai_diarization_model, token=token, cache_dir=cache
                        )
                    except TypeError:  # older releases call the argument `use_auth_token`
                        older: dict[str, Any] = {"use_auth_token": token, "cache_dir": cache}
                        pipeline = Pipeline.from_pretrained(s.ai_diarization_model, **older)
                    if pipeline is None:
                        raise ProviderUnavailableError(
                            f"Could not load {s.ai_diarization_model}. Check the Hugging Face token and that "
                            "you accepted the model's terms on its page."
                        )
                    pipeline.to(torch.device(_torch_device(s)))
                    self._pipeline = pipeline
                except ProviderUnavailableError:
                    raise
                except Exception as error:
                    raise _explain("speaker separation", s.ai_diarization_model, error) from error
            return self._pipeline

    def diarize(self, audio: Path) -> Diarization:
        import pyannote.audio

        pipeline = self.load()
        s = self._settings
        samples, rate = read_pcm16_mono(audio)
        try:
            # The audio is handed over already decoded, so the library never needs FFmpeg to open it.
            result = pipeline({"waveform": _waveform_16k(samples, rate), "sample_rate": EXPECTED_RATE})
            # pyannote 3 returns an Annotation; later releases wrap it in an object that has
            # `speaker_diarization`.
            annotation = getattr(result, "speaker_diarization", result)
            turns = sorted(
                (
                    Turn(start_ms=_ms(segment.start), end_ms=_ms(segment.end), speaker=str(label))
                    for segment, _track, label in annotation.itertracks(yield_label=True)
                ),
                key=lambda turn: (turn.start_ms, turn.end_ms),
            )
        except PermanentError:
            raise
        except Exception as error:
            raise _explain("speaker separation", s.ai_diarization_model, error) from error
        return Diarization(
            turns=tuple(turns),
            model=s.ai_diarization_model[:80],
            model_version=str(pyannote.audio.__version__)[:80],
        )


# --- speaker matching -------------------------------------------------------------------------------------


class SpeechBrainEmbedder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._classifier: Any = None
        self._lock = threading.Lock()

    @property
    def model(self) -> str:
        return self._settings.ai_speaker_model[:80]

    def load(self) -> Any:
        with self._lock:
            if self._classifier is None:
                s = self._settings
                try:
                    _limit_threads(s)
                    try:
                        from speechbrain.inference.speaker import EncoderClassifier
                    except ImportError:  # releases before 1.0
                        from speechbrain.pretrained import EncoderClassifier

                    if s.ai_offline:
                        import os

                        os.environ.setdefault("HF_HUB_OFFLINE", "1")
                    folder = (
                        (s.ai_model_dir / "speechbrain" / s.ai_speaker_model.replace("/", "_"))
                        if s.ai_model_dir
                        else Path.home()
                        / ".cache"
                        / "ask"
                        / "speechbrain"
                        / s.ai_speaker_model.replace("/", "_")
                    )
                    options: dict[str, Any] = {}
                    if sys.platform == "win32":
                        # The default links files into place, which Windows only allows with elevated rights.
                        try:
                            from speechbrain.utils.fetching import LocalStrategy

                            options["local_strategy"] = LocalStrategy.COPY
                        except ImportError:  # releases before this option existed copy anyway
                            pass
                    self._classifier = EncoderClassifier.from_hparams(
                        source=s.ai_speaker_model,
                        savedir=str(folder),
                        run_opts={"device": _torch_device(s)},
                        **options,
                    )
                except ProviderUnavailableError:
                    raise
                except Exception as error:
                    raise _explain("speaker matching", s.ai_speaker_model, error) from error
            return self._classifier

    def encode(self, signal: Any) -> tuple[float, ...]:
        """The voice print of a (1, samples) 16 kHz tensor."""
        import numpy
        import torch

        classifier = self.load()
        try:
            with torch.no_grad():
                vector = classifier.encode_batch(signal).squeeze().cpu().numpy()
        except Exception as error:
            raise _explain("speaker matching", self._settings.ai_speaker_model, error) from error
        if not numpy.isfinite(vector).all():
            raise PermanentError("The speaker model could not make a voice print from this audio.")
        return tuple(float(x) for x in vector)

    def encode_many(self, signals: Sequence[Any], batch_size: int = 16) -> list[tuple[float, ...]]:
        """Voice prints of many (1, samples) tensors, in order. Equal-length ones are run together, which is
        several times faster than one at a time and gives the same numbers."""
        import numpy
        import torch

        classifier = self.load()
        vectors: dict[int, tuple[float, ...]] = {}
        by_length: dict[int, list[int]] = {}
        for index, signal in enumerate(signals):
            by_length.setdefault(int(signal.shape[-1]), []).append(index)
        try:
            with torch.no_grad():
                for indices in by_length.values():
                    for first in range(0, len(indices), batch_size):
                        chosen = indices[first : first + batch_size]
                        batch = torch.cat([signals[i] for i in chosen], dim=0)  # (batch, samples)
                        out = classifier.encode_batch(batch).reshape(len(chosen), -1).cpu().numpy()
                        for i, vector in zip(chosen, out, strict=True):
                            if not numpy.isfinite(vector).all():
                                raise PermanentError("The speaker model could not make a voice print.")
                            vectors[i] = tuple(float(x) for x in vector)
        except PermanentError:
            raise
        except Exception as error:
            raise _explain("speaker matching", self._settings.ai_speaker_model, error) from error
        return [vectors[i] for i in range(len(signals))]

    def embed(self, audio: Path, spans: Sequence[tuple[int, int]] | None = None) -> Embedding:
        import speechbrain

        samples, rate = read_pcm16_mono(audio)
        chosen = slice_spans(samples, rate, spans)
        if not chosen:
            raise PermanentError("There is no audio in the requested stretch.")
        vector = self.encode(_waveform_16k(chosen, rate))
        return Embedding(vector=vector, model=self.model, model_version=str(speechbrain.__version__)[:80])


# --- speaker separation without a gated model (experimental) ------------------------------------------------

WINDOW_MS = 1500  # how much speech one voice print is taken from
HOP_MS = 750
MIN_WINDOW_MS = 600  # a stretch shorter than this is too little to hear a voice in
MIN_TURN_MS = 700  # a lone shorter turn between two of the same voice is a wobble, not a speaker


class ClusteringDiarizer:
    """Who spoke when, by sound alone: find the speech, take a voice print of each short window of it with the
    speaker model, and group windows that sound alike.

    EXPERIMENTAL. This exists so speaker separation can run without the gated pyannote model and its token. It
    does not handle two people talking at once, it needs a window or two of a voice to notice it, and its
    grouping threshold (`ASK_DIARIZATION_CLUSTER_DISTANCE`) was only tried on synthetic voices. It labels
    its output as experimental and the transcript says which separator produced it. Prefer pyannote where
    you can have it, and check any result by listening.
    """

    def __init__(self, settings: Settings, embedder: SpeechBrainEmbedder) -> None:
        self._settings = settings
        self._embedder = embedder

    @property
    def model(self) -> str:
        return "speechbrain ecapa clustering (experimental)"

    def load(self) -> None:
        self._embedder.load()

    def diarize(self, audio: Path) -> Diarization:
        import speechbrain

        samples, rate = read_pcm16_mono(audio)
        wave16 = _waveform_16k(samples, rate)
        windows = self._windows(wave16.squeeze(0).numpy())
        vectors = self._embedder.encode_many([wave16[:, a:b] for a, b, _ in windows])
        turns = _turns_from_windows(windows, self._labels(vectors))
        return Diarization(
            turns=tuple(turns), model=self.model, model_version=str(speechbrain.__version__)[:80]
        )

    @staticmethod
    def _windows(signal: Any) -> list[tuple[int, int, int]]:
        """(start sample, end sample, speech region) of each stretch to take a voice print from."""
        from faster_whisper.vad import VadOptions, get_speech_timestamps

        regions = get_speech_timestamps(
            signal, VadOptions(min_silence_duration_ms=300, speech_pad_ms=100, min_speech_duration_ms=250)
        )
        per_ms = EXPECTED_RATE // 1000
        windows: list[tuple[int, int, int]] = []
        for index, region in enumerate(regions):
            start, end = int(region["start"]), int(region["end"])
            if end - start < MIN_WINDOW_MS * per_ms:
                continue
            if end - start <= WINDOW_MS * per_ms:
                windows.append((start, end, index))
                continue
            position = start
            while position + WINDOW_MS * per_ms < end:
                windows.append((position, position + WINDOW_MS * per_ms, index))
                position += HOP_MS * per_ms
            windows.append(
                (end - WINDOW_MS * per_ms, end, index)
            )  # the last window ends where the speech does
        return windows

    def _labels(self, vectors: list[tuple[float, ...]]) -> list[int]:
        """A group number for each window, in order of first appearance."""
        if len(vectors) < 2:
            return [0] * len(vectors)
        import numpy
        from scipy.cluster.hierarchy import fcluster, linkage

        tree = linkage(numpy.asarray(vectors, dtype=numpy.float64), method="average", metric="cosine")
        raw = fcluster(tree, t=self._settings.diarization_cluster_distance, criterion="distance")
        order: dict[int, int] = {}
        return [order.setdefault(int(label), len(order)) for label in raw]


def _turns_from_windows(windows: Sequence[tuple[int, int, int]], labels: Sequence[int]) -> list[Turn]:
    """Join windows into turns. Where the voice changes inside one stretch of speech, the turn ends halfway
    through the overlap of the two windows, and a lone short wobble between two of the same voice is
    absorbed."""
    per_ms = EXPECTED_RATE // 1000
    runs: list[list[int]] = []  # [start sample, end sample, label]
    for position, ((start, end, region), label) in enumerate(zip(windows, labels, strict=True)):
        if runs and runs[-1][2] == label and windows[position - 1][2] == region:
            runs[-1][1] = end
            continue
        if runs and windows[position - 1][2] == region:
            boundary = (runs[-1][1] + start) // 2  # between two voices in one stretch: meet in the middle
            runs[-1][1] = boundary
            start = boundary
        runs.append([start, end, label])

    merged: list[list[int]] = []
    for index, run in enumerate(runs):
        following = runs[index + 1] if index + 1 < len(runs) else None
        if (
            merged
            and following is not None
            and run[1] - run[0] < MIN_TURN_MS * per_ms
            and merged[-1][2] == following[2]
            and merged[-1][1] >= run[0]  # touching, not separated by a pause
            and run[1] >= following[0]
        ):
            merged[-1][1] = run[1]  # the wobble becomes part of the voice around it
        elif merged and merged[-1][2] == run[2] and merged[-1][1] >= run[0]:
            merged[-1][1] = run[1]
        else:
            merged.append(list(run))
    return [
        Turn(start_ms=a // per_ms, end_ms=b // per_ms, speaker=f"SPEAKER_{label:02d}")
        for a, b, label in merged
    ]


class PitchDiarizer:
    """Who spoke when, by pitch first and voice colour second. EXPERIMENTAL; see app/voiceid/diarize.py.

    Needs no speaker model, no token and no network, only the audio. It separates voices that differ clearly
    in pitch reliably, voices of similar pitch only when their colour differs enough, and not people talking
    at once. It labels its output as experimental, and the transcript says which separator produced it.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def model(self) -> str:
        return "pitch and voice colour (experimental)"

    def load(self) -> None:
        return None  # nothing to load

    def diarize(self, audio: Path) -> Diarization:
        from app.voiceid.audio_io import load_for_analysis
        from app.voiceid.diarize import diarize_samples

        try:
            loaded = load_for_analysis(audio)
        except (wave.Error, EOFError, ValueError) as error:
            raise PermanentError("The audio file could not be read as a WAV.") from error
        found = diarize_samples(
            loaded.samples,
            loaded.rate,
            pitch_gate_octaves=self._settings.diarization_pitch_gate_octaves,
            cluster_distance=self._settings.diarization_pitch_cluster_distance,
        )
        turns = [
            Turn(start_ms=t.start_ms, end_ms=t.end_ms, speaker=f"SPEAKER_{t.speaker:02d}") for t in found
        ]
        return Diarization(turns=tuple(turns), model=self.model, model_version="1")


# --- assembling the provider --------------------------------------------------------------------------


def build_local(settings: Settings) -> Provider:
    capabilities = local_capabilities(settings)
    if not capabilities.transcription:
        raise ProviderUnavailableError(capabilities.notes[0])
    embedder = SpeechBrainEmbedder(settings) if capabilities.speaker_matching else None
    diarizer: Diarizer | None = None
    if capabilities.diarization:
        if settings.ai_diarizer == "pitch":
            diarizer = PitchDiarizer(settings)
        elif settings.ai_diarizer == "clustering" and embedder is not None:
            diarizer = ClusteringDiarizer(settings, embedder)  # shares the one loaded speaker model
        else:
            diarizer = PyannoteDiarizer(settings)
    return Provider(
        name="local",
        placeholder=False,
        transcriber=FasterWhisperTranscriber(settings),
        diarizer=diarizer,
        embedder=embedder,
        notes=capabilities.notes,
    )
