"""The summary's meaning-based engine, with the REAL sentence model, run offline.

Needs the model's weights on this machine (it is never downloaded by a test): put them in ASK_AI_MODEL_DIR, or
download them once on purpose with ASK_AI_TEXT_ALLOW_DOWNLOAD=true (see platform/docs/VOICE_SIGNATURE.md).
Skipped if they are not there. The sentences below were written for this test and are not among the examples
the classifier is given, so it has to recognise what they mean.
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.insights import functions
from app.insights.embedding import HashingEmbedder, build_embedder
from app.insights.functions import Classifier
from tests.real.conftest import MODELS

PARAPHRASES = [
    ("Leave the booking with me, I'll sort it out.", functions.COMMITMENT),
    ("You can count on me to get the figures over.", functions.COMMITMENT),
    ("Somebody ought to get in touch with the supplier.", functions.ACTION),
    ("Could we find out when the parcel is due?", functions.QUESTION),
    ("Any idea what the cost comes to?", functions.QUESTION),
    ("So we're going ahead with the cheaper vendor.", functions.DECISION),
    ("The checkout keeps freezing for a few users.", functions.ISSUE),
    ("There's a chance the vendor will pull out.", functions.RISK),
    ("Fine by me, proceed.", functions.APPROVAL),
    ("We'll have to turn that proposal down.", functions.REJECTION),
    ("Clients want to be able to share their dashboards.", functions.REQUIREMENT),
    ("The weather was lovely over the weekend.", functions.NOTE),
]


@pytest.fixture(scope="module")
def semantic_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        secret_key=SecretStr("test-secret-key-0123456789-abcdefghijklmnop"),
        database_url="sqlite://",
        ai_model_dir=MODELS,
        analysis_engine="semantic",  # insist: if it cannot run offline, say so
        ai_offline=True,  # and never contact the hub
    )


@pytest.fixture(scope="module")
def semantic_classifier(semantic_settings: Settings) -> Classifier:
    try:
        embedder = build_embedder(semantic_settings)
    except Exception as error:
        pytest.skip(f"the sentence model is not available offline here: {error}")
    assert embedder.engine == "semantic"
    return Classifier(embedder)


def test_the_real_model_recognises_what_sentences_mean(semantic_classifier: Classifier) -> None:
    got = semantic_classifier.classify_many([s for s, _ in PARAPHRASES])
    right = sum(1 for (_, want), g in zip(PARAPHRASES, got, strict=True) if g.activity == want)
    words = Classifier(HashingEmbedder()).classify_many([s for s, _ in PARAPHRASES])
    words_right = sum(1 for (_, want), g in zip(PARAPHRASES, words, strict=True) if g.activity == want)
    print(f"\n  meaning engine {right}/{len(PARAPHRASES)}   words engine {words_right}/{len(PARAPHRASES)}")
    for (sentence, want), g in zip(PARAPHRASES, got, strict=True):
        if g.activity != want:
            print(f"    miss: {sentence!r} wanted {want}, got {g.activity}")
    assert right >= 9
    assert right > words_right


def test_a_wrong_activity_is_rare_even_where_it_misses(semantic_classifier: Classifier) -> None:
    """A miss should fall back to a plain note far more often than it names the wrong activity."""
    got = semantic_classifier.classify_many([s for s, _ in PARAPHRASES])
    named_wrongly = [
        s
        for (s, want), g in zip(PARAPHRASES, got, strict=True)
        if want != functions.NOTE and g.activity not in (want, functions.NOTE)
    ]
    print(f"\n  named the wrong activity: {named_wrongly}")
    assert len(named_wrongly) <= 2


def test_it_never_fetched_anything(semantic_settings: Settings) -> None:
    assert semantic_settings.ai_text_allow_download is False and semantic_settings.ai_offline is True
