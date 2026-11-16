"""Google Cloud Speech-to-Text for the transcript and Gemini for the summary (`ASK_AI_PROVIDER=google`).

Audio leaves this server: it is sent to Google. The libraries (`pip install -e ".[google]"`) are imported only
when a client is built, so the rest of the application, and the tests, never need them.

Speech-to-Text's synchronous `recognize` accepts at most one minute of audio, so recordings are sent in
pieces of up to 55 seconds and the results are put back together with each piece's offset. Google labels
speakers within one request; the labels of different 55-second pieces are not linked to each other, so
"Speaker 1" in one minute need not be "Speaker 1" in the next. Tested here only with stand-in clients.
"""

from __future__ import annotations

from array import array
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.ai.local import read_pcm16_mono
from app.ai.types import (
    AsrSegment,
    Diarization,
    Provider,
    ProviderUnavailableError,
    Transcription,
    Turn,
    Word,
)
from app.config import Settings

CHUNK_SECONDS = 55
SPEECH_TIMEOUT_SECONDS = 120.0
GEMINI_TIMEOUT_MS = 60_000
SPEECH_MODEL = "google-speech-to-text"
SPEECH_VERSION = "v1"
SUMMARY_PROMPT = (
    "Summarise this meeting transcript in plain text with four headed sections: Summary, Decisions, "
    "Action items (with owner when stated), Open questions. Use only what the transcript says. If a section "
    "has nothing, write 'None'.\n\nTranscript:\n"
)


def _ms(value: Any) -> int:
    """Google times arrive as timedelta (proto-plus converts Duration); be lenient about None."""
    if value is None:
        return 0
    if isinstance(value, timedelta):
        return int(value.total_seconds() * 1000)
    return int(float(value) * 1000)


