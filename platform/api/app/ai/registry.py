"""Choosing the provider from settings, and saying what it can do without loading a model."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace

from app.ai.google import build_google, missing_settings
from app.ai.local import build_local, local_capabilities
from app.ai.stub import NOTES as STUB_NOTES
from app.ai.stub import build_stub
from app.ai.types import Capabilities, Provider, ProviderUnavailableError
from app.config import Settings

NOT_CONFIGURED = (
    "No processing provider is configured (ASK_AI_PROVIDER=none), so recordings wait. "
    "Set it to `local` for real models, or `stub` for development placeholders."
)


def capabilities(settings: Settings) -> Capabilities:
    """What the configured provider can do. Cheap: nothing is loaded, so the API can call it freely."""
    if settings.ai_provider == "local":
        return local_capabilities(settings)
    if settings.ai_provider == "google":
        missing = missing_settings(settings)
        return Capabilities(
            provider="google",
            placeholder=False,
            transcription=(
                settings.google_application_credentials is not None or settings.google_stt_api_key is not None
            ),
            diarization=(
                settings.google_application_credentials is not None or settings.google_stt_api_key is not None
            ),
            speaker_matching=False,
            notes=(
                *missing,
                "Audio is sent to Google Cloud Speech-to-Text; summaries are written by Gemini.",
            ),
        )
    if settings.ai_provider == "stub":
        return Capabilities(
            provider="stub",
            placeholder=True,
            transcription=True,
            diarization=False,
            speaker_matching=False,
            notes=STUB_NOTES,
        )
    return Capabilities(
        provider="none",
        placeholder=False,
        transcription=False,
        diarization=False,
        speaker_matching=False,
        notes=(NOT_CONFIGURED,),
    )


class ProviderHolder:
    """The API process's own copy of the provider, built on first use.

    The API only needs it to turn a voice sample into a voice print at enrollment, so a deployment
    that never enrolls anyone never loads a model. Models are not safe to share between threads, so
    `lock` serialises their use.
    """

    def __init__(self, settings: Settings, provider: Provider | None = None) -> None:
        self._settings = settings
        # Supplied directly by tests; otherwise built from settings on first use.
        self._provider: Provider | None = provider
        self._failure: str | None = None
        self._built = threading.Lock()
        self.lock = threading.Lock()

    def get(self) -> Provider | None:
        """The provider, or None if none is configured or it cannot start (see `failure`)."""
        with self._built:
            if self._provider is None and self._failure is None:
                try:
                    self._provider = build_provider(self._settings)
                except ProviderUnavailableError as error:
                    self._failure = str(error)
            return self._provider

    @property
    def failure(self) -> str | None:
        return self._failure


def warm_up(provider: Provider, log: Callable[[str], None] = lambda _message: None) -> Provider:
    """Load every model now, so the first recording does not pay for it and a missing model is found at
    start-up instead of on a customer's recording.

    Transcription is the one thing a worker cannot do without: if it will not load, this raises
    ProviderUnavailableError. Speaker separation and speaker matching each degrade on their own, exactly as
    when their library or token is missing: the provider that comes back simply lacks them, and its notes
    say why. Components without a `load` method (the stub, test doubles) are left as they are.
    """

    def load(kind: str, component: object) -> ProviderUnavailableError | None:
        loader = getattr(component, "load", None)
        if not callable(loader):
            return None
        started = time.monotonic()
        try:
            loader()
        except ProviderUnavailableError as error:
            return error  # returned whole, so what the library itself said stays attached to it
        log(f"{kind} model ready in {time.monotonic() - started:.1f}s")
        return None

    failure = load("speech recognition", provider.transcriber)
    if failure is not None:
        raise failure

    diarizer, embedder, notes = provider.diarizer, provider.embedder, list(provider.notes)
    if diarizer is not None and (failure := load("speaker separation", diarizer)) is not None:
        diarizer = None
        notes.append(
            f"Speaker separation is unavailable: {failure} Every recording is treated as one speaker."
        )
    if embedder is not None and (failure := load("speaker matching", embedder)) is not None:
        embedder = None
        notes.append(f"Speaker matching is unavailable: {failure} Nobody is identified by voice.")
    summarizer = provider.summarizer
    if summarizer is not None and (failure := load("summary", summarizer)) is not None:
        summarizer = None
        notes.append(f"Summaries are unavailable: {failure} Transcripts are saved without one.")
    return replace(provider, diarizer=diarizer, embedder=embedder, notes=tuple(notes), summarizer=summarizer)


def build_provider(settings: Settings) -> Provider:
    """The models to run. Raises ProviderUnavailableError (with what to do about it) if there are none."""
    if settings.ai_provider == "stub":
        return build_stub()
    if settings.ai_provider == "local":
        return build_local(settings)
    if settings.ai_provider == "google":
        return build_google(settings)
    raise ProviderUnavailableError(NOT_CONFIGURED)
