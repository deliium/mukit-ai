"""Handcrafted symbolic embedding feature tests (`symbolic.features.v1`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.embeddings.errors import EmbeddingScopeError
from app.embeddings.features import (
    SYMBOLIC_FEATURES_V1_DIMS,
    embed_composition_scope,
    extract_symbolic_features_v1,
    symbolic_features_v1_dims,
)
from app.embeddings.schemas import (
    EmbedScopeBarRange,
    EmbedScopeComposition,
    EmbedScopeMotif,
    EmbedScopeSection,
)
from app.embeddings.vector import (
    cosine_similarity,
    euclidean_distance,
    feature_vector_digest,
    l2_normalize,
    l2_norm,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "embeddings"


def _load(name: str) -> CompositionV2:
    return CompositionV2.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def test_symbolic_features_dims_constant():
    assert symbolic_features_v1_dims() == SYMBOLIC_FEATURES_V1_DIMS == 81


def test_l2_normalize_and_distances():
    vector = l2_normalize([3.0, 4.0])
    assert vector == pytest.approx([0.6, 0.8])
    assert l2_norm(vector) == pytest.approx(1.0)
    assert cosine_similarity(vector, vector) == pytest.approx(1.0)
    assert euclidean_distance(vector, vector) == pytest.approx(0.0)


def test_embed_composition_stable_digest():
    composition = _load("similar_rhythm_a.json")
    first = embed_composition_scope(composition)
    second = embed_composition_scope(composition, EmbedScopeComposition())
    assert first.dims == 81
    assert first.profile_id == "symbolic.features.v1"
    assert first.artist_label_used is False
    assert first.vector == second.vector
    assert feature_vector_digest(first.vector) == "a1a3436aa9dea13584462f374cd28a0d"
    assert abs(l2_norm(first.vector) - 1.0) < 1e-6


def test_same_rhythm_nearer_than_unrelated_texture():
    """Eval example: distributional affinity — not musical quality.

    musical_quality_claim: false
    """
    emb_a = embed_composition_scope(_load("similar_rhythm_a.json"))
    emb_b = embed_composition_scope(_load("similar_rhythm_b.json"))
    emb_c = embed_composition_scope(_load("dense_texture_c.json"))
    sim_ab = cosine_similarity(emb_a.vector, emb_b.vector)
    sim_ac = cosine_similarity(emb_a.vector, emb_c.vector)
    assert sim_ab > sim_ac
    assert sim_ab > 0.85


def test_section_and_bar_range_scopes():
    composition = _load("similar_rhythm_a.json")
    section = embed_composition_scope(composition, EmbedScopeSection(section_index=0))
    bars = embed_composition_scope(
        composition, EmbedScopeBarRange(start_bar=1, end_bar=2)
    )
    assert section.note_count > 0
    assert bars.note_count > 0
    assert section.scope.kind == "section"
    assert bars.scope.kind == "bar_range"


def test_motif_scope_uses_event_ids():
    composition = _load("motif_scope.json")
    emb = embed_composition_scope(composition, EmbedScopeMotif(motif_id="motif-a"))
    assert emb.note_count == 3
    assert emb.scope.kind == "motif"


def test_empty_scope_raises():
    composition = _load("similar_rhythm_a.json")
    # Bars 3-4 have no notes in fixture A (only first 4 onsets in bars 1-ish).
    # Actually events at 0,480,960,1440 are all in bar 1 for 4/4 @ 480ppq (1920/bar).
    # So bars 3-4 are empty.
    with pytest.raises(EmbeddingScopeError) as exc:
        embed_composition_scope(
            composition, EmbedScopeBarRange(start_bar=3, end_bar=4)
        )
    assert exc.value.code == "embed_empty_scope"


def test_projection_none_only():
    composition = _load("similar_rhythm_a.json")
    with pytest.raises(EmbeddingScopeError):
        extract_symbolic_features_v1(
            composition, EmbedScopeComposition(), projection_id="offline_linear.v1"  # type: ignore[arg-type]
        )


@pytest.mark.eval_example
def test_eval_example_nearest_neighbor_order():
    """Documented ranking under symbolic.features.v1 (affinity ≠ quality)."""
    query = embed_composition_scope(_load("similar_rhythm_a.json"))
    candidates = [
        ("b", embed_composition_scope(_load("similar_rhythm_b.json"))),
        ("c", embed_composition_scope(_load("dense_texture_c.json"))),
    ]
    ranked = sorted(
        candidates,
        key=lambda item: cosine_similarity(query.vector, item[1].vector),
        reverse=True,
    )
    assert [name for name, _ in ranked] == ["b", "c"]
