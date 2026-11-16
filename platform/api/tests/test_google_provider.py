"""Google Cloud Speech-to-Text and Gemini, with stand-in clients: nothing here talks to Google."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.ai.google import SUMMARY_PROMPT, GeminiSummarizer, GoogleDiarizer, GoogleSpeech
from app.ai.registry import capabilities
from app.config import Settings
from tests.helpers import make_wav


def word(text: str, start_s: float, end_s: float, tag: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        word=text, start_time=timedelta(seconds=start_s), end_time=timedelta(seconds=end_s), speaker_tag=tag
    )


def result(
    transcript: str, words: list[SimpleNamespace], end_s: float, confidence: float = 0.9
) -> SimpleNamespace:
    return SimpleNamespace(
        alternatives=[SimpleNamespace(transcript=transcript, confidence=confidence, words=words)],
        result_end_time=timedelta(seconds=end_s),
    )


class FakeSpeechClient:
    def __init__(self, responses: list[list[SimpleNamespace]] | None = None) -> None:
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []

    def recognize(self, request: dict[str, Any], timeout: float | None = None) -> SimpleNamespace:
        self.requests.append(request)
        self.timeouts.append(timeout)
        if self.responses is None:  # every chunk says "hello" one second in, by speaker 1
            return SimpleNamespace(results=[result("hello", [word("hello", 1.0, 1.5, 1)], 1.5)])
        return SimpleNamespace(results=self.responses[len(self.requests) - 1])


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key=SecretStr("test-secret-key-0123456789-abcdefghijklmnop"),
        ai_provider="google",
        google_application_credentials=tmp_path / "sa.json",
        gemini_api_key=SecretStr("gemini-test-key-0123456789"),
        gemini_model="gemini-test",
    )


def wav(tmp_path: Path, seconds: float) -> Path:
    path = tmp_path / "clip.wav"
    path.write_bytes(make_wav(seconds))
    return path


def test_long_audio_is_sent_in_55_second_chunks_with_offsets(settings: Settings, tmp_path: Path) -> None:
    client = FakeSpeechClient()
    speech = GoogleSpeech(settings, client=client)
    transcription = speech.transcribe(wav(tmp_path, 130.0))

    assert len(client.requests) == 3
    first = client.requests[0]
    assert first["config"]["encoding"] == "LINEAR16" and first["config"]["sample_rate_hertz"] == 16000
    assert first["config"]["language_code"] == "en-US"
    assert first["config"]["enable_automatic_punctuation"] is True
    assert first["config"]["diarization_config"]["enable_speaker_diarization"] is True
    sizes = [len(r["audio"]["content"]) for r in client.requests]
    assert sizes == [55 * 16000 * 2, 55 * 16000 * 2, 20 * 16000 * 2]
    starts = [s.start_ms for s in transcription.segments]
    assert starts == [1000, 56000, 111000]
    assert transcription.segments[1].words[0].start_ms == 56000
    assert transcription.model == "google-speech-to-text"


def test_speaker_tags_become_turns(settings: Settings, tmp_path: Path) -> None:
    words = [word("Hi", 0.0, 0.5, 1), word("there.", 0.5, 1.0, 1), word("Hello.", 1.2, 2.0, 2)]
    client = FakeSpeechClient(
        [[result("Hi there.", words[:2], 1.0), result("Hello.", words[2:], 2.0), result("", words, 2.0)]]
    )
    speech = GoogleSpeech(settings, client=client)
    path = wav(tmp_path, 3.0)
    transcription = speech.transcribe(path)
    assert [s.text for s in transcription.segments] == ["Hi there.", "Hello."]
    turns = GoogleDiarizer(speech).diarize(path).turns
    assert [(t.start_ms, t.end_ms, t.speaker) for t in turns] == [
        (0, 1000, "SPEAKER_1"),
        (1200, 2000, "SPEAKER_2"),
    ]


def test_silence_gives_an_empty_transcript(settings: Settings, tmp_path: Path) -> None:
    speech = GoogleSpeech(settings, client=FakeSpeechClient([[]]))
    path = wav(tmp_path, 2.0)
    assert speech.transcribe(path).segments == ()
    assert GoogleDiarizer(speech).diarize(path).turns == ()


class FakeModels:
    def __init__(self, text: str | None) -> None:
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, *, model: str, contents: str, config: Any = None) -> SimpleNamespace:
        self.calls.append({"model": model, "contents": contents, "config": config})
        return SimpleNamespace(text=self.text)


def test_summarizer_sends_the_prompt_and_returns_text(settings: Settings) -> None:
    models = FakeModels("Summary\nThey met.\n")
    summarizer = GeminiSummarizer(settings, client=SimpleNamespace(models=models))
    assert summarizer.model == "gemini-test"
    assert summarizer.summarize("[00:00:01] Speaker 1: We ship Friday.") == "Summary\nThey met."
    [call] = models.calls
    assert call["model"] == "gemini-test"
    assert call["contents"] == SUMMARY_PROMPT + "[00:00:01] Speaker 1: We ship Friday."


def test_an_empty_answer_is_an_error(settings: Settings) -> None:
    summarizer = GeminiSummarizer(settings, client=SimpleNamespace(models=FakeModels("  ")))
    with pytest.raises(RuntimeError):
        summarizer.summarize("[00:00:01] Speaker 1: Hi.")


def test_registry_reports_missing_google_settings() -> None:
    bare = Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key=SecretStr("test-secret-key-0123456789-abcdefghijklmnop"),
        ai_provider="google",
    )
    caps = capabilities(bare)
    assert caps.provider == "google" and caps.transcription is False
    notes = " ".join(caps.notes)
    assert "ASK_GOOGLE_APPLICATION_CREDENTIALS" in notes and "ASK_GEMINI_API_KEY" in notes


def test_the_real_v1_shape_is_not_transcribed_twice(settings: Settings, tmp_path: Path) -> None:
    """With diarization on, Speech-to-Text v1 answers with the usual results (words untagged) and then
    one more result repeating every word with its speaker, sometimes with the whole text again."""
    plain = [word("Hi", 0.0, 0.5), word("there.", 0.5, 1.0)], [word("Hello.", 1.2, 2.0)]
    tagged = [word("Hi", 0.0, 0.5, 1), word("there.", 0.5, 1.0, 1), word("Hello.", 1.2, 2.0, 2)]
    responses = [
        [
            result("Hi there.", plain[0], 1.0),
            result("Hello.", plain[1], 2.0),
            result("Hi there. Hello.", tagged, 2.0),
        ]
    ]
    speech = GoogleSpeech(settings, client=FakeSpeechClient(responses))
    path = wav(tmp_path, 3.0)
    assert [s.text for s in speech.transcribe(path).segments] == ["Hi there.", "Hello."]
    assert [t.speaker for t in GoogleDiarizer(speech).diarize(path).turns] == ["SPEAKER_1", "SPEAKER_2"]


def test_requests_have_time_limits(settings: Settings, tmp_path: Path) -> None:
    client = FakeSpeechClient()
    GoogleSpeech(settings, client=client).transcribe(wav(tmp_path, 2.0))
    assert client.timeouts == [120.0]
    models = FakeModels("Summary")
    GeminiSummarizer(settings, client=SimpleNamespace(models=models)).summarize("x")
    assert models.calls[0]["config"] == {"http_options": {"timeout": 60_000}}


def test_the_prompt_asks_for_what_the_design_says() -> None:
    for section in ("Decisions", "Action items", "Open questions"):
        assert section in SUMMARY_PROMPT


def test_an_api_key_is_enough_for_speech_to_text() -> None:
    keyed = Settings(
        _env_file=None,  # type: ignore[call-arg]
        secret_key=SecretStr("test-secret-key-0123456789-abcdefghijklmnop"),
        ai_provider="google",
        google_stt_api_key=SecretStr("stt-test-key-0123456789"),
    )
    caps = capabilities(keyed)
    assert caps.provider == "google" and caps.transcription is True
    # With no service-account file the client is built from the key, and the key never appears in the notes.
    assert "stt-test-key" not in " ".join(caps.notes)
    from app.ai.google import GoogleSpeech

    seen: dict[str, Any] = {}

    class FakeSpeechClient:
        def __init__(self, client_options: dict[str, Any] | None = None) -> None:
            seen["options"] = client_options

    import sys
    from types import ModuleType

    fake_speech = ModuleType("google.cloud.speech")
    fake_speech.SpeechClient = FakeSpeechClient  # type: ignore[attr-defined]
    original = sys.modules.get("google.cloud.speech")
    sys.modules["google.cloud.speech"] = fake_speech
    try:
        import google.cloud as gcloud

        previous = getattr(gcloud, "speech", None)
        gcloud.speech = fake_speech  # type: ignore[attr-defined]
        try:
            GoogleSpeech(keyed).load()
        finally:
            if previous is not None:
                gcloud.speech = previous  # type: ignore[attr-defined]
    finally:
        if original is not None:
            sys.modules["google.cloud.speech"] = original
        else:
            sys.modules.pop("google.cloud.speech", None)
    assert seen["options"] == {"api_key": "stt-test-key-0123456789"}
