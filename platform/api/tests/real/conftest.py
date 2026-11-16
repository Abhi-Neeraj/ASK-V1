"""Tests that run the REAL speech models on real (synthetic-voice) audio.

They are skipped unless you ask for them, because they need the `ai` extra, downloaded weights and sample
audio, and they take minutes:

    python tools/make_sample_audio.py                    # once: writes platform/data/samples/real-audio
    $env:ASK_REAL_MODELS = "1"
    python -m pytest tests/real -q -s

Environment (all optional):
    ASK_SAMPLE_AUDIO_DIR   where the sample audio is (default platform/data/samples/real-audio)
    ASK_AI_MODEL_DIR       where weights are kept (default platform/data/models)
    ASK_AI_ASR_MODEL       the Whisper size to test with (default tiny.en: fast; use small for real accuracy)
    ASK_HF_TOKEN           only for the gated speaker-separation model; tests that need it skip without it

Nothing here is a placeholder: a test that would pass on the development stub is a bug in the test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.ai.registry import build_provider, warm_up
from app.ai.types import Provider
from app.config import Settings

PLATFORM = Path(__file__).resolve().parents[3]
SAMPLES = Path(os.environ.get("ASK_SAMPLE_AUDIO_DIR", PLATFORM / "data" / "samples" / "real-audio"))
MODELS = Path(os.environ.get("ASK_AI_MODEL_DIR", PLATFORM / "data" / "models"))


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("ASK_REAL_MODELS") == "1":
        return
    skip = pytest.mark.skip(reason="real-model tests: set ASK_REAL_MODELS=1 (see tests/real/conftest.py)")
    for item in items:
        if "tests/real" in item.nodeid.replace("\\", "/"):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def samples() -> Path:
    if not (SAMPLES / "manifest.json").is_file():
        pytest.skip(f"no sample audio in {SAMPLES}: run tools/make_sample_audio.py")
    return SAMPLES


def real_settings_from(base: Settings) -> Settings:
    token = os.environ.get("ASK_HF_TOKEN")
    return base.model_copy(
        update={
            "ai_provider": "local",
            "ai_model_dir": MODELS,
            "ai_asr_model": os.environ.get("ASK_AI_ASR_MODEL", "tiny.en"),
            "hf_token": SecretStr(token) if token else None,
            # pyannote when there is a token for it; otherwise the experimental clustering separator, so
            # speaker separation is exercised either way. Say ASK_AI_DIARIZER=pyannote to insist on pyannote.
            "ai_diarizer": os.environ.get("ASK_AI_DIARIZER", "pyannote" if token else "clustering"),
            "job_retry_base_seconds": 0.0,
        }
    )


@pytest.fixture(scope="session")
def real_provider() -> Provider:
    """The real models, loaded once for the whole run (as a worker loads them once at start-up)."""
    base = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        secret_key=SecretStr("test-secret-key-0123456789-abcdefghijklmnop"),
        database_url="sqlite://",
    )
    provider = warm_up(build_provider(real_settings_from(base)), print)
    assert provider.placeholder is False, "the real-model tests must never run on the placeholder stub"
    return provider


@pytest.fixture
def settings(settings: Settings) -> Settings:  # type: ignore[no-redef]
    """The ordinary test settings, with the real provider switched on."""
    return real_settings_from(settings)
