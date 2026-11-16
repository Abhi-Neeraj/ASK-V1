"""Settings, read from environment variables (prefix ASK_) and an optional `.env` file.

Validation happens at startup, so a bad deployment fails immediately with a clear message
instead of misbehaving later. Secrets are only ever read from the environment.
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = API_DIR.parent / "data"

# Values people copy from examples. Refusing them stops a placeholder reaching production.
_PLACEHOLDER_SECRETS = {"change-me", "changeme", "replace-me", "secret", "password", "example"}


def _require_public_https(name: str, value: str) -> None:
    """Production addresses must be https and name a real host, not this machine."""
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        raise ValueError(f"{name} must be an https:// address in production (got {value!r})")
    if host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"} or host.endswith(".localhost"):  # noqa: S104
        raise ValueError(f"{name} cannot point at this machine in production (got {value!r})")


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ASK_",
        env_file=API_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- security -----------------------------------------------------------------------------
    secret_key: SecretStr = Field(
        description="At least 32 random characters. Keys CSRF tokens and pairing codes."
    )
    web_origins: Annotated[list[str], NoDecode] = ["http://localhost:3000"]
    web_base_url: str = "http://localhost:3000"
    # None means "secure cookies exactly when running in production".
    cookie_secure: bool | None = None
    session_ttl_hours: int = Field(default=168, ge=1, le=24 * 90)
    # Lockets: each locket's secret is HMAC-SHA256(this key, product ID). Write it into lockets at
    # manufacture; changing it later breaks pairing for every locket already made. Never logged.
    locket_master_key: SecretStr | None = None
    locket_code_ttl_minutes: int = Field(default=10, ge=1, le=60)
    locket_code_requests_per_hour: int = Field(default=5, ge=1)
    locket_code_requests_per_locket_per_hour: int = Field(default=10, ge=1)
    # Firmware for over-the-air updates: how long a download link works, and the largest file accepted.
    firmware_link_minutes: int = Field(default=30, ge=1, le=24 * 60)
    firmware_max_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    # The one company whose administrators may publish firmware. Every locket on this server is offered the
    # latest build, so this must be the operator, never a customer. Unset: nobody can upload.
    firmware_publisher_company_id: uuid.UUID | None = None
    session_idle_hours: int = Field(default=24, ge=1, le=24 * 30)
    # Trust X-Forwarded-For from the web tier. Only enable behind a proxy you control.
    trust_forwarded_for: bool = False
    # How many proxies you control sit between the internet and this API. The client address is the entry that
    # many places from the END of X-Forwarded-For, because each trusted proxy appends the address it saw and
    # anything a client sent itself stays at the front, where it cannot be mistaken for a real hop.
    trusted_proxy_hops: int = Field(default=1, ge=1, le=5)

    # --- data ---------------------------------------------------------------------------------
    database_url: str = f"sqlite:///{(DATA_DIR / 'ask.sqlite3').as_posix()}"
    storage_dir: Path = DATA_DIR / "recordings"

    # --- device and upload rules ---------------------------------------------------------------
    heartbeat_expiry_seconds: int = Field(default=90, ge=10, le=3600)
    pairing_ttl_minutes: int = Field(default=10, ge=1, le=120)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)
    max_clip_seconds: int = Field(default=600, ge=1)
    min_clip_seconds: float = Field(default=0.5, gt=0)
    allowed_sample_rates: Annotated[list[int], NoDecode] = [16000]
    upload_read_timeout_seconds: float = Field(default=30.0, gt=0)
    # Ordinary (JSON) request bodies. Audio uploads have their own limit above.
    max_json_bytes: int = Field(default=1024 * 1024, ge=1024)
    # The whole upload, not just the gap between pieces. Without it a sender can drip one byte every few
    # seconds and keep a request, a spool file and a worker thread tied up for as long as it likes.
    upload_total_seconds: float = Field(default=300.0, gt=0)
    chunk_seconds: int = Field(default=10, ge=1, le=600)
    # Periodic housekeeping inside the API process (marking silent devices offline, purging deleted
    # recordings). Turn off to run `python -m app.cli housekeeping` from a scheduler instead.
    run_background_tasks: bool = True
    background_interval_seconds: int = Field(default=15, ge=1, le=3600)

    # --- live updates (GET /api/events) --------------------------------------------------------
    event_poll_interval_seconds: float = Field(default=1.0, gt=0)
    event_keepalive_seconds: float = Field(default=15.0, gt=0)
    # A stream ends itself after this long and the browser reconnects with Last-Event-ID, so no
    # event is lost and no connection outlives a revoked session for more than a few seconds.
    event_stream_max_seconds: float = Field(default=600.0, gt=0)
    max_event_streams_per_user: int = Field(default=5, ge=1)
    max_event_streams: int = Field(default=500, ge=1)
    live_event_retention_hours: int = Field(default=24, ge=1, le=24 * 30)

    # --- processing (the worker: transcription, speaker separation, speaker matching) -----------
    # none: no worker may start; recordings wait, and the dashboard says so.
    # stub: development only. Produces clearly labelled placeholder text, never real transcripts and
    #       never speaker names. Refused in production.
    # local: real models on this machine (needs the `ai` extra, downloaded weights and, for speaker
    #       separation, a Hugging Face token). Each missing piece degrades on its own and is reported.
    # google: Google Cloud Speech-to-Text for the transcript and Gemini for the summary (needs the `google`
    #       extra, ASK_GOOGLE_APPLICATION_CREDENTIALS and ASK_GEMINI_API_KEY). Audio leaves this server.
    ai_provider: Literal["none", "stub", "local", "google"] = "none"
    # Path to a Google Cloud service-account JSON file allowed to use Speech-to-Text. Never logged.
    google_application_credentials: Path | None = None
    # Or a Google Cloud API key with Speech-to-Text enabled, used when there is no service-account file.
    google_stt_api_key: SecretStr | None = None  # never logged
    google_stt_language: str = "en-US"
    gemini_api_key: SecretStr | None = None  # never logged
    gemini_model: str = "gemini-2.5-flash"
    ai_asr_model: str = "small"  # a faster-whisper model size, or a path to a converted model
    ai_asr_device: str = "auto"
    ai_asr_compute_type: str = "int8"
    ai_asr_language: str | None = None  # None: detect the language of each recording
    # Candidate sentences the decoder keeps while transcribing. 1 is the fastest and a little less accurate.
    ai_asr_beam_size: int = Field(default=5, ge=1, le=10)
    ai_model_dir: Path | None = None  # where downloaded weights live (default: the library's own cache)
    # Never contact the model hub: use only weights already in ai_model_dir (or the library's cache), and
    # fail clearly if they are missing. Recordings are never sent anywhere either way; this is for machines
    # that must not make outside connections at all.
    ai_offline: bool = False
    # Where speaker separation and speaker matching run: auto (a GPU if there is one), cpu or cuda.
    ai_torch_device: Literal["auto", "cpu", "cuda"] = "auto"
    # CPU threads for the models. 0 leaves each library to choose (usually every core).
    ai_cpu_threads: int = Field(default=0, ge=0, le=256)
    hf_token: SecretStr | None = None  # for gated models (speaker separation). Never logged.
    ai_diarization_model: str = "pyannote/speaker-diarization-3.1"
    # Who separates speakers. auto: pyannote when it is installed and a token is set, otherwise nobody
    # (every recording is one speaker). pyannote: as auto, but say so if it cannot run. clustering:
    # EXPERIMENTAL, needs no token; groups short windows of speech by how their voices sound, using the
    # speaker model. pitch: EXPERIMENTAL, needs no token and no model at all; tells voices apart by pitch
    # first and by the colour of the voice second (see app/voiceid/diarize.py).
    ai_diarizer: Literal["auto", "pyannote", "clustering", "pitch"] = "auto"
    # pitch only: windows whose base pitch differs by more than this many octaves (12 semitones each) are
    # never
    # the same speaker, and windows closer in pitch than that are grouped when their voice colour is closer
    # than the distance below. Only tried on synthetic voices; check them on your own recordings.
    diarization_pitch_gate_octaves: float = Field(default=0.45, gt=0, le=2)
    diarization_pitch_cluster_distance: float = Field(default=1.2, gt=0, le=10)
    # Voice signatures (pitch plus the shape of the voice's spectrum; see app/voiceid/signature.py). A voice
    # is
    # given a name only if its similarity reaches the threshold (0 to 1, higher means more "Unknown speaker")
    # and beats the next person by the margin, and only among people whose base pitch is within the gate
    # (octaves). NOT calibrated on real people: only tried on synthetic voices.
    signature_match_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    signature_match_margin: float = Field(default=0.1, ge=0.0, le=1.0)
    signature_pitch_gate_octaves: float = Field(default=0.4, gt=0, le=2)
    # The meeting summary (decisions, actions, owners; see app/insights). auto: match sentences to activities
    # by
    # meaning if the sentence model is installed and its weights are on this machine, otherwise by words.
    # semantic: insist on the sentence model (fail clearly if it cannot run). lexical: always by words.
    analysis_engine: Literal["auto", "lexical", "semantic"] = "auto"
    ai_text_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    # Weights are never downloaded unless this is on. Off, the sentence model only runs from weights already
    # in ASK_AI_MODEL_DIR (or the library's own cache), and nothing is fetched from the internet.
    ai_text_allow_download: bool = False
    # Context check in the summary: a sentence is in the same context as the three before it if it is more
    # similar than this to the closest of them (0 to 1), otherwise a new context starts. Empty: the measured
    # default for the engine (0.30 for the sentence model; none for the words engine, which cannot do this).
    # 0.6 was suggested and called almost every sentence a new topic: see app/insights/context.py.
    context_similarity: float | None = Field(default=None, gt=0, lt=1)
    # clustering only: windows whose voice prints are closer than this (cosine distance, 0 to 2) are the same
    # speaker. Only tried on synthetic voices; check it on your own recordings.
    diarization_cluster_distance: float = Field(default=0.6, gt=0, lt=2)
    ai_speaker_model: str = "speechbrain/spkrec-ecapa-voxceleb"
    # Cosine similarity a voice must reach to be given a name. NOT calibrated until you run
    # `python -m app.cli calibrate-speakers` on your own recordings. Higher means more "Unknown speaker".
    speaker_match_threshold: float = Field(default=0.5, ge=-1.0, le=1.0)
    # The best match must also beat the runner-up by this much, so two similar voices are not guessed at.
    speaker_match_margin: float = Field(default=0.05, ge=0.0, le=1.0)
    # A speaker needs this much speech in a recording before their voice is compared at all.
    speaker_min_speech_seconds: float = Field(default=3.0, gt=0)
    voice_enroll_min_samples: int = Field(default=3, ge=1, le=20)
    voice_enroll_max_samples: int = Field(default=10, ge=1, le=50)
    voice_sample_min_seconds: float = Field(default=3.0, gt=0)
    voice_sample_max_seconds: float = Field(default=30.0, gt=0)
    job_retry_base_seconds: float = Field(default=30.0, ge=0)
    # A job whose worker has been silent this long is taken back (the worker crashed or was killed).
    # A running worker renews its claim three times in this period, even while a model is busy, so it can be
    # short: only a worker that has really died (or is frozen) lets it lapse. 15 minutes (the earlier default)
    # meant a recording stuck for a quarter of an hour after a crash.
    job_lock_timeout_seconds: float = Field(default=180.0, gt=0)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    # The dashboard says "no worker running" when none has reported in for this long.
    worker_online_seconds: float = Field(default=30.0, gt=0)

    # --- simulated lockets ---------------------------------------------------------------------
    # tools/simulate_device.py pairs as a "simulated" locket. None means: allowed everywhere except
    # production, where real hardware is the only thing that may pair unless this is set to true.
    allow_simulated_devices: bool | None = None

    # --- accounts ------------------------------------------------------------------------------
    invite_ttl_hours: int = Field(default=72, ge=1, le=24 * 30)
    reset_ttl_minutes: int = Field(default=30, ge=5, le=24 * 60)
    # Invitations one person may send per hour. Each sends an email to an address they choose, so without a
    # limit the server could be used to mail anyone.
    invites_per_hour: int = Field(default=30, ge=1)
    verify_ttl_hours: int = Field(default=48, ge=1, le=24 * 14)

    # --- abuse limits (per process; see docs for scaling out) ----------------------------------
    login_attempts_per_minute: int = Field(default=5, ge=1)
    reset_requests_per_hour: int = Field(default=5, ge=1)
    pair_attempts_per_minute: int = Field(default=10, ge=1)
    contact_per_hour: int = Field(default=5, ge=1)

    # --- email ---------------------------------------------------------------------------------
    email_backend: Literal["console", "smtp", "memory"] = "console"
    email_from: str = "ASK <no-reply@localhost>"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    contact_recipient: str | None = None

    @field_validator("database_url")
    @classmethod
    def _database_driver(cls, value: str) -> str:
        return _with_psycopg_driver(value)

    @field_validator("web_origins", mode="before")
    @classmethod
    def _origins_from_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("allowed_sample_rates", mode="before")
    @classmethod
    def _rates_from_csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("secret_key")
    @classmethod
    def _secret_is_strong(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 32:
            raise ValueError(
                "must be at least 32 characters (generate one with `python -m app.cli generate-env`)"
            )
        return value

    @field_validator("locket_master_key")
    @classmethod
    def _locket_key_is_strong(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 32:
            raise ValueError("must be at least 32 characters")
        return value

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if (
            self.locket_master_key is not None
            and self.locket_master_key.get_secret_value() == self.secret_key.get_secret_value()
        ):
            raise ValueError("ASK_LOCKET_MASTER_KEY must be different from ASK_SECRET_KEY")
        if self.secret_key.get_secret_value().lower().startswith(tuple(_PLACEHOLDER_SECRETS)):
            raise ValueError("ASK_SECRET_KEY looks like a placeholder; generate a real one")
        if "*" in self.web_origins:
            raise ValueError("ASK_WEB_ORIGINS must list explicit origins, never '*'")
        if self.env == "production":
            if self.database_url.startswith("sqlite"):
                raise ValueError("production requires PostgreSQL (ASK_DATABASE_URL), not SQLite")
            if self.cookie_secure is False:
                raise ValueError("production requires secure cookies (leave ASK_COOKIE_SECURE unset or true)")
            if self.email_backend == "console":
                raise ValueError("production requires ASK_EMAIL_BACKEND=smtp")
            if self.email_backend == "memory":
                raise ValueError(
                    "production cannot keep mail in memory (ASK_EMAIL_BACKEND=memory): it would send nothing"
                )
            if self.ai_provider == "stub":
                raise ValueError(
                    "production cannot use the placeholder processing provider (ASK_AI_PROVIDER=stub)"
                )
            if self.ai_provider == "google":
                if self.google_application_credentials is None and self.google_stt_api_key is None:
                    raise ValueError(
                        "ASK_AI_PROVIDER=google needs ASK_GOOGLE_APPLICATION_CREDENTIALS "
                        "or ASK_GOOGLE_STT_API_KEY"
                    )
                if self.gemini_api_key is None:
                    raise ValueError("ASK_AI_PROVIDER=google needs ASK_GEMINI_API_KEY")
            for origin in self.web_origins:
                _require_public_https("ASK_WEB_ORIGINS", origin)
            _require_public_https("ASK_WEB_BASE_URL", self.web_base_url)
        if self.voice_enroll_min_samples > self.voice_enroll_max_samples:
            raise ValueError("ASK_VOICE_ENROLL_MIN_SAMPLES cannot exceed ASK_VOICE_ENROLL_MAX_SAMPLES")
        if self.voice_sample_min_seconds > self.voice_sample_max_seconds:
            raise ValueError("ASK_VOICE_SAMPLE_MIN_SECONDS cannot exceed ASK_VOICE_SAMPLE_MAX_SECONDS")
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError("ASK_SMTP_HOST is required when ASK_EMAIL_BACKEND=smtp")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.env == "production" if self.cookie_secure is None else self.cookie_secure

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def simulated_devices_allowed(self) -> bool:
        if self.allow_simulated_devices is None:
            return self.env != "production"
        return self.allow_simulated_devices


class DatabaseSettings(BaseSettings):
    """Just the database URL, so migrations and backups never need the application's secrets."""

    model_config = SettingsConfigDict(
        env_prefix="ASK_",
        env_file=API_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = f"sqlite:///{(DATA_DIR / 'ask.sqlite3').as_posix()}"

    @field_validator("database_url")
    @classmethod
    def _database_driver(cls, value: str) -> str:
        return _with_psycopg_driver(value)


def _with_psycopg_driver(url: str) -> str:
    """Hosted databases (Render, Heroku-style) hand out `postgresql://` or `postgres://`, which SQLAlchemy
    reads as psycopg2. Only psycopg 3 is installed (`.[postgres]`), so name it. A named driver is kept."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


@lru_cache
def get_settings() -> Settings:
    return Settings()  # required fields (the secret key) come from the environment


def reset_settings_cache() -> None:
    get_settings.cache_clear()
