"""Voice enrollment: consent first, a few samples, a voice print stored, the audio never kept."""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.registry import ProviderHolder
from app.ai.stub import build_stub
from app.ai.types import Capabilities, PermanentError
from app.models import AuditEvent, Person, TranscriptSegment, VoiceProfile
from app.services import matching, voice
from tests.ai_doubles import TEST_VOICE_MODEL, SequenceEmbedder, make_provider
from tests.conftest import new_web
from tests.helpers import add_person, make_wav

SAMPLE = make_wav(4.0)


def manager_web(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def use_embedder(app, embedder: SequenceEmbedder) -> None:  # type: ignore[no-untyped-def]
    app.state.ai = ProviderHolder(app.state.settings, make_provider(embedder=embedder))


def send(web, person: Person, body: bytes = SAMPLE, content_type: str = "audio/wav"):  # type: ignore[no-untyped-def]
    return web.post(f"/api/people/{person.id}/enroll", content=body, headers={"Content-Type": content_type})


def profile_of(db: Session, person: Person) -> VoiceProfile | None:
    db.expire_all()
    return db.scalar(select(VoiceProfile).where(VoiceProfile.person_id == person.id))


class TestWhoMayEnroll:
    def test_consent_must_be_recorded_first(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        embedder = SequenceEmbedder()
        use_embedder(app, embedder)
        ana = add_person(db, acme["company"], "Ana", consent="pending")

        response = send(manager_web(app), ana)

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "consent_required"
        assert embedder.calls == 0  # no model was even run on a voice nobody agreed to keep
        assert profile_of(db, ana) is None

    def test_withdrawn_consent_blocks_it_too(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana", consent="revoked")
        assert send(manager_web(app), ana).status_code == 409

    def test_employees_cannot_enroll_anyone(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana")
        staff = new_web(app)
        staff.login("employee@acme.example.com")
        assert send(staff, ana).status_code == 403

    def test_another_companys_person_is_not_found(self, app, db: Session, acme, globex) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        stranger = add_person(db, globex["company"], "Outsider")
        assert send(manager_web(app), stranger).status_code == 404
        assert profile_of(db, stranger) is None

    def test_the_session_and_csrf_rules_apply(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana")
        assert send(new_web(app), ana).status_code == 401
        web = manager_web(app)
        response = web.post(
            f"/api/people/{ana.id}/enroll", content=SAMPLE, headers={"Content-Type": "audio/wav"}, csrf=False
        )
        assert response.status_code == 403


class TestWhenThereIsNoSpeakerModel:
    def test_no_provider_says_how_to_set_one_up(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        response = send(manager_web(app), ana)  # the default test configuration has no provider
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "not_available"
        assert "ASK_AI_PROVIDER" in response.json()["error"]["message"]

    def test_the_placeholder_stub_has_no_speaker_model_and_never_pretends_to(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        app.state.ai = ProviderHolder(
            app.state.settings.model_copy(update={"ai_provider": "stub"}), build_stub()
        )
        app.state.settings = app.state.settings.model_copy(update={"ai_provider": "stub"})
        ana = add_person(db, acme["company"], "Ana")

        response = send(manager_web(app), ana)

        assert response.status_code == 501
        assert "placeholder" in response.json()["error"]["message"].lower()
        assert profile_of(db, ana) is None


class TestAddingSamples:
    def test_three_samples_make_a_person_recognizable_and_nothing_secret_comes_back(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        use_embedder(app, SequenceEmbedder([(1, 0.1, 0), (0.9, 0.2, 0), (1, 0, 0.1)]))
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)

        seen = []
        for expected in (1, 2, 3):
            response = send(web, ana)
            assert response.status_code == 200, response.text
            body = response.json()
            seen.append((body["sampleCount"], body["enrolled"]))
            assert body["samplesRequired"] == 3
            assert body["voiceOutdated"] is False
            # The voice print is never part of any response.
            assert not {"embedding", "vector", "voicePrint"} & set(body)
            assert expected == body["sampleCount"]

        assert seen == [(1, False), (2, False), (3, True)]  # recognizable only once enough was heard
        listed = next(p for p in web.get("/api/people").json() if p["id"] == str(ana.id))
        assert listed["enrolled"] is True and listed["sampleCount"] == 3

    def test_the_stored_print_is_the_average_direction_of_the_samples_and_marks_its_model(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        use_embedder(app, SequenceEmbedder([(1, 0, 0), (0, 1, 0)]))
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        send(web, ana)
        send(web, ana)

        profile = profile_of(db, ana)
        assert profile is not None
        assert (profile.model_name, profile.sample_count, profile.embedding_dim) == (TEST_VOICE_MODEL, 2, 3)
        stored = matching.decode_vector(profile.embedding, profile.embedding_dim)
        assert stored == pytest.approx((2**-0.5, 2**-0.5, 0.0), abs=1e-6)
        assert matching.norm(stored) == pytest.approx(1.0, abs=1e-6)

    def test_the_sample_recording_is_deleted_whatever_happens(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        send(web, ana)
        send(web, ana, make_wav(1.0))  # too short: refused
        send(web, ana, b"not audio at all")  # not a WAV: refused
        leftovers = list(app.state.storage.tmp.glob("*.part"))
        assert leftovers == []
        assert not list(app.state.storage.root.rglob("*.wav"))  # and none became a stored recording

    def test_adding_a_sample_is_audited_without_the_voice_print(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder([(0.123456, 0.654321, 0.111111)]))
        ana = add_person(db, acme["company"], "Ana")
        send(manager_web(app), ana)

        db.expire_all()
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "person.voice_sample_added"))
        assert event is not None
        assert event.detail == {"sample_count": 1, "replaced_older_model": False}
        assert "0.1234" not in json.dumps(event.detail)

    def test_there_is_a_limit_to_how_many_samples_are_kept(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        limit = app.state.settings.voice_enroll_max_samples
        for _ in range(limit):
            assert send(web, ana).status_code == 200
        response = send(web, ana)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "enough_samples"
        profile = profile_of(db, ana)
        assert profile is not None and profile.sample_count == limit

    def test_a_print_from_a_different_model_is_replaced_not_mixed_in(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        db.add(
            VoiceProfile(
                person_id=ana.id,
                company_id=ana.company_id,
                embedding=matching.encode_vector(matching.normalize((0, 0, 1))),
                embedding_dim=3,
                model_name="an-older-model",
                model_version="0",
                sample_count=3,
            )
        )
        db.commit()
        use_embedder(app, SequenceEmbedder([(1, 0, 0)]))

        response = send(manager_web(app), ana)

        assert response.status_code == 200
        profile = profile_of(db, ana)
        assert profile is not None
        assert (profile.model_name, profile.sample_count) == (TEST_VOICE_MODEL, 1)
        assert matching.decode_vector(profile.embedding, 3) == pytest.approx((1.0, 0.0, 0.0), abs=1e-6)
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "person.voice_sample_added"))
        assert event is not None and event.detail is not None and event.detail["replaced_older_model"] is True


class TestBadSamples:
    @pytest.mark.parametrize(
        ("body", "content_type", "status", "code"),
        [
            (make_wav(1.0), "audio/wav", 422, "sample_too_short"),
            (make_wav(31.0), "audio/wav", 422, "sample_too_long"),
            (b"this is not a recording", "audio/wav", 422, "invalid_audio"),
            (b"", "audio/wav", 422, "invalid_audio"),
            (SAMPLE, "text/plain", 415, "unsupported_media_type"),
            (make_wav(4.0, silent=True), "audio/wav", 422, "silent_audio"),
        ],
        ids=["too-short", "too-long", "not-audio", "empty", "wrong-type", "silent"],
    )
    def test_each_is_refused_clearly_and_nothing_is_stored(
        self,
        app,
        db: Session,
        acme,
        body: bytes,
        content_type: str,
        status: int,
        code: str,  # type: ignore[no-untyped-def]
    ) -> None:
        embedder = SequenceEmbedder()
        use_embedder(app, embedder)
        ana = add_person(db, acme["company"], "Ana")

        response = send(manager_web(app), ana, body, content_type)

        assert (response.status_code, response.json()["error"]["code"]) == (status, code)
        assert embedder.calls == 0
        assert profile_of(db, ana) is None

    def test_a_sample_the_model_cannot_use_says_why(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(
            app, SequenceEmbedder(error=PermanentError("There is not enough speech in this recording."))
        )
        ana = add_person(db, acme["company"], "Ana")
        response = send(manager_web(app), ana)
        assert response.status_code == 422
        assert response.json()["error"] == {
            "code": "unusable_sample",
            "message": "There is not enough speech in this recording.",
        }

    def test_a_model_crash_is_reported_without_leaking_its_error(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        secret = "CUDA out of memory at /srv/models/ecapa.ckpt with hf_abcdefghijklmnop"
        use_embedder(app, SequenceEmbedder(error=RuntimeError(secret)))
        ana = add_person(db, acme["company"], "Ana")
        response = send(manager_web(app), ana)
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "speaker_model_error"
        assert "CUDA" not in response.text and "/srv" not in response.text and "hf_abc" not in response.text
        assert profile_of(db, ana) is None

    def test_trying_too_fast_is_slowed_down(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        codes = [send(web, ana, make_wav(1.0)).status_code for _ in range(32)]
        assert codes[:30] == [422] * 30
        assert 429 in codes[30:]


class TestWhenTheModelChanges:
    def test_a_print_from_the_previous_model_is_flagged_and_no_longer_counts(
        self,
        app,
        db: Session,
        acme,
        monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
    ) -> None:
        ana = add_person(db, acme["company"], "Ana")
        db.add(
            VoiceProfile(
                person_id=ana.id,
                company_id=ana.company_id,
                embedding=matching.encode_vector(matching.normalize((1, 0, 0))),
                embedding_dim=3,
                model_name="an-older-model",
                model_version="0",
                sample_count=3,
            )
        )
        db.commit()
        web = manager_web(app)
        listed = next(p for p in web.get("/api/people").json() if p["id"] == str(ana.id))
        assert (
            listed["enrolled"] is True and listed["voiceOutdated"] is False
        )  # no speaker model configured: nothing to compare to

        monkeypatch.setattr(voice, "capabilities", lambda s: Capabilities("local", False, True, False, True))
        monkeypatch.setattr(app.state.settings, "ai_speaker_model", TEST_VOICE_MODEL)
        listed = next(p for p in web.get("/api/people").json() if p["id"] == str(ana.id))
        assert listed["voiceOutdated"] is True
        assert listed["enrolled"] is False  # they must enroll again, and the screen says so

    def test_withdrawing_consent_deletes_the_print_and_unnames_past_lines(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        from tests.conftest import provision_device
        from tests.helpers import add_transcript

        use_embedder(app, SequenceEmbedder())
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        send(web, ana)
        assert profile_of(db, ana) is not None

        _, locket = provision_device(app, web)
        recording_id = uuid.UUID(locket.upload("clip-consent-0001", make_wav(1.0)).json()["recordingId"])
        add_transcript(db, recording_id, [{"text": "Hello there.", "person": ana, "confidence": 0.9}])
        named = db.scalar(select(TranscriptSegment).where(TranscriptSegment.person_id == ana.id))
        assert named is not None  # the past line carries her name

        assert web.post(f"/api/people/{ana.id}/consent", json={"granted": False}).status_code == 200

        assert profile_of(db, ana) is None
        db.expire_all()
        line = db.scalar(select(TranscriptSegment).where(TranscriptSegment.id == named.id))
        assert line is not None and line.person_id is None and line.confidence is None
        assert line.text == "Hello there."  # what was said stays; who said it no longer does
