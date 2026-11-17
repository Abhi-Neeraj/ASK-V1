"""The whole voice-to-summary chain through the real worker:

    audio of several voices -> pitch-based separation -> pitch and voice signature per speaker -> matched to
    the people who enrolled a signature -> speaker-labelled transcript -> sentence analysis -> summary

Speech recognition is a test double that returns known sentences with known times (the real model is tested
elsewhere); everything else is the real code on made-up voices with known pitch.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.local import PitchDiarizer
from app.ai.types import AsrSegment
from app.models import AuditEvent, PersonSignature, RecordingAnalysis, Transcript
from app.services import insights
from app.worker import Worker
from tests.ai_doubles import FakeDiarizer, FakeEmbedder, FakeTranscriber, make_provider
from tests.conftest import new_web, provision_device
from tests.helpers import add_person
from tests.test_processing import reload, segments_of, states_of
from tests.voice_synth import HIGH_FEMALE, LOW_MALE, Voice, conversation, to_wav_bytes

SENTENCES = [
    "Good morning everyone, let us go through the agenda.",
    "Could you confirm whether the delivery arrives on Monday or on Tuesday morning?",
    "It arrives on Tuesday. I will send the revised budget to everyone before the end of the day tomorrow.",
    "Please remember to submit your expense reports before the end of the month.",
]
# Who speaks each sentence: the low voice, the high voice, the low voice, the high voice.
VOICES = [LOW_MALE, HIGH_FEMALE, LOW_MALE, HIGH_FEMALE]


def manager_web(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def talk(voices: list[Voice] = VOICES, seconds: float = 6.0):  # type: ignore[no-untyped-def]
    audio, spans = conversation([(v, seconds, 10 + i) for i, v in enumerate(voices)])
    return to_wav_bytes(audio), spans


def upload_audio(app, body: bytes):  # type: ignore[no-untyped-def]
    web = manager_web(app)
    _, locket = provision_device(app, web, f"Locket {uuid.uuid4().hex[:4]}")
    stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", body)
    assert stored.status_code == 201, stored.text
    return uuid.UUID(stored.json()["recordingId"]), web


def transcriber_for(spans, sentences=SENTENCES) -> FakeTranscriber:  # type: ignore[no-untyped-def]
    return FakeTranscriber(
        [
            AsrSegment(start + 300, end - 300, text, (), 0.95)
            for (start, end, _), text in zip(spans, sentences, strict=False)
        ]
    )


def worker_for(app, provider) -> Worker:  # type: ignore[no-untyped-def]
    return Worker(app.state.session_factory, app.state.settings, provider, app.state.storage, "worker-v")


def enroll_signature(web, person, voice: Voice, count: int = 3) -> None:  # type: ignore[no-untyped-def]
    from tests.voice_synth import speak

    for n in range(count):
        response = web.post(
            f"/api/people/{person.id}/signature",
            content=to_wav_bytes(speak(voice, 6.0, seed=100 + n)),
            headers={"Content-Type": "audio/wav"},
        )
        assert response.status_code == 200, response.text


def fake_turns(spans):  # type: ignore[no-untyped-def]
    from app.ai.types import Turn

    return [Turn(start, end, f"SPEAKER_{i % 2:02d}") for i, (start, end, _) in enumerate(spans)]


def stored_analysis(db: Session, recording_id: uuid.UUID) -> RecordingAnalysis | None:
    db.expire_all()
    return db.scalar(
        select(RecordingAnalysis)
        .join(Transcript, Transcript.id == RecordingAnalysis.transcript_id)
        .where(Transcript.recording_id == recording_id)
    )


class TestNamingSpeakersByVoiceSignature:
    def setup(self, app, db: Session, acme):  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        lena = add_person(db, acme["company"], "Lena Low")
        hana = add_person(db, acme["company"], "Hana High")
        enroll_signature(web, lena, LOW_MALE)
        enroll_signature(web, hana, HIGH_FEMALE)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))
        return recording_id, web, lena, hana, provider

    def test_each_voice_is_named_by_its_signature_and_the_stage_is_announced(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, _web, lena, hana, provider = self.setup(app, db, acme)

        assert worker_for(app, provider).run(once=True) == 1

        assert states_of(db, recording_id) == [
            "validating",
            "transcribing",
            "diarizing",
            "identifying_speakers",  # there is no speaker model here: the signature did the naming
            "aligning",
            "completed",
        ]
        lines = segments_of(db, recording_id)
        assert [s.person_id for s in lines] == [lena.id, hana.id, lena.id, hana.id]
        assert all(s.confidence is not None and 0.5 <= s.confidence <= 1.0 for s in lines)

    def test_labels_that_are_not_one_voice_each_are_never_named(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        """Google labels speakers within each 55-second piece only. When the labels are not linked, a label
        may hold several people's speech, so matching it to an enrolled voice could name the wrong person: the
        speakers stay "Speaker N" even though the same enrolled voices named everyone in the test above."""
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        enroll_signature(web, add_person(db, acme["company"], "Lena Low"), LOW_MALE)
        enroll_signature(web, add_person(db, acme["company"], "Hana High"), HIGH_FEMALE)
        provider = make_provider(
            transcriber=transcriber_for(spans),
            diarizer=FakeDiarizer(fake_turns(spans), labels_linked=False),
        )

        assert worker_for(app, provider).run(once=True) == 1

        assert "identifying_speakers" not in states_of(db, recording_id)
        assert all(s.person_id is None and s.confidence is None for s in segments_of(db, recording_id))
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "recording.processed"))
        assert event is not None
        assert event.detail["speakerLabelsLinked"] is False and event.detail["speakersNamed"] == 0

    def test_a_voice_matching_nobody_stays_unknown(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        stranger = Voice(f0=170.0, formants=(430.0, 2300.0, 3400.0), bandwidth=(160.0, 220.0, 300.0))
        body, spans = talk([stranger, stranger, stranger, stranger])
        recording_id, web = upload_audio(app, body)
        enroll_signature(web, add_person(db, acme["company"], "Lena Low"), LOW_MALE)
        enroll_signature(web, add_person(db, acme["company"], "Hana High"), HIGH_FEMALE)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))

        worker_for(app, provider).run(once=True)

        assert all(s.person_id is None and s.confidence is None for s in segments_of(db, recording_id))

    def test_the_speaker_model_keeps_priority_and_the_signature_only_fills_the_gaps(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        from tests.test_processing import enroll as enroll_voiceprint

        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        ana = add_person(db, acme["company"], "Ana")
        enroll_voiceprint(db, ana, (1, 0, 0))  # the speaker model says the first voice is Ana
        hana = add_person(db, acme["company"], "Hana High")
        enroll_signature(web, hana, HIGH_FEMALE)
        provider = make_provider(
            transcriber=transcriber_for(spans),
            diarizer=FakeDiarizer(fake_turns(spans)),
            embedder=FakeEmbedder({spans[0][0]: (1, 0, 0), spans[1][0]: (0, 1, 0)}),
        )

        worker_for(app, provider).run(once=True)

        first, second, _third, _fourth = segments_of(db, recording_id)
        assert first.person_id == ana.id  # named by the speaker model
        assert second.person_id == hana.id  # left over, named by its signature
        analysis = stored_analysis(db, recording_id)
        assert analysis is not None
        by_label = {v["label"]: v for v in analysis.content["voices"]}
        assert by_label["SPEAKER_00"]["match"]["method"] == "voice_model"
        assert by_label["SPEAKER_01"]["match"]["method"] == "signature"

    def test_a_signature_needs_enough_samples_and_consent_before_it_can_name_anyone(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        too_few = add_person(db, acme["company"], "Few Samples")
        enroll_signature(web, too_few, LOW_MALE, count=2)
        pending = add_person(db, acme["company"], "No Consent")
        enroll_signature(web, pending, HIGH_FEMALE)
        web.post(f"/api/people/{pending.id}/consent", json={"granted": False})  # withdrawn: signature goes
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))

        worker_for(app, provider).run(once=True)

        assert "identifying_speakers" not in states_of(db, recording_id)  # nobody is eligible: no stage
        assert all(s.person_id is None for s in segments_of(db, recording_id))

    def test_consent_withdrawn_while_it_was_processing_means_no_name(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, web, _lena, hana, _ = self.setup(app, db, acme)
        _body, spans = talk()

        class Withdrawn(FakeTranscriber):
            def transcribe(self, audio: Path):  # type: ignore[no-untyped-def]
                done = super().transcribe(audio)
                # Hana withdraws consent while the recording is being processed, before the result is saved.
                web.post(f"/api/people/{hana.id}/consent", json={"granted": False})
                return done

        provider = make_provider(
            transcriber=Withdrawn(transcriber_for(spans).segments), diarizer=FakeDiarizer(fake_turns(spans))
        )
        worker_for(app, provider).run(once=True)

        lines = segments_of(db, recording_id)
        assert hana.id not in {s.person_id for s in lines}


class TestWhatIsStored:
    def test_the_analysis_holds_pitch_talk_time_and_the_sentences_without_any_signature(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        lena = add_person(db, acme["company"], "Lena Low")
        enroll_signature(web, lena, LOW_MALE)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))

        worker_for(app, provider).run(once=True)

        analysis = stored_analysis(db, recording_id)
        assert analysis is not None and analysis.engine == "lexical"
        content = analysis.content
        voices = {v["label"]: v for v in content["voices"]}
        assert voices["SPEAKER_00"]["pitch"]["baseHz"] == pytest.approx(LOW_MALE.f0, rel=0.07)
        assert voices["SPEAKER_01"]["pitch"]["baseHz"] == pytest.approx(HIGH_FEMALE.f0, rel=0.07)
        assert (
            voices["SPEAKER_00"]["pitch"]["lowHz"]
            < voices["SPEAKER_00"]["pitch"]["baseHz"]
            < voices["SPEAKER_00"]["pitch"]["highHz"]
        )
        assert voices["SPEAKER_00"]["talkMs"] > 0 and voices["SPEAKER_00"]["turns"] == 2
        assert len(content["items"]) == 5  # the third line holds two sentences
        flat = str(content)
        # How a name was found is recorded ('method': 'signature'), but no measurement of the voice itself is.
        assert "timbre" not in flat and "'signature':" not in flat and "'embedding'" not in flat
        assert "Lena" not in flat  # no name is stored: it is looked up when read

    def test_speakers_with_too_little_speech_still_get_a_pitch_but_no_name(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk([LOW_MALE, HIGH_FEMALE], seconds=2.0)
        recording_id, web = upload_audio(app, body)
        enroll_signature(web, add_person(db, acme["company"], "Hana High"), HIGH_FEMALE)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))

        worker_for(app, provider).run(once=True)

        assert all(
            s.person_id is None for s in segments_of(db, recording_id)
        )  # under the three-second minimum

    def test_a_placeholder_transcript_gets_no_summary(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        provider = make_provider(transcriber=transcriber_for(spans), placeholder=True)

        worker_for(app, provider).run(once=True)

        assert stored_analysis(db, recording_id) is None
        assert web.get(f"/api/recordings/{recording_id}").json()["analysis"] is None

    def test_a_summary_that_cannot_be_made_never_fails_the_recording(
        self, app, db: Session, acme, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, _ = upload_audio(app, body)
        monkeypatch.setattr(
            insights, "classifier_for", lambda settings: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))

        with caplog.at_level(logging.ERROR, logger="ask.pipeline"):
            worker_for(app, provider).run(once=True)

        recording, job = reload(db, recording_id)
        assert (recording.state, job.status) == ("completed", "done")
        assert len(segments_of(db, recording_id)) == 4  # the transcript is saved
        assert stored_analysis(db, recording_id) is None
        assert "could not analyse recording" in caplog.text

    def test_processing_again_replaces_the_analysis_instead_of_adding_a_second(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))
        worker_for(app, provider).run(once=True)
        first = stored_analysis(db, recording_id)
        assert first is not None
        first_id = first.id
        assert web.post(f"/api/recordings/{recording_id}/reprocess").status_code == 202
        from tests.test_processing import make_due

        make_due(db, recording_id)
        worker_for(app, provider).run(once=True)

        db.expire_all()
        assert len(list(db.scalars(select(RecordingAnalysis)))) == 1
        second = stored_analysis(db, recording_id)
        assert second is not None and second.id != first_id

    def test_the_audit_trail_does_not_carry_what_was_said_or_measured(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        _recording_id, _ = upload_audio(app, body)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))
        worker_for(app, provider).run(once=True)
        db.expire_all()
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "recording.processed"))
        assert event is not None
        assert "expense" not in str(event.detail) and "Hz" not in str(event.detail)


class TestWhatThePageIsTold:
    def run(self, app, db: Session, acme):  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        lena = add_person(db, acme["company"], "Lena Low")
        hana = add_person(db, acme["company"], "Hana High")
        enroll_signature(web, lena, LOW_MALE)
        enroll_signature(web, hana, HIGH_FEMALE)
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))
        worker_for(app, provider).run(once=True)
        return recording_id, web, lena, hana

    def test_the_detail_carries_voices_a_summary_and_every_sentence(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, web, _lena, _hana = self.run(app, db, acme)
        detail = web.get(f"/api/recordings/{recording_id}").json()
        analysis = detail["analysis"]

        assert analysis["engine"] == "lexical"
        titles = {v["name"]: v for v in analysis["voices"]}
        assert set(titles) == {"Lena Low", "Hana High"}
        assert titles["Lena Low"]["matchedBy"] == "signature"
        assert titles["Lena Low"]["pitch"]["baseHz"] == pytest.approx(LOW_MALE.f0, rel=0.07)
        assert titles["Hana High"]["pitch"]["baseHz"] > titles["Lena Low"]["pitch"]["baseHz"]
        assert abs(sum(v["share"] for v in analysis["voices"]) - 1.0) < 0.01

        text = " ".join(analysis["summary"]["narrative"])
        assert "Lena Low" in text and "Hana High" in text  # named speakers appear in the overview
        assert "before the end of the day tomorrow" in text  # the due phrase of the commitment
        first = analysis["items"][0]
        assert set(first) >= {
            "what",
            "context",
            "activity",
            "activityLabel",
            "task",
            "owner",
            "due",
            "speaker",
        }
        assert analysis["counts"]["COMMITMENT"] == 1

    def test_every_sentence_carries_its_verb_keywords_and_whether_it_is_a_dated_task(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, web, _lena, _hana = self.run(app, db, acme)
        items = web.get(f"/api/recordings/{recording_id}").json()["analysis"]["items"]
        commitment = next(i for i in items if i["activity"] == "COMMITMENT")
        assert commitment["verb"] == "send" and commitment["dated"] is True
        assert commitment["due"] == "before the end of the day tomorrow"
        assert all(isinstance(i["keywords"], list) and len(i["keywords"]) <= 3 for i in items)
        question = next(i for i in items if i["activity"] == "QUESTION")
        assert question["dated"] is False and question["verb"] is None
        narrative = " ".join(
            web.get(f"/api/recordings/{recording_id}").json()["analysis"]["summary"]["narrative"]
        )
        assert re.search(r"\d of the \d tasks? (has|have) a deadline\.", narrative)

    def test_with_the_words_engine_no_topics_are_claimed_to_the_page(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        recording_id, web, _lena, _hana = self.run(app, db, acme)
        analysis = web.get(f"/api/recordings/{recording_id}").json()["analysis"]
        assert analysis["contexts"] == [] and analysis["contextThreshold"] is None
        assert all(i["context"]["group"] is None for i in analysis["items"])
        assert "topics" not in " ".join(analysis["summary"]["narrative"])

    def test_asking_for_a_context_threshold_works_the_topics_out_and_lists_them(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        app.state.settings = app.state.settings.model_copy(update={"context_similarity": 0.05})
        recording_id, web, _lena, _hana = self.run(app, db, acme)
        analysis = web.get(f"/api/recordings/{recording_id}").json()["analysis"]
        assert analysis["contextThreshold"] == 0.05
        assert analysis["contexts"] and analysis["contexts"][0]["group"] == 0
        assert all(i["context"]["group"] is not None for i in analysis["items"])
        first = analysis["items"][0]["context"]
        assert first["newContext"] is True and first["similarity"] is None  # nothing came before it
        assert sum(c["sentences"] for c in analysis["contexts"]) == len(analysis["items"])

    def test_a_correction_keeps_the_context_setting(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        app.state.settings = app.state.settings.model_copy(update={"context_similarity": 0.05})
        recording_id, web, _lena, _hana = self.run(app, db, acme)
        segment = web.get(f"/api/recordings/{recording_id}").json()["transcript"]["segments"][3]
        changed = web.patch(
            f"/api/recordings/{recording_id}/transcript",
            json={"edits": [{"segmentId": segment["id"], "text": "Priya will finish the budget by Friday."}]},
        )
        analysis = changed.json()["analysis"]
        assert analysis["contextThreshold"] == 0.05 and analysis["contexts"]
        done = next(i for i in analysis["items"] if "finish the budget" in i["what"])
        assert done["activity"] == "COMMITMENT" and done["dated"] is True and done["verb"] == "finish"

    def test_only_managers_see_how_alike_the_voice_was(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        from app.models import Recording
        from app.services import recordings as svc

        recording_id, web, *_ = self.run(app, db, acme)
        manager = {
            v["name"]: v for v in web.get(f"/api/recordings/{recording_id}").json()["analysis"]["voices"]
        }
        assert (
            manager["Lena Low"]["matchScore"] is not None and 0.5 <= manager["Lena Low"]["matchScore"] <= 1.0
        )

        # What anyone who is not a manager is given (an employee reaches the same service with can_manage
        # off).
        db.expire_all()
        recording = db.get(Recording, recording_id)
        assert recording is not None
        detail = svc.recording_detail(db, recording, can_manage=False)
        assert detail.analysis is not None
        assert {v.name for v in detail.analysis.voices} == {"Lena Low", "Hana High"}
        assert all(v.match_score is None for v in detail.analysis.voices)

    def test_withdrawing_consent_takes_the_name_out_of_the_summary_at_once(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, web, _lena, hana = self.run(app, db, acme)

        web.post(f"/api/people/{hana.id}/consent", json={"granted": False})

        analysis = web.get(f"/api/recordings/{recording_id}").json()["analysis"]
        names = {v["name"] for v in analysis["voices"]}
        assert names == {"Lena Low", None}
        gone = next(v for v in analysis["voices"] if v["name"] is None)
        assert (
            gone["title"].startswith("Speaker ") and gone["matchedBy"] is None and gone["matchScore"] is None
        )
        assert "Hana" not in str(analysis)

    def test_correcting_the_transcript_rebuilds_the_summary_from_the_corrected_words(
        self, app, db: Session, acme
    ) -> None:  # type: ignore[no-untyped-def]
        recording_id, web, *_ = self.run(app, db, acme)
        detail = web.get(f"/api/recordings/{recording_id}").json()
        segment = detail["transcript"]["segments"][3]
        assert "expense reports" in segment["text"]

        changed = web.patch(
            f"/api/recordings/{recording_id}/transcript",
            json={"edits": [{"segmentId": segment["id"], "text": "We decided to cancel the offsite."}]},
        )

        assert changed.status_code == 200, changed.text
        analysis = changed.json()["analysis"]
        assert analysis["counts"]["DECISION"] == 1
        assert any(
            "cancel the offsite" in line["text"]
            for s in analysis["summary"]["sections"]
            for line in s["lines"]
        )
        assert "expense reports" not in str(analysis)

    def test_another_company_cannot_see_the_summary(self, app, db: Session, acme, globex) -> None:  # type: ignore[no-untyped-def]
        recording_id, *_ = self.run(app, db, acme)
        other = new_web(app)
        assert other.login("admin@globex.example.com").status_code == 200
        assert other.get(f"/api/recordings/{recording_id}").status_code == 404


class TestSeparatingByPitchEndToEnd:
    """The real pitch separator in the real worker: no diarization model, no speaker model, no token."""

    def test_two_voices_become_two_labelled_speakers_and_are_named(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk()
        recording_id, web = upload_audio(app, body)
        lena = add_person(db, acme["company"], "Lena Low")
        hana = add_person(db, acme["company"], "Hana High")
        enroll_signature(web, lena, LOW_MALE)
        enroll_signature(web, hana, HIGH_FEMALE)
        settings = app.state.settings.model_copy(update={"ai_diarizer": "pitch"})
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=PitchDiarizer(settings))

        worker_for(app, provider).run(once=True)

        lines = segments_of(db, recording_id)
        assert [s.speaker_label for s in lines] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00", "SPEAKER_01"]
        assert [s.person_id for s in lines] == [lena.id, hana.id, lena.id, hana.id]
        detail = web.get(f"/api/recordings/{recording_id}").json()
        assert detail["transcript"]["separationExperimental"] is True  # said so on the screen
        assert detail["analysis"]["voices"][0]["pitch"] is not None

    def test_one_voice_stays_one_speaker(self, app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
        body, spans = talk([LOW_MALE, LOW_MALE, LOW_MALE, LOW_MALE])
        recording_id, _ = upload_audio(app, body)
        settings = app.state.settings.model_copy(update={"ai_diarizer": "pitch"})
        provider = make_provider(transcriber=transcriber_for(spans), diarizer=PitchDiarizer(settings))

        worker_for(app, provider).run(once=True)

        assert {s.speaker_label for s in segments_of(db, recording_id)} == {"SPEAKER_00"}


def test_signatures_are_not_touched_by_processing(app, db: Session, acme) -> None:  # type: ignore[no-untyped-def]
    body, spans = talk()
    _recording_id, web = upload_audio(app, body)
    lena = add_person(db, acme["company"], "Lena Low")
    enroll_signature(web, lena, LOW_MALE)
    before = db.scalar(select(PersonSignature).where(PersonSignature.person_id == lena.id))
    assert before is not None
    timbre, count = before.timbre, before.sample_count
    provider = make_provider(transcriber=transcriber_for(spans), diarizer=FakeDiarizer(fake_turns(spans)))

    worker_for(app, provider).run(once=True)

    db.expire_all()
    after = db.scalar(select(PersonSignature).where(PersonSignature.person_id == lena.id))
    assert after is not None and (after.timbre, after.sample_count) == (
        timbre,
        count,
    )  # matching never learns
