"""Voice signatures over the API: add samples from WAV uploads, see the pitch summary, match a new upload
against
the people who have consented. Made-up voices with known pitch (no model, no network)."""

from __future__ import annotations

import json

import numpy as np
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditEvent, Person, PersonSignature
from tests.conftest import new_web
from tests.helpers import add_person, make_wav
from tests.voice_synth import HIGH_FEMALE, LOW_MALE, MID_MALE, RATE, Voice, speak, to_wav_bytes


def manager_web(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def sample(voice: Voice, seed: int, seconds: float = 6.0) -> bytes:
    return to_wav_bytes(speak(voice, seconds, seed=seed))


def add_sample(web, person: Person, body: bytes, content_type: str = "audio/wav"):  # type: ignore[no-untyped-def]
    return web.post(
        f"/api/people/{person.id}/signature", content=body, headers={"Content-Type": content_type}
    )


def match(web, body: bytes, content_type: str = "audio/wav"):  # type: ignore[no-untyped-def]
    return web.post("/api/people/signature-matches", content=body, headers={"Content-Type": content_type})


def enroll(web, person: Person, voice: Voice, count: int = 3, first_seed: int = 1) -> None:  # type: ignore[no-untyped-def]
    for n in range(count):
        response = add_sample(web, person, sample(voice, first_seed + n))
        assert response.status_code == 200, response.text


def stored(db: Session, person: Person) -> PersonSignature | None:
    db.expire_all()
    return db.scalar(select(PersonSignature).where(PersonSignature.person_id == person.id))


class TestWhoMayAddASample:
    def test_consent_must_be_recorded_first(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana", consent="pending")
        response = add_sample(manager_web(app), ana, sample(MID_MALE, 1))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "consent_required"
        assert stored(db, ana) is None

    def test_withdrawn_consent_blocks_it_too(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana", consent="revoked")
        assert add_sample(manager_web(app), ana, sample(MID_MALE, 1)).status_code == 409

    def test_employees_cannot_add_samples_or_match(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        staff = new_web(app)
        staff.login("employee@acme.example.com")
        assert add_sample(staff, ana, sample(MID_MALE, 1)).status_code == 403
        assert match(staff, sample(MID_MALE, 2)).status_code == 403

    def test_another_companys_person_is_not_found(self, app, db: Session, acme, globex) -> None:  # type: ignore[no-untyped-def]
        stranger = add_person(db, globex["company"], "Outsider")
        assert add_sample(manager_web(app), stranger, sample(MID_MALE, 1)).status_code == 404
        assert stored(db, stranger) is None

    def test_the_session_and_csrf_rules_apply(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        assert add_sample(new_web(app), ana, sample(MID_MALE, 1)).status_code == 401
        assert match(new_web(app), sample(MID_MALE, 1)).status_code == 401
        web = manager_web(app)
        no_csrf = web.post(
            f"/api/people/{ana.id}/signature",
            content=sample(MID_MALE, 1),
            headers={"Content-Type": "audio/wav"},
            csrf=False,
        )
        assert no_csrf.status_code == 403


class TestBuildingASignature:
    def test_three_samples_make_a_person_matchable_and_show_their_pitch_to_managers(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)

        seen = []
        for n in (1, 2, 3):
            response = add_sample(web, ana, sample(MID_MALE, n))
            assert response.status_code == 200, response.text
            signature = response.json()["signature"]
            seen.append((signature["sampleCount"], signature["ready"]))
            assert signature["samplesRequired"] == 3 and signature["outdated"] is False
        assert seen == [(1, False), (2, False), (3, True)]

        pitch = response.json()["signature"]["pitch"]
        assert pitch["lowHz"] < pitch["baseHz"] < pitch["highHz"]
        assert pitch["baseHz"] == pytest.approx(MID_MALE.f0, rel=0.06)  # within about a semitone

    def test_the_voice_signature_itself_never_comes_back(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        enroll(web, ana, MID_MALE)
        listed = web.get("/api/people").text
        for secret in ("timbre", "vector", "embedding", "voicePrint"):
            assert secret not in listed

    def test_employees_see_that_a_signature_exists_but_not_the_pitch(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        enroll(manager_web(app), ana, MID_MALE)
        staff = new_web(app)
        staff.login("employee@acme.example.com")
        listed = next(p for p in staff.get("/api/people").json() if p["id"] == str(ana.id))
        assert listed["signature"]["ready"] is True
        assert listed["signature"]["pitch"] is None

    def test_the_stored_pitch_is_a_blend_of_the_samples(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        enroll(manager_web(app), ana, MID_MALE, count=2)
        row = stored(db, ana)
        assert row is not None
        assert row.sample_count == 2 and row.version == "dsp-1"
        assert len(row.timbre) == 24 * 4  # 24 numbers, never returned
        assert row.pitch_low_hz <= row.pitch_base_hz <= row.pitch_high_hz

    def test_there_is_a_limit_to_how_many_samples_are_kept(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        app.state.settings = app.state.settings.model_copy(update={"voice_enroll_max_samples": 3})
        enroll(web, ana, MID_MALE, count=3)
        response = add_sample(web, ana, sample(MID_MALE, 9))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "enough_samples"

    def test_the_sample_recording_is_deleted_whatever_happens(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        add_sample(web, ana, sample(MID_MALE, 1))
        add_sample(web, ana, make_wav(1.0))  # too short
        add_sample(web, ana, b"not audio at all")
        add_sample(web, ana, to_wav_bytes(np.random.default_rng(1).normal(0, 0.1, RATE * 4)))  # noise
        assert list(app.state.storage.tmp.glob("*.part")) == []
        assert not list(app.state.storage.root.rglob("*.wav"))

    def test_adding_a_sample_is_audited_without_any_measurement(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        add_sample(manager_web(app), ana, sample(MID_MALE, 1))
        db.expire_all()
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "person.signature_sample_added"))
        assert event is not None
        assert event.detail == {"sample_count": 1, "replaced_older_version": False}

    def test_a_signature_from_an_older_version_is_replaced_not_mixed_in(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        db.add(
            PersonSignature(
                person_id=ana.id,
                company_id=ana.company_id,
                timbre=b"\x00" * 96,
                version="dsp-0",
                pitch_base_hz=100,
                pitch_low_hz=90,
                pitch_high_hz=110,
                voiced_seconds=5,
                sample_count=3,
            )
        )
        db.commit()
        web = manager_web(app)
        listed = next(p for p in web.get("/api/people").json() if p["id"] == str(ana.id))
        assert listed["signature"]["outdated"] is True and listed["signature"]["ready"] is False

        response = add_sample(web, ana, sample(MID_MALE, 1))

        assert response.status_code == 200
        row = stored(db, ana)
        assert row is not None and (row.version, row.sample_count) == ("dsp-1", 1)


class TestBadSamples:
    @pytest.mark.parametrize(
        ("body", "content_type", "status", "code"),
        [
            (make_wav(1.0), "audio/wav", 422, "sample_too_short"),
            (make_wav(31.0), "audio/wav", 422, "sample_too_long"),
            (b"this is not a recording", "audio/wav", 422, "invalid_audio"),
            (b"", "audio/wav", 422, "invalid_audio"),
            (make_wav(4.0), "text/plain", 415, "unsupported_media_type"),
            (make_wav(4.0, silent=True), "audio/wav", 422, "silent_audio"),
            (
                to_wav_bytes(np.random.default_rng(2).normal(0, 0.1, RATE * 4)),
                "audio/wav",
                422,
                "not_enough_speech",
            ),
        ],
        ids=["too-short", "too-long", "not-audio", "empty", "wrong-type", "silent", "noise-only"],
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
        ana = add_person(db, acme["company"], "Ana")
        response = add_sample(manager_web(app), ana, body, content_type)
        assert (response.status_code, response.json()["error"]["code"]) == (status, code)
        assert stored(db, ana) is None

    def test_trying_too_fast_is_slowed_down(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        codes = [add_sample(web, ana, make_wav(1.0)).status_code for _ in range(32)]
        assert codes[:30] == [422] * 30
        assert 429 in codes[30:]


class TestForgetting:
    def test_withdrawing_consent_deletes_the_signature(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        enroll(web, ana, MID_MALE)
        assert stored(db, ana) is not None

        response = web.post(f"/api/people/{ana.id}/consent", json={"granted": False})

        assert response.status_code == 200
        assert stored(db, ana) is None
        assert response.json()["signature"]["sampleCount"] == 0

    def test_deleting_the_voice_data_deletes_the_signature(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        enroll(web, ana, MID_MALE)
        assert web.delete(f"/api/people/{ana.id}/voice-profile").status_code == 204
        assert stored(db, ana) is None

    def test_deleting_the_person_deletes_the_signature(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        enroll(web, ana, MID_MALE)
        assert web.delete(f"/api/people/{ana.id}").status_code == 204
        db.expire_all()
        assert db.scalar(select(PersonSignature)) is None


class TestMatchingAnUpload:
    def two_people(self, app, db: Session, acme):  # type: ignore[no-untyped-def]
        low, high = add_person(db, acme["company"], "Lena Low"), add_person(db, acme["company"], "Hana High")
        web = manager_web(app)
        enroll(web, low, LOW_MALE)
        enroll(web, high, HIGH_FEMALE)
        return web, low, high

    def test_a_new_recording_of_each_voice_is_matched_to_its_person(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, low, high = self.two_people(app, db, acme)
        for voice, person in ((LOW_MALE, low), (HIGH_FEMALE, high)):
            response = match(web, sample(voice, 77))
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["outcome"] == "matched"
            assert body["match"]["personId"] == str(person.id)
            assert body["match"]["name"] == person.display_name
            assert body["match"]["passedPitchGate"] is True
            assert body["candidatesConsidered"] == 2
            assert body["pitch"]["baseHz"] == pytest.approx(voice.f0, rel=0.08)

    def test_a_stranger_is_not_named(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web, _, _ = self.two_people(app, db, acme)
        stranger = Voice(f0=170.0, formants=(430.0, 2300.0, 3400.0), bandwidth=(160.0, 220.0, 300.0))
        body = match(web, sample(stranger, 5)).json()
        assert body["outcome"] == "no_confident_match"
        assert body["match"] is None
        assert "not identity verification" in body["notice"]

    def test_pitch_is_the_first_layer_so_a_much_lower_voice_is_ruled_out_before_its_colour_is_looked_at(
        self,
        app,
        db: Session,
        acme,  # type: ignore[no-untyped-def]
    ) -> None:
        web, low, high = self.two_people(app, db, acme)
        body = match(web, sample(LOW_MALE, 88)).json()
        by_id = {c["personId"]: c for c in body["closest"]}
        assert by_id[str(low.id)]["passedPitchGate"] is True
        assert by_id[str(high.id)]["passedPitchGate"] is False
        assert body["closest"][0]["personId"] == str(low.id)  # those that pass the gate are listed first

    def test_with_nobody_enrolled_there_is_nothing_to_match(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        body = match(manager_web(app), sample(MID_MALE, 1)).json()
        assert body["outcome"] == "nobody_enrolled"
        assert body["match"] is None and body["closest"] == []

    def test_someone_with_too_few_samples_is_not_a_candidate(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        enroll(web, ana, MID_MALE, count=2)  # three are needed
        assert match(web, sample(MID_MALE, 9)).json()["outcome"] == "nobody_enrolled"

    def test_people_whose_consent_was_withdrawn_are_never_candidates(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        ana = add_person(db, acme["company"], "Ana")
        web = manager_web(app)
        enroll(web, ana, MID_MALE)
        # A signature left behind by some other path (a restored backup, say) still must not be used.
        web.post(f"/api/people/{ana.id}/consent", json={"granted": False})
        db.add(
            PersonSignature(
                person_id=ana.id,
                company_id=ana.company_id,
                timbre=b"\x00" * 96,
                version="dsp-1",
                pitch_base_hz=135,
                pitch_low_hz=120,
                pitch_high_hz=150,
                voiced_seconds=9,
                sample_count=3,
            )
        )
        db.commit()
        assert match(web, sample(MID_MALE, 9)).json()["outcome"] == "nobody_enrolled"

    def test_another_companys_people_are_never_candidates(self, app, db: Session, acme, globex) -> None:  # type: ignore[no-untyped-def]
        outsider = add_person(db, globex["company"], "Outsider")
        db.add(
            PersonSignature(
                person_id=outsider.id,
                company_id=globex["company"].id,
                timbre=b"\x00" * 96,
                version="dsp-1",
                pitch_base_hz=135,
                pitch_low_hz=120,
                pitch_high_hz=150,
                voiced_seconds=9,
                sample_count=3,
            )
        )
        db.commit()
        body = match(manager_web(app), sample(MID_MALE, 1)).json()
        assert body["outcome"] == "nobody_enrolled" and body["candidatesConsidered"] == 0
        assert "Outsider" not in json.dumps(body)

    def test_the_upload_is_deleted_and_the_lookup_is_audited_without_names(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        web, _, _ = self.two_people(app, db, acme)
        match(web, sample(LOW_MALE, 5))
        assert not list(app.state.storage.root.rglob("*.wav"))
        assert list(app.state.storage.tmp.glob("*.part")) == []
        db.expire_all()
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "voice_signature.matched"))
        assert event is not None
        assert event.detail == {"outcome": "matched", "candidates": 2}
        assert "Lena" not in json.dumps(event.detail)

    def test_a_bad_upload_is_refused_like_a_bad_sample(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        assert match(web, make_wav(1.0)).json()["error"]["code"] == "sample_too_short"
        assert match(web, b"nope").json()["error"]["code"] == "invalid_audio"
        assert match(web, make_wav(4.0), "text/plain").status_code == 415

    def test_it_is_slowed_down_when_tried_too_fast(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        codes = [match(web, make_wav(1.0)).status_code for _ in range(22)]
        assert codes[:20] == [422] * 20
        assert 429 in codes[20:]
