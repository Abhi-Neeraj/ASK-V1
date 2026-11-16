"""Speaker separation (who spoke when) and speaker matching (which enrolled person that voice is), with the
real speaker model on real audio.

Two things are kept apart on purpose:

* separation gives anonymous turns (SPEAKER_00, SPEAKER_01). It never knows a name.
* matching compares a turn's voice with the voice prints of people in the SAME company whose consent is on
  record, and names a turn only above a threshold and clear of the runner-up. Otherwise: Unknown speaker.

The voices are two synthetic text-to-speech voices. Telling two of them apart is an easy case, so these tests
prove the machinery and the safeguards, not accuracy on real people. See tests/real/conftest.py to run them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.registry import ProviderHolder
from app.ai.types import Embedding, Provider, Turn
from app.models import Company, Person, VoiceProfile
from app.worker import Worker
from tests.conftest import new_web, provision_device
from tests.helpers import add_person


def turns_of(provider: Provider, audio: Path) -> list[Turn]:
    assert provider.diarizer is not None, "no speaker separation is configured for the real-model tests"
    return list(provider.diarizer.diarize(audio).turns)


def known_turns(samples: Path) -> list[dict]:  # type: ignore[type-arg]
    return json.loads((samples / "manifest.json").read_text())["conversation"]["turns"]


def purity(turns: list[Turn], truth: list[dict]) -> float:  # type: ignore[type-arg]
    """Of the time each known speaker was talking, the share that ended up under that speaker's one label.

    For every real speaker take the label that covers most of their speech, and count how much of all the
    speech lands under the right label. 1.0 is perfect; two speakers merged into one label scores 0.5.
    """
    best: dict[str, dict[str, int]] = {}
    total = 0
    for turn in truth:
        for found in turns:
            overlap = min(turn["endMs"], found.end_ms) - max(turn["startMs"], found.start_ms)
            if overlap > 0:
                best.setdefault(turn["speaker"], {}).setdefault(found.speaker, 0)
                best[turn["speaker"]][found.speaker] += overlap
        total += turn["endMs"] - turn["startMs"]
    correct = sum(max(by_label.values()) for by_label in best.values())
    return correct / max(1, total)


class TestSeparation:
    def test_two_voices_taking_turns_become_two_labelled_speakers_at_the_right_times(
        self, real_provider: Provider, samples: Path
    ) -> None:
        turns = turns_of(real_provider, samples / "conversation.wav")
        truth = known_turns(samples)
        labels = {t.speaker for t in turns}
        print(
            f"\n  conversation: {len(turns)} turns, labels {sorted(labels)}, "
            f"purity {purity(turns, truth):.0%}"
        )
        assert len(labels) == 2
        assert purity(turns, truth) >= 0.9
        # Each voice has one label throughout, and the two voices have different labels.
        by_voice: dict[str, set[str]] = {}
        for real in truth:
            middle = (real["startMs"] + real["endMs"]) // 2
            covering = [t.speaker for t in turns if t.start_ms <= middle < t.end_ms]
            assert covering, real
            by_voice.setdefault(real["speaker"], set()).update(covering)
        assert all(len(found) == 1 for found in by_voice.values()), by_voice
        assert len({next(iter(found)) for found in by_voice.values()}) == 2

    def test_turn_boundaries_are_close_to_where_the_voice_changed(
        self, real_provider: Provider, samples: Path
    ) -> None:
        turns = turns_of(real_provider, samples / "conversation.wav")
        starts = sorted(t.start_ms for t in turns)
        for real in known_turns(samples):
            assert min(abs(real["startMs"] - s) for s in starts) <= 1200, real

    def test_one_voice_alone_is_not_split_into_two_speakers(
        self, real_provider: Provider, samples: Path
    ) -> None:
        for name in ("single-hazel.wav", "single-zira.wav"):
            labels = {t.speaker for t in turns_of(real_provider, samples / name)}
            assert len(labels) == 1, (name, labels)

    def test_noise_added_to_a_conversation_still_separates_the_voices(
        self, real_provider: Provider, samples: Path
    ) -> None:
        turns = turns_of(real_provider, samples / "conversation-noisy.wav")
        score = purity(turns, known_turns(samples))
        print(f"\n  noisy conversation (about 10 dB signal to noise): purity {score:.0%}")
        assert score >= 0.8

    def test_silence_and_faint_noise_have_no_speakers(self, real_provider: Provider, samples: Path) -> None:
        for name in ("silence.wav", "quiet-noise.wav"):
            assert turns_of(real_provider, samples / name) == [], name

    def test_a_very_short_clip_is_at_most_one_speaker(self, real_provider: Provider, samples: Path) -> None:
        assert len({t.speaker for t in turns_of(real_provider, samples / "short.wav")}) <= 1

    def test_overlapping_voices_do_not_crash_and_are_not_hidden(
        self, real_provider: Provider, samples: Path
    ) -> None:
        """This separator does not handle two people at once. It must still return something sensible."""
        turns = turns_of(real_provider, samples / "overlap.wav")
        print(f"\n  overlap: {[(t.start_ms, t.end_ms, t.speaker) for t in turns]}")
        assert turns and all(t.end_ms > t.start_ms >= 0 for t in turns)

    def test_the_separator_says_it_is_experimental_when_it_is(self, real_provider: Provider) -> None:
        assert real_provider.diarizer is not None
        if "clustering" in str(getattr(real_provider.diarizer, "model", "")):
            assert any("EXPERIMENTAL" in note for note in real_provider.notes)


# --- naming speakers, through the whole system --------------------------------------------------------------


@pytest.fixture
def use_real_models(app, real_provider: Provider) -> Provider:  # type: ignore[no-untyped-def]
    """Let the API enroll voices with the real speaker model (loaded once, shared with the worker)."""
    app.state.ai = ProviderHolder(app.state.settings, real_provider)
    return real_provider


def sign_in(app, email: str = "manager@acme.example.com"):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login(email)
    return web


def enroll(web, db: Session, company: Company, name: str, samples: Path, files: list[str]) -> Person:  # type: ignore[no-untyped-def]
    person = add_person(db, company, name)  # consent granted
    for file in files:
        sent = web.post(
            f"/api/people/{person.id}/enroll",
            content=(samples / file).read_bytes(),
            headers={"Content-Type": "audio/wav"},
        )
        assert sent.status_code == 200, sent.text
    return person


ENROLLMENT = {
    "Hazel": [f"voices/hazel/enroll-{n}.wav" for n in (1, 2, 3)],
    "Zira": [f"voices/zira/enroll-{n}.wav" for n in (1, 2, 3)],
}


def process(app, real_provider: Provider, web, samples: Path, name: str = "conversation.wav") -> dict:  # type: ignore[no-untyped-def,type-arg]
    _device, locket = provision_device(app, web, f"Locket {uuid.uuid4().hex[:4]}")
    stored = locket.upload(f"clip-{uuid.uuid4().hex[:12]}", (samples / name).read_bytes())
    assert stored.status_code == 201, stored.text
    recording_id = stored.json()["recordingId"]
    worker = Worker(app.state.session_factory, app.state.settings, real_provider, app.state.storage, "w")
    assert worker.work_once() is True
    return web.get(f"/api/recordings/{recording_id}").json()


def who_said(detail: dict, phrase: str) -> str | None:  # type: ignore[type-arg]
    lines = [s for s in detail["transcript"]["segments"] if phrase in s["text"].lower()]
    assert lines, (phrase, [s["text"] for s in detail["transcript"]["segments"]])
    return lines[0]["person"]["name"] if lines[0]["person"] else None


class TestNamingSpeakers:
    def test_enrolled_people_are_named_and_the_turns_stay_anonymous_until_they_are(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        for name, files in ENROLLMENT.items():
            enroll(web, db, acme["company"], name, samples, files)
        detail = process(app, use_real_models, web, samples)
        assert detail["placeholder"] is False
        segments = detail["transcript"]["segments"]
        print(
            "\n  "
            + "\n  ".join(
                f"{s['speakerLabel']} {s['person'] and s['person']['name']} {s['confidence']} | {s['text']}"
                for s in segments
            )
        )
        assert who_said(detail, "delivery") == "Hazel"
        assert who_said(detail, "expense") == "Zira"
        assert who_said(detail, "entrance") == "Hazel"
        assert who_said(detail, "handouts") == "Zira"
        assert all(s["person"] is None or 0 <= s["confidence"] <= 1 for s in segments)
        # Who said it is a voice match, and the transcript says which separator and model were used.
        assert detail["transcript"]["diarizationModel"]

    def test_a_voice_nobody_enrolled_stays_unknown_while_the_enrolled_one_is_named(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        enroll(web, db, acme["company"], "Zira", samples, ENROLLMENT["Zira"])  # Hazel is not enrolled
        detail = process(app, use_real_models, web, samples)
        assert who_said(detail, "expense") == "Zira"
        assert who_said(detail, "delivery") is None  # Unknown speaker, not a guess
        assert who_said(detail, "entrance") is None

    def test_one_sample_is_not_enough_to_be_named(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        enroll(web, db, acme["company"], "Hazel", samples, ENROLLMENT["Hazel"][:1])
        detail = process(app, use_real_models, web, samples)
        assert who_said(detail, "delivery") is None

    def test_a_threshold_that_is_not_reached_names_nobody(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        for name, files in ENROLLMENT.items():
            enroll(web, db, acme["company"], name, samples, files)
        app.state.settings.speaker_match_threshold = 0.999  # no real voice print is this close to another's
        detail = process(app, use_real_models, web, samples)
        assert all(s["person"] is None for s in detail["transcript"]["segments"])

    def test_a_speaker_with_too_little_speech_is_not_named(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        enroll(web, db, acme["company"], "Zira", samples, ENROLLMENT["Zira"])
        detail = process(app, use_real_models, web, samples, "short.wav")  # Zira's voice, about 2 seconds
        assert all(s["person"] is None for s in detail["transcript"]["segments"])

    def test_another_companys_voices_are_never_used(
        self, app, db: Session, acme, globex, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        # Globex enrolls both voices. Acme records the same two voices and must not get Globex's names.
        other = new_web(app)
        other.login("admin@globex.example.com")
        for name, files in ENROLLMENT.items():
            enroll(other, db, globex["company"], f"Globex {name}", samples, files)
        detail = process(app, use_real_models, sign_in(app), samples)
        assert all(s["person"] is None for s in detail["transcript"]["segments"])
        assert db.scalar(select(func.count()).select_from(VoiceProfile)) == 2  # theirs are intact

    def test_withdrawing_consent_before_processing_means_no_name_and_no_voice_print(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        hazel = enroll(web, db, acme["company"], "Hazel", samples, ENROLLMENT["Hazel"])
        assert web.post(f"/api/people/{hazel.id}/consent", json={"granted": False}).status_code == 200
        db.expire_all()
        assert db.scalar(select(func.count()).select_from(VoiceProfile)) == 0  # the print is gone at once
        detail = process(app, use_real_models, web, samples)
        assert who_said(detail, "delivery") is None

    def test_consent_withdrawn_while_the_recording_is_being_processed_still_means_no_name(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        people = {n: enroll(web, db, acme["company"], n, samples, f) for n, f in ENROLLMENT.items()}
        real = use_real_models.embedder
        assert real is not None

        class WithdrawsAfterMatching:
            """The real speaker model, but Hazel withdraws consent just after her voice has been matched."""

            model = real.model

            def __init__(self) -> None:
                self.done = False

            def embed(self, audio: Path, spans=None) -> Embedding:  # type: ignore[no-untyped-def]
                result = real.embed(audio, spans)
                if not self.done:
                    self.done = True
                    response = web.post(f"/api/people/{people['Hazel'].id}/consent", json={"granted": False})
                    assert response.status_code == 200
                return result

        racing = Provider(
            name=use_real_models.name,
            placeholder=False,
            transcriber=use_real_models.transcriber,
            diarizer=use_real_models.diarizer,
            embedder=WithdrawsAfterMatching(),
        )
        detail = process(app, racing, web, samples)
        assert (
            who_said(detail, "delivery") is None
        )  # Hazel withdrew: she is not named, though she was matched
        assert who_said(detail, "expense") == "Zira"  # nobody else is affected

    def test_removing_a_voice_and_enrolling_again_changes_who_can_be_named(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        zira = enroll(web, db, acme["company"], "Zira", samples, ENROLLMENT["Zira"])
        assert who_said(process(app, use_real_models, web, samples), "expense") == "Zira"

        assert web.delete(f"/api/people/{zira.id}/voice-profile").status_code == 204
        assert who_said(process(app, use_real_models, web, samples), "expense") is None

        for file in ENROLLMENT["Zira"]:
            assert (
                web.post(
                    f"/api/people/{zira.id}/enroll",
                    content=(samples / file).read_bytes(),
                    headers={"Content-Type": "audio/wav"},
                ).status_code
                == 200
            )
        assert who_said(process(app, use_real_models, web, samples), "expense") == "Zira"

    def test_a_person_without_recorded_consent_is_never_a_candidate(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        zira = enroll(web, db, acme["company"], "Zira", samples, ENROLLMENT["Zira"])
        zira.consent_status = (
            "pending"  # as if it had never been recorded: the print exists, the consent does not
        )
        db.commit()
        assert who_said(process(app, use_real_models, web, samples), "expense") is None

    def test_voice_prints_never_leave_the_server(
        self, app, db: Session, acme, use_real_models: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = sign_in(app)
        zira = enroll(web, db, acme["company"], "Zira", samples, ENROLLMENT["Zira"])
        listing = web.get("/api/people")
        assert listing.status_code == 200 and listing.json()
        everything = json.dumps(listing.json()).lower()
        assert "embedding" not in everything and "voiceprint" not in everything and "vector" not in everything
        assert str(zira.id) in everything  # the person is listed; only the voice print is never shown
