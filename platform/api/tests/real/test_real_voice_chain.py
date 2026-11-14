"""The whole voice-to-summary chain on REAL speech audio with the REAL recognizer.

    two Windows text-to-speech voices talking -> real speech recognition -> separation by pitch (no model)
    -> pitch and voice signature per speaker -> matched to people who enrolled signatures from other sentences
    -> speaker-labelled transcript -> rule-based summary

The voices are synthetic and there are only two, of similar pitch, so this proves the chain works on real
speech signals; it says nothing about accuracy on real people, rooms or microphones. See
tests/real/conftest.py to run it (ASK_REAL_MODELS=1).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.local import PitchDiarizer
from app.ai.types import Provider
from app.config import Settings
from app.models import Person
from app.worker import Worker
from tests.conftest import new_web, provision_device
from tests.helpers import add_person
from tests.test_processing import segments_of


def pitch_provider(real_provider: Provider, settings: Settings) -> Provider:
    """The real recognizer and speaker model, with separation done by pitch instead."""
    return Provider(
        name=real_provider.name,
        placeholder=False,
        transcriber=real_provider.transcriber,
        diarizer=PitchDiarizer(settings.model_copy(update={"ai_diarizer": "pitch"})),
        embedder=real_provider.embedder,
        notes=real_provider.notes,
    )


def manager_web(app):  # type: ignore[no-untyped-def]
    web = new_web(app)
    web.login("manager@acme.example.com")
    return web


def enroll(web, person: Person, folder: Path) -> None:  # type: ignore[no-untyped-def]
    for sample in sorted(folder.glob("enroll-*.wav")):
        response = web.post(
            f"/api/people/{person.id}/signature",
            content=sample.read_bytes(),
            headers={"Content-Type": "audio/wav"},
        )
        assert response.status_code == 200, response.text


class TestTheChainOnRealSpeech:
    def run_conversation(self, app, db: Session, acme, real_provider: Provider, samples: Path):  # type: ignore[no-untyped-def]
        web = manager_web(app)
        hazel = add_person(db, acme["company"], "Hazel Reed")
        zira = add_person(db, acme["company"], "Zira Cole")
        # Enrolled from sentences that are NOT in the conversation.
        enroll(web, hazel, samples / "voices" / "hazel")
        enroll(web, zira, samples / "voices" / "zira")

        _, locket = provision_device(app, web, "Locket Real")
        stored = locket.upload(f"clip-{uuid.uuid4().hex[:10]}", (samples / "conversation.wav").read_bytes())
        assert stored.status_code == 201, stored.text
        recording_id = uuid.UUID(stored.json()["recordingId"])

        provider = pitch_provider(real_provider, app.state.settings)
        worker = Worker(
            app.state.session_factory, app.state.settings, provider, app.state.storage, "worker-real"
        )
        assert worker.run(once=True) == 1
        return web, recording_id, hazel, zira

    def test_each_voice_is_separated_named_and_summarised(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web, recording_id, hazel, zira = self.run_conversation(app, db, acme, real_provider, samples)

        detail = web.get(f"/api/recordings/{recording_id}").json()
        assert detail["state"] in {"completed", "needs_review"}, detail
        transcript = detail["transcript"]
        assert (
            transcript["separationExperimental"] is True
        )  # said so, because separation by pitch is experimental
        lines = segments_of(db, recording_id)
        print("\n  transcript:")
        for line in lines:
            who = db.get(Person, line.person_id).display_name if line.person_id else "Unknown speaker"  # type: ignore[union-attr]
            print(f"    {line.start_ms / 1000:5.1f}s {line.speaker_label} {who}: {line.text}")

        # Who spoke each known turn (from how the file was built), and what the system called them.
        manifest = json.loads((samples / "manifest.json").read_text())["conversation"]["turns"]
        owners = {"hazel": hazel.id, "zira": zira.id}
        wrong, unnamed, checked = 0, 0, 0
        for turn in manifest:
            middle = (turn["startMs"] + turn["endMs"]) // 2
            inside = [s for s in lines if s.start_ms - 800 <= middle <= s.end_ms + 800]
            for segment in inside:
                checked += 1
                if segment.person_id is None:
                    unnamed += 1
                elif segment.person_id != owners[turn["speaker"]]:
                    wrong += 1
        print(
            f"  checked {checked} line(s) against the known speakers: "
            f"{wrong} wrongly named, {unnamed} left unnamed"
        )
        assert checked >= 3
        assert wrong == 0, "a wrong name is worse than no name"
        assert checked - unnamed >= 2, "at least some lines should have been named by their voice signature"

        # The two voices were told apart.
        assert len({s.speaker_label for s in lines}) == 2

    def test_the_summary_finds_the_questions_tasks_and_commitments_that_were_spoken(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web, recording_id, *_ = self.run_conversation(app, db, acme, real_provider, samples)
        analysis = web.get(f"/api/recordings/{recording_id}").json()["analysis"]
        assert analysis is not None
        print("\n  summary:")
        for paragraph in analysis["summary"]["narrative"]:
            print(f"    {paragraph}")
        for item in analysis["items"]:
            print(
                f"    [{item['activityLabel']:11}] {item['speaker']}: {item['what'][:70]} | "
                f"task={item['task']} owner={item['owner']} due={item['due']}"
            )

        by_kind: dict[str, list[dict]] = {}
        for item in analysis["items"]:
            by_kind.setdefault(item["activity"], []).append(item)
        question = next(i for i in by_kind["QUESTION"] if "delivery" in i["what"].lower())
        assert question["open"] is not None
        reminder = next(i for i in analysis["items"] if "expense" in i["what"].lower())
        assert reminder["activity"] == "ACTION" and reminder["due"] and "month" in reminder["due"]
        commitment = next(i for i in by_kind["COMMITMENT"] if "handouts" in i["what"].lower())
        assert commitment["owner"] is not None and "handouts" in (commitment["task"] or "")

        # Both voices have a measured pitch, and the lower-pitched one is reported as lower.
        pitches = {v["label"]: v["pitch"] for v in analysis["voices"]}
        assert all(p is not None and p["lowHz"] < p["baseHz"] < p["highHz"] for p in pitches.values())
        assert analysis["engine"] in {"lexical", "semantic"}

    def test_the_summary_with_the_real_sentence_model_finds_the_same_things(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        """The same conversation, with the summary matched by meaning (the sentence model, offline)."""
        from app.insights.embedding import semantic_installed
        from app.services import insights as insights_service
        from tests.real.conftest import MODELS

        if not semantic_installed():
            pytest.skip("the sentence model package is not installed")
        insights_service.reset_classifiers()
        app.state.settings = app.state.settings.model_copy(
            update={"analysis_engine": "semantic", "ai_model_dir": MODELS, "ai_offline": True}
        )
        try:
            web, recording_id, *_ = self.run_conversation(app, db, acme, real_provider, samples)
        finally:
            insights_service.reset_classifiers()
        analysis = web.get(f"/api/recordings/{recording_id}").json()["analysis"]
        assert analysis is not None and analysis["engine"] == "semantic"
        print("  summary (meaning engine):")
        for item in analysis["items"]:
            print(f"    [{item['activityLabel']:11}] {item['speaker']}: {item['what'][:70]}")
        activity = {i["what"].lower(): i["activity"] for i in analysis["items"]}
        asking = next(a for w, a in activity.items() if "delivery" in w)
        reminding = next(a for w, a in activity.items() if "expense" in w)
        bringing = next(a for w, a in activity.items() if "handouts" in w)
        assert (asking, reminding, bringing) == ("QUESTION", "ACTION", "COMMITMENT")

    def test_a_voice_that_nobody_enrolled_is_left_unnamed(
        self, app, db: Session, acme, real_provider: Provider, samples: Path
    ) -> None:  # type: ignore[no-untyped-def]
        web = manager_web(app)
        add_person(db, acme["company"], "Someone Else")  # no samples: not a candidate
        _, locket = provision_device(app, web, "Locket Real 2")
        stored = locket.upload(f"clip-{uuid.uuid4().hex[:10]}", (samples / "conversation.wav").read_bytes())
        recording_id = uuid.UUID(stored.json()["recordingId"])
        provider = pitch_provider(real_provider, app.state.settings)
        Worker(
            app.state.session_factory, app.state.settings, provider, app.state.storage, "worker-real-2"
        ).run(once=True)

        assert all(s.person_id is None for s in segments_of(db, recording_id))
        assert db.scalar(select(Person).where(Person.display_name == "Someone Else")) is not None
