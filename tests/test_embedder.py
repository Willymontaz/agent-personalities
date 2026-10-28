"""The multilingual claim, checked rather than assumed.

Everything downstream rests on one property: a French sentence and its English
equivalent must land closer together than either lands to an unrelated
sentence. bge-m3 is chosen for exactly this.
"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import REPO

from memlib import embedder

PAIRS = [
    ("Je préfère des revues de code courtes", "I prefer terse code reviews"),
    ("Il déteste les réunions du lundi matin", "He hates Monday morning meetings"),
    ("On déploie le vendredi seulement si les tests passent",
     "We only deploy on Friday when the tests are green"),
]

UNRELATED = [
    "Le chat dort sur le radiateur",
    "The train to Lisbon leaves at half past six",
    "La recette demande trois oeufs et du beurre fondu",
]


@pytest.fixture(scope="module")
def vectors():
    from memlib import config as configlib

    cfg = configlib.load(REPO)
    texts = [t for pair in PAIRS for t in pair] + UNRELATED
    mat = embedder.embed(cfg, texts)
    return cfg, {t: v for t, v in zip(texts, mat)}


def test_vectors_are_normalised(vectors):
    _, vecs = vectors
    for text, vec in vecs.items():
        assert np.isclose(np.linalg.norm(vec), 1.0, atol=1e-4), text


def test_dims_match_config(vectors):
    cfg, vecs = vectors
    for vec in vecs.values():
        assert len(vec) == cfg.dims


@pytest.mark.parametrize("fr,en", PAIRS)
def test_cross_lingual_pair_beats_unrelated(vectors, fr, en):
    _, vecs = vectors
    pair_cos = float(vecs[fr] @ vecs[en])
    for other in UNRELATED:
        # both directions: FR query vs EN memory, and EN query vs FR memory
        assert pair_cos > float(vecs[fr] @ vecs[other])
        assert pair_cos > float(vecs[en] @ vecs[other])
    assert pair_cos > 0.6, f"cross-lingual similarity too low: {pair_cos:.3f}"


def test_passage_text_embeds_the_tags():
    text = embedder.passage_text("Title", ["topics/code-review", "people/alex"], "Body here.")
    assert "tags: topics/code-review, people/alex" in text
    assert text.startswith("Title\n")
    assert text.endswith("Body here.")

