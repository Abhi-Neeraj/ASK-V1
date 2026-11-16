"""The parts of the real-model adapters that do not need a model: reading audio, explaining failures,
warming models up at start-up, and running a file by hand. (Tests that run the real models are in
tests/real and are skipped unless ASK_REAL_MODELS=1.)"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from app import cli
from app.ai.local import _explain, read_pcm16_mono, slice_spans
from app.ai.registry import warm_up
from app.ai.types import PermanentError, Provider, ProviderUnavailableError
from app.config import Settings
from app.errors import ApiError
from app.services import offline
from tests.ai_doubles import (
    FakeDiarizer,
    FakeEmbedder,
    FakeTranscriber,
    make_provider,
    two_speaker_turns,
)
from tests.helpers import make_wav


def write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


class TestReadingAudio:
    def test_stereo_is_averaged_to_mono(self, tmp_path: Path) -> None:
        path = tmp_path / "stereo.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(
                b"".join(
                    left.to_bytes(2, "little", signed=True) + right.to_bytes(2, "little", signed=True)
                    for left, right in [(1000, 3000), (-200, 200)]
                )
            )
        samples, rate = read_pcm16_mono(path)
        assert rate == 16000 and list(samples) == [2000, 0]

    def test_a_file_that_is_not_a_wav_is_a_permanent_failure(self, tmp_path: Path) -> None:
        with pytest.raises(PermanentError):
            read_pcm16_mono(write(tmp_path / "x.wav", b"not audio at all"))

    def test_eight_bit_audio_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PermanentError, match="16-bit"):
            read_pcm16_mono(write(tmp_path / "8.wav", make_wav(0.1, bits=8)))

    def test_a_half_written_final_sample_is_ignored(self, tmp_path: Path) -> None:
        good = make_wav(0.1)
        path = write(tmp_path / "odd.wav", good + b"\x01")
        samples, _ = read_pcm16_mono(path)  # the wave module reads what the header promises
        assert len(samples) == 1600

    def test_spans_are_cut_in_milliseconds_and_joined(self) -> None:
        samples = list(range(16000))  # one second at 16 kHz
        assert slice_spans(samples, 16000, [(0, 1), (500, 501)]) == samples[0:16] + samples[8000:8016]
        assert slice_spans(samples, 16000, None) == samples
        assert slice_spans(samples, 16000, [(2000, 3000)]) == []  # past the end


class TestExplainingFailures:
    def test_lack_of_memory_says_so_and_how_to_cure_it(self) -> None:
        error = _explain("speech recognition", "small", MemoryError())
        assert isinstance(error, ProviderUnavailableError) and "not enough memory" in str(error)
        assert "ASK_AI_ASR_MODEL" in str(error)
        assert "not enough memory" in str(
            _explain("speaker matching", "m", RuntimeError("DefaultCPUAllocator: not enough memory"))
        )

    def test_the_message_a_real_memory_cap_produced_is_recognised(self) -> None:
        # Seen when the real worker was run under a hard memory limit (a Windows job object).
        error = _explain(
            "speech recognition", "tiny.en", RuntimeError("mkl_malloc: failed to allocate memory")
        )
        assert "not enough memory" in str(error)

    def test_a_full_disk_is_named(self) -> None:
        assert "disk is full" in str(
            _explain("speech recognition", "small", OSError(28, "No space left on device"))
        )

    def test_missing_weights_point_at_the_offline_and_model_dir_settings(self) -> None:
        message = str(
            _explain(
                "speech recognition", "small", ConnectionError("Max retries exceeded: connection refused")
            )
        )
        assert "not on this machine" in message and "ASK_AI_OFFLINE" in message
        gated = str(
            _explain("speaker separation", "pyannote/x", RuntimeError("403 Client Error: gated repo"))
        )
        assert "token" in gated and "terms" in gated

    def test_an_unusable_gpu_says_to_use_the_cpu(self) -> None:
        assert "cpu" in str(
            _explain("speech recognition", "small", RuntimeError("CUDA driver version is insufficient"))
        )

    def test_anything_else_names_the_error_type_but_not_its_text(self) -> None:
        message = str(
            _explain("speech recognition", "small", ValueError(r"secret path C:\Users\someone\audio.wav"))
        )
        assert "ValueError" in message and "someone" not in message


PER_MS = 16  # samples per millisecond at 16 kHz


class TestTurnsFromWindows:
    """The pure logic that turns labelled windows of speech into speaker turns (no model needed)."""

    def turns(self, windows, labels):  # type: ignore[no-untyped-def]
        from app.ai.local import _turns_from_windows

        return [(t.start_ms, t.end_ms, t.speaker) for t in _turns_from_windows(windows, labels)]

    def test_no_speech_means_no_turns(self) -> None:
        assert self.turns([], []) == []

    def test_windows_of_one_voice_in_one_stretch_make_one_turn(self) -> None:
        windows = [(0, 24000, 0), (12000, 36000, 0), (24000, 48000, 0)]
        assert self.turns(windows, [0, 0, 0]) == [(0, 3000, "SPEAKER_00")]

    def test_where_the_voice_changes_the_turns_meet_in_the_middle_of_the_overlap(self) -> None:
        windows = [(0, 24000, 0), (12000, 36000, 0), (24000, 48000, 0), (36000, 60000, 0)]
        # Windows 0 and 1 are one voice, 2 and 3 another. Window 1 ends at 36000, window 2 starts at 24000.
        assert self.turns(windows, [0, 0, 1, 1]) == [(0, 1875, "SPEAKER_00"), (1875, 3750, "SPEAKER_01")]

    def test_a_pause_ends_a_turn_even_if_the_same_voice_goes_on(self) -> None:
        windows = [(0, 24000, 0), (80000, 104000, 1)]  # two stretches of speech with a long pause between
        assert self.turns(windows, [0, 0]) == [(0, 1500, "SPEAKER_00"), (5000, 6500, "SPEAKER_00")]

    def test_a_short_wobble_between_two_stretches_of_one_voice_is_absorbed(self) -> None:
        windows = [(0, 32000, 0), (30000, 40000, 0), (38000, 70000, 0)]
        assert self.turns(windows, [0, 1, 0]) == [(0, 4375, "SPEAKER_00")]

    def test_a_real_change_of_speaker_that_lasts_is_kept(self) -> None:
        windows = [(0, 32000, 0), (30000, 70000, 0), (68000, 100000, 0)]
        found = self.turns(windows, [0, 1, 0])
        assert [speaker for _, _, speaker in found] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"]

    def test_labels_are_grouped_in_order_of_first_appearance(self, settings: Settings) -> None:
        pytest.importorskip("scipy")
        from app.ai.local import ClusteringDiarizer

        diarizer = ClusteringDiarizer(settings, None)  # type: ignore[arg-type]  # no model is used for grouping
        near_x, near_y = (1.0, 0.0, 0.1), (0.0, 1.0, 0.1)
        labels = diarizer._labels([near_y, near_y, near_x, near_y, near_x])
        assert labels == [0, 0, 1, 0, 1]  # the first voice heard is 0, whichever direction it points

    def test_a_looser_threshold_merges_voices_a_strict_one_keeps_apart(self, settings: Settings) -> None:
        pytest.importorskip("scipy")
        from app.ai.local import ClusteringDiarizer

        apart = [(1.0, 0.0), (0.8, 0.6)]  # cosine distance 0.2
        strict = ClusteringDiarizer(settings.model_copy(update={"diarization_cluster_distance": 0.1}), None)  # type: ignore[arg-type]
        loose = ClusteringDiarizer(settings.model_copy(update={"diarization_cluster_distance": 0.5}), None)  # type: ignore[arg-type]
        assert strict._labels(apart) == [0, 1]
        assert loose._labels(apart) == [0, 0]

    def test_one_window_is_one_speaker(self, settings: Settings) -> None:
        from app.ai.local import ClusteringDiarizer

        assert ClusteringDiarizer(settings, None)._labels([(1.0, 0.0)]) == [0]  # type: ignore[arg-type]
        assert ClusteringDiarizer(settings, None)._labels([]) == []  # type: ignore[arg-type]


class _Loadable:
    """A model with a `load` step, as the real adapters have."""

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.loaded = 0

    def load(self) -> None:
        if self.error:
            raise ProviderUnavailableError(self.error)
        self.loaded += 1


class _LoadableTranscriber(FakeTranscriber, _Loadable):
    def __init__(self, error: str | None = None) -> None:
        FakeTranscriber.__init__(self)
        _Loadable.__init__(self, error)


class _LoadableDiarizer(FakeDiarizer, _Loadable):
    def __init__(self, error: str | None = None) -> None:
        FakeDiarizer.__init__(self, two_speaker_turns())
        _Loadable.__init__(self, error)


class _LoadableEmbedder(FakeEmbedder, _Loadable):
    def __init__(self, error: str | None = None) -> None:
        FakeEmbedder.__init__(self, {0: (1.0, 0.0)})
        _Loadable.__init__(self, error)


def provider_of(t: object, d: object | None = None, e: object | None = None) -> Provider:
    return Provider(name="test", placeholder=False, transcriber=t, diarizer=d, embedder=e)  # type: ignore[arg-type]


class TestWarmingUp:
    def test_every_model_is_loaded_once_before_the_first_recording(self) -> None:
        t, d, e = _LoadableTranscriber(), _LoadableDiarizer(), _LoadableEmbedder()
        messages: list[str] = []
        result = warm_up(provider_of(t, d, e), messages.append)
        assert (t.loaded, d.loaded, e.loaded) == (1, 1, 1)
        assert result.diarizer is d and result.embedder is e
        assert len(messages) == 3 and all("ready in" in m for m in messages)

    def test_a_transcriber_that_will_not_load_stops_the_worker_with_the_reason(self) -> None:
        with pytest.raises(ProviderUnavailableError, match="no weights"):
            warm_up(provider_of(_LoadableTranscriber("no weights")))

    def test_speaker_separation_that_will_not_load_is_dropped_and_explained(self) -> None:
        result = warm_up(
            provider_of(_LoadableTranscriber(), _LoadableDiarizer("terms not accepted"), _LoadableEmbedder())
        )
        assert result.diarizer is None and result.embedder is not None
        assert result.capabilities.diarization is False and result.capabilities.speaker_matching is True
        assert any("terms not accepted" in n and "one speaker" in n for n in result.notes)

    def test_speaker_matching_that_will_not_load_is_dropped_and_explained(self) -> None:
        result = warm_up(provider_of(_LoadableTranscriber(), None, _LoadableEmbedder("out of memory")))
        assert result.embedder is None
        assert any("out of memory" in n and "Nobody is identified" in n for n in result.notes)

    def test_models_without_a_load_step_are_left_alone(self) -> None:
        provider = make_provider()
        assert warm_up(provider) == provider

    def test_the_worker_process_exits_with_the_reason_when_speech_recognition_will_not_load(
        self, settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        from app import worker

        broken = provider_of(_LoadableTranscriber("the weights are missing and downloads are off"))
        monkeypatch.setattr(worker, "get_settings", lambda: settings)
        monkeypatch.setattr(worker, "build_provider", lambda _settings: broken)
        assert worker.main(["--once"]) == 2
        assert "cannot start" in caplog.text and "weights are missing" in caplog.text


class TestProcessingAFileByHand:
    def settings(self, settings: Settings) -> Settings:
        return settings.model_copy(update={"speaker_match_threshold": 0.5, "speaker_min_speech_seconds": 1})

    def test_the_stages_run_in_order_and_each_is_timed(self, settings: Settings, tmp_path: Path) -> None:
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        provider = make_provider(diarizer=FakeDiarizer(two_speaker_turns()))
        result = offline.process_file(provider, self.settings(settings), audio)
        assert [s.name for s in result.stages] == [
            "validating",
            "transcribing",
            "separating speakers",
            "aligning",
        ]
        assert result.audio_seconds == pytest.approx(9.0)
        assert [line.speaker for line in result.lines] == ["SPEAKER_00", "SPEAKER_01"]
        assert all(line.person is None for line in result.lines)  # nobody was enrolled, so nobody is named
        assert result.total_seconds >= 0

    def test_without_speaker_separation_it_says_so(self, settings: Settings, tmp_path: Path) -> None:
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        result = offline.process_file(make_provider(), self.settings(settings), audio)
        assert any("not separated" in note for note in result.notes)

    def test_a_voice_is_named_only_when_it_matches_an_enrolled_sample(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        voices = tmp_path / "voices"
        (voices / "Priya").mkdir(parents=True)
        write(voices / "Priya" / "one.wav", make_wav(1.0))
        # The embedder picks a voice print by where the speech starts; the enrollment sample has none ->
        # key 0, which is the first speaker's start. So SPEAKER_00 matches Priya and SPEAKER_01 does not.
        embedder = FakeEmbedder({0: (1.0, 0.0), 3500: (0.0, 1.0)})
        provider = make_provider(diarizer=FakeDiarizer(two_speaker_turns()), embedder=embedder)
        result = offline.process_file(provider, self.settings(settings), audio, voices)
        assert [line.person for line in result.lines] == ["Priya", None]
        assert result.lines[0].score == pytest.approx(1.0)
        assert "enrolling samples" in " ".join(s.name for s in result.stages)

    def test_a_file_that_is_not_audio_is_refused_before_any_model_runs(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        transcriber = FakeTranscriber()
        with pytest.raises(ApiError) as info:
            offline.process_file(
                make_provider(transcriber=transcriber), settings, write(tmp_path / "b.wav", b"junk")
            )
        assert info.value.code == "invalid_audio" and transcriber.calls == []

    def test_the_text_output_shows_unknown_speakers_and_the_identity_warning(
        self, settings: Settings, tmp_path: Path
    ) -> None:
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        result = offline.process_file(
            make_provider(diarizer=FakeDiarizer(two_speaker_turns())), self.settings(settings), audio
        )
        text = offline.render(result)
        assert "Unknown speaker" in text and "not proof of identity" in text
        assert "00:00.500-00:02.200" in text


class TestProcessFileCommand:
    def test_the_stub_is_labelled_as_a_placeholder_and_names_nobody(
        self, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        code = cli.process_file(settings.model_copy(update={"ai_provider": "stub"}), audio, None, False)
        out = capsys.readouterr().out
        assert code == 0 and "PLACEHOLDER" in out and "not a real transcript" in out

    def test_json_output_carries_the_placeholder_flag(
        self, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        assert cli.process_file(settings.model_copy(update={"ai_provider": "stub"}), audio, None, True) == 0
        data = json.loads(capsys.readouterr().out)
        assert data["placeholder"] is True and data["provider"] == "stub"
        assert all(line["person"] is None for line in data["lines"])

    def test_a_corrupt_file_is_refused_with_the_reason(
        self, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = cli.process_file(
            settings.model_copy(update={"ai_provider": "stub"}),
            write(tmp_path / "x.wav", b"junk"),
            None,
            False,
        )
        assert code == 1 and "Refused" in capsys.readouterr().err

    def test_a_missing_file_and_a_missing_provider_are_reported(
        self, settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert cli.process_file(settings, tmp_path / "nope.wav", None, False) == 1
        audio = write(tmp_path / "clip.wav", make_wav(9.0))
        assert cli.process_file(settings, audio, None, False) == 1  # provider "none"
        assert "ASK_AI_PROVIDER" in capsys.readouterr().err
