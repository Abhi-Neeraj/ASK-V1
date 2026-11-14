"""Test helpers: a client that behaves like the web app, and small builders for tenants and users."""

from __future__ import annotations

import hashlib
import io
import math
import struct
import uuid
import wave
from datetime import UTC, datetime
from typing import Any

import httpx2 as httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Company, Membership, Person, Recording, Transcript, TranscriptSegment, User
from app.security.passwords import hash_password, use_cheap_parameters_for_tests

PASSWORD = "correct-horse-battery-staple"
ORIGIN = "http://localhost:3000"


class WebClient:
    """A TestClient wrapper that copies the CSRF cookie into the CSRF header like the web app does."""

    def __init__(self, client: TestClient) -> None:
        self.client = client

    def _headers(self, method: str, csrf: bool, extra: dict[str, str] | None) -> dict[str, str]:
        headers = {"Origin": ORIGIN}
        token = self.client.cookies.get("ask_csrf")
        if csrf and token and method in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["x-csrf-token"] = token
        headers.update(extra or {})
        return headers

    def request(
        self,
        method: str,
        url: str,
        *,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        return self.client.request(method, url, headers=self._headers(method, csrf, headers), **kwargs)

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", url, **kwargs)

    def login(self, email: str, password: str = PASSWORD) -> httpx.Response:
        return self.post("/api/auth/login", json={"email": email, "password": password})


class DeviceClient:
    """A locket talking to the API: bearer credential, no cookies, no CSRF."""

    def __init__(self, client: TestClient, credential: str | None = None) -> None:
        self.client = client
        self.credential = credential

    def request(
        self,
        method: str,
        url: str,
        *,
        credential: str | None = None,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        merged = dict(headers or {})
        token = credential if credential is not None else self.credential
        if token:
            merged["Authorization"] = f"Bearer {token}"
        return self.client.request(method, url, headers=merged, **kwargs)

    def upload(
        self,
        client_id: str,
        body: bytes,
        *,
        sha256: str | None = None,
        recorded_at: str | None = None,
        content_type: str = "audio/wav",
        extra: dict[str, str] | None = None,
        credential: str | None = None,
    ) -> httpx.Response:
        headers = {
            "Content-Type": content_type,
            "X-Content-SHA256": sha256 if sha256 is not None else hashlib.sha256(body).hexdigest(),
            "X-Recorded-At": recorded_at or datetime.now(UTC).isoformat(),
        }
        headers.update(extra or {})
        return self.request(
            "PUT",
            f"/api/device/v1/recordings/{client_id}",
            content=body,
            headers=headers,
            credential=credential,
        )

    def pair(
        self, code: str, firmware: str | None = "0.9.2", *, simulated: bool | None = None
    ) -> httpx.Response:
        body: dict[str, Any] = {"code": code, "firmwareVersion": firmware}
        if simulated is not None:
            body["simulated"] = simulated
        return self.request("POST", "/api/device/v1/pair", json=body)

    def heartbeat(self, **body: Any) -> httpx.Response:
        return self.request("POST", "/api/device/v1/heartbeat", json=body)


def add_company(db: Session, name: str = "Acme") -> Company:
    company = Company(name=name)
    db.add(company)
    db.commit()
    return company


def add_member(
    db: Session, company: Company, email: str, role: str = "employee", *, name: str | None = None
) -> User:
    user = User(
        email=email.lower(),
        name=name or email.split("@")[0].title(),
        password_hash=_HASH,
        email_verified_at=None,
    )
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, company_id=company.id, role=role))
    db.commit()
    return user


# Production hashing is deliberately slow; tests use cheap parameters, and hash once for all users.
use_cheap_parameters_for_tests()
_HASH = hash_password(PASSWORD)


def uid() -> str:
    return uuid.uuid4().hex[:8]


def add_person(db: Session, company: Company, name: str, consent: str = "granted") -> Person:
    person = Person(company_id=company.id, display_name=name, consent_status=consent)
    db.add(person)
    db.commit()
    return person


def add_transcript(
    db: Session,
    recording_id: uuid.UUID,
    lines: list[dict[str, Any]],
    *,
    state: str = "completed",
) -> Transcript:
    """What the processing worker will eventually write, so the library can be tested without models.
    Each line: {"text": str, "person": Person | None, "confidence": float | None, "needs_review": bool}."""
    recording = db.get(Recording, recording_id)
    assert recording is not None
    transcript = Transcript(
        recording_id=recording_id,
        company_id=recording.company_id,
        full_text=" ".join(line["text"] for line in lines),
        transcription_model="test-transcriber",
        transcription_model_version="0",
        diarization_model="test-diarizer",
        diarization_model_version="0",
        review_status="needs_review" if any(line.get("needs_review") for line in lines) else "none",
    )
    db.add(transcript)
    db.flush()
    for index, line in enumerate(lines):
        person = line.get("person")
        db.add(
            TranscriptSegment(
                transcript_id=transcript.id,
                company_id=recording.company_id,
                idx=index,
                start_ms=index * 1000,
                end_ms=index * 1000 + 900,
                speaker_label=f"SPEAKER_{index % 2:02d}",
                person_id=person.id if person else None,
                confidence=line.get("confidence") if person else None,
                text=line["text"],
                needs_review=bool(line.get("needs_review")),
            )
        )
    recording.state = "needs_review" if transcript.review_status == "needs_review" else state
    db.commit()
    return transcript


def make_wav(
    seconds: float = 1.0,
    *,
    rate: int = 16000,
    channels: int = 1,
    bits: int = 16,
    silent: bool = False,
    frequency: float = 440.0,
) -> bytes:
    """A real, valid PCM WAV file (a sine tone, or silence) built with the standard library."""
    frames = int(seconds * rate)
    sample_width = bits // 8
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(sample_width)
        writer.setframerate(rate)
        for n in range(frames):
            value = 0 if silent else int(12000 * math.sin(2 * math.pi * frequency * n / rate))
            for _ in range(channels):
                if bits == 16:
                    writer.writeframesraw(struct.pack("<h", value))
                else:
                    writer.writeframesraw(struct.pack("<B", (value >> 8) + 128))
    return out.getvalue()