class GoogleSpeech:
    """Speech-to-Text v1. Keeps the speaker turns of the last file it transcribed for `GoogleDiarizer`."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client
        self._turns: dict[Path, tuple[Turn, ...]] = {}

    def load(self) -> Any:
        if self._client is None:
            path = self._settings.google_application_credentials
            api_key = self._settings.google_stt_api_key
            if path is None and api_key is None:
                raise ProviderUnavailableError(
                    "Set ASK_GOOGLE_APPLICATION_CREDENTIALS (a service-account file) "
                    "or ASK_GOOGLE_STT_API_KEY."
                )
            try:
                from google.cloud import speech
            except ImportError as error:
                raise ProviderUnavailableError(
                    'Google Speech-to-Text is not installed. Run: pip install -e ".[google]"'
                ) from error
            if path is not None:
                self._client = speech.SpeechClient.from_service_account_file(str(path))
            elif api_key is not None:
                self._client = speech.SpeechClient(client_options={"api_key": api_key.get_secret_value()})
        return self._client

    def _config(self, rate: int) -> dict[str, Any]:
        return {
            "encoding": "LINEAR16",
            "sample_rate_hertz": rate,
            "language_code": self._settings.google_stt_language,
            "enable_automatic_punctuation": True,
            "enable_word_time_offsets": True,
            "diarization_config": {
                "enable_speaker_diarization": True,
                "min_speaker_count": 1,
                "max_speaker_count": 6,
            },
        }

    def transcribe(self, audio: Path) -> Transcription:
        client = self.load()
        samples, rate = read_pcm16_mono(audio)
        step = CHUNK_SECONDS * rate
        segments: list[AsrSegment] = []
        turns: list[Turn] = []
        for start in range(0, len(samples), step):
            piece = array("h", samples[start : start + step])
            offset_ms = start * 1000 // rate
            response = client.recognize(
                request={"config": self._config(rate), "audio": {"content": piece.tobytes()}},
                timeout=SPEECH_TIMEOUT_SECONDS,
            )
            new_segments, new_turns = self._read(response, offset_ms)
            segments.extend(new_segments)
            turns.extend(new_turns)
        self._turns[audio] = tuple(turns)
        return Transcription(
            tuple(segments),
            model=SPEECH_MODEL,
            model_version=SPEECH_VERSION,
            language=self._settings.google_stt_language,
        )

    @staticmethod
    def _read(response: Any, offset_ms: int) -> tuple[list[AsrSegment], list[Turn]]:
        segments: list[AsrSegment] = []
        results = [r for r in (getattr(response, "results", []) or []) if r.alternatives]

        def is_tagged(result: Any) -> bool:
            return any(getattr(w, "speaker_tag", 0) for w in (result.alternatives[0].words or []))

        tagged: list[Any] = []
        if results and is_tagged(results[-1]):
            # With diarization on, v1 ends with one extra result repeating every word with its speaker (and
            # sometimes the whole text). Use it for the speakers only, never as another line of transcript.
            tagged = list(results[-1].alternatives[0].words)
            if len(results) > 1:
                results = results[:-1]
        previous_end = offset_ms
        for result in results:
            best = result.alternatives[0]
            words = list(getattr(best, "words", []) or [])
            text = (best.transcript or "").strip()
            if not text:
                continue
            out_words = tuple(
                Word(_ms(w.start_time) + offset_ms, _ms(w.end_time) + offset_ms, " " + w.word) for w in words
            )
            start = out_words[0].start_ms if out_words else previous_end
            end = max(_ms(getattr(result, "result_end_time", None)) + offset_ms, start)
            if out_words:
                end = max(end, out_words[-1].end_ms)
            confidence = getattr(best, "confidence", None)
            segments.append(
                AsrSegment(start, end, text, out_words, float(confidence) if confidence else None)
            )
            previous_end = end
        turns: list[Turn] = []
        for w in tagged:
            tag = getattr(w, "speaker_tag", 0)
            if not tag:
                continue
            start, end, label = _ms(w.start_time) + offset_ms, _ms(w.end_time) + offset_ms, f"SPEAKER_{tag}"
            if turns and turns[-1].speaker == label:
                turns[-1] = Turn(turns[-1].start_ms, end, label)
            else:
                turns.append(Turn(start, end, label))
        return segments, turns


class GoogleDiarizer:
    """Who spoke when, as Google labelled it while transcribing the same file: no second request."""

    model = SPEECH_MODEL

    def __init__(self, speech: GoogleSpeech) -> None:
        self._speech = speech

    def load(self) -> None:
        return None

    def diarize(self, audio: Path) -> Diarization:
        return Diarization(
            self._speech._turns.get(audio, ()), model=SPEECH_MODEL, model_version=SPEECH_VERSION
        )


class GeminiSummarizer:
    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def model(self) -> str:
        return self._settings.gemini_model

    def load(self) -> Any:
        if self._client is None:
            key = self._settings.gemini_api_key
            if key is None:
                raise ProviderUnavailableError("Set ASK_GEMINI_API_KEY for summaries.")
            try:
                from google import genai
            except ImportError as error:
                raise ProviderUnavailableError(
                    'Gemini is not installed. Run: pip install -e ".[google]"'
                ) from error
            self._client = genai.Client(api_key=key.get_secret_value())
        return self._client

    def summarize(self, transcript: str) -> str:
        client = self.load()
        response = client.models.generate_content(
            model=self.model,
            contents=SUMMARY_PROMPT + transcript,
            config={"http_options": {"timeout": GEMINI_TIMEOUT_MS}},
        )
        text = (getattr(response, "text", None) or "").strip()
        if not text:
            raise RuntimeError("Gemini returned no summary.")
        return text


def missing_settings(settings: Settings) -> list[str]:
    missing = []
    if settings.google_application_credentials is None and settings.google_stt_api_key is None:
        missing.append(
            "Speech-to-Text needs ASK_GOOGLE_APPLICATION_CREDENTIALS (a service-account JSON file) "
            "or ASK_GOOGLE_STT_API_KEY."
        )
    if settings.gemini_api_key is None:
        missing.append("Summaries need ASK_GEMINI_API_KEY.")
    return missing


def build_google(settings: Settings) -> Provider:
    missing = missing_settings(settings)
    if settings.google_application_credentials is None and settings.google_stt_api_key is None:
        raise ProviderUnavailableError(" ".join(missing))
    speech = GoogleSpeech(settings)
    summarizer = GeminiSummarizer(settings) if settings.gemini_api_key is not None else None
    notes = (
        *missing,
        "Audio is sent to Google Cloud Speech-to-Text and transcripts to Gemini.",
        "Speaker labels are per 55-second piece: Speaker 1 in one minute need not be Speaker 1 in the next.",
    )
    return Provider(
        name="google",
        placeholder=False,
        transcriber=speech,
        diarizer=GoogleDiarizer(speech),
        embedder=None,
        notes=notes,
        summarizer=summarizer,
    )
