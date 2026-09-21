"""Project similarity index: ranking, dataset export gate, stale filter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.embeddings.errors import DatasetExportForbiddenError, SimilarityIndexError
from app.embeddings.features import embed_composition_scope
from app.embeddings.index import ProjectSimilarityIndex, default_index_scopes
from app.embeddings.schemas import EmbedScopeComposition
from app.embeddings.settings import load_embedding_settings
from app.embeddings.vector import cosine_similarity
from app.services.composition_fingerprint import composition_source_fingerprint


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "embeddings"


def _load(name: str) -> CompositionV2:
    return CompositionV2.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def test_default_index_scopes_composition_and_sections():
    composition = _load("similar_rhythm_a.json")
    scopes = default_index_scopes(composition, include_motifs=False)
    kinds = [s.kind for s in scopes]
    assert kinds[0] == "composition"
    assert kinds.count("section") == len(composition.sections)
    assert "motif" not in kinds


def test_index_search_ranking(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setenv("EMBEDDING_ALLOW_DATASET_CORPUS", "0")
    settings = load_embedding_settings()
    index = ProjectSimilarityIndex(settings=settings, enable_filesystem=False)

    a = _load("similar_rhythm_a.json")
    b = _load("similar_rhythm_b.json")
    c = _load("dense_texture_c.json")
    count = index.build_index_from_compositions(
        [
            ("proj-a", a, [EmbedScopeComposition()]),
            ("proj-b", b, [EmbedScopeComposition()]),
            ("proj-c", c, [EmbedScopeComposition()]),
        ]
    )
    assert count == 3

    query = embed_composition_scope(a)
    hits = index.search(
        query.vector,
        top_k=3,
        exclude_project_id="proj-a",
        exclude_source_fingerprint=query.source_fingerprint,
    )
    assert [h.target.project_id for h in hits] == ["proj-b", "proj-c"]
    assert hits[0].score > hits[1].score
    assert hits[0].musical_quality_claim is False
    # Sanity vs direct cosine.
    assert hits[0].score == pytest.approx(
        cosine_similarity(query.vector, embed_composition_scope(b).vector),
        abs=1e-5,
    )


def test_dataset_export_forbidden_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setenv("EMBEDDING_ALLOW_DATASET_CORPUS", "0")
    settings = load_embedding_settings()
    index = ProjectSimilarityIndex(settings=settings)
    composition = _load("similar_rhythm_a.json")
    index.build_index_from_compositions(
        [("proj-a", composition, [EmbedScopeComposition()])]
    )

    with pytest.raises(DatasetExportForbiddenError) as exc:
        index.refuse_dataset_export()
    assert exc.value.code == "dataset_export_forbidden"

    with pytest.raises(DatasetExportForbiddenError):
        index.export_to_dataset_corpus(
            destination=tmp_path / "export.json",
            explicit_confirm=True,
        )

    with pytest.raises(DatasetExportForbiddenError):
        index.export_to_dataset_corpus(
            destination=tmp_path / "export.json",
            explicit_confirm=False,
        )


def test_dataset_export_allowed_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setenv("EMBEDDING_ALLOW_DATASET_CORPUS", "1")
    settings = load_embedding_settings()
    index = ProjectSimilarityIndex(settings=settings)
    composition = _load("similar_rhythm_a.json")
    index.build_index_from_compositions(
        [("proj-a", composition, [EmbedScopeComposition()])]
    )
    # Still refuses without explicit confirm.
    with pytest.raises(DatasetExportForbiddenError):
        index.export_to_dataset_corpus(
            destination=tmp_path / "export.json",
            explicit_confirm=False,
        )
    path = index.export_to_dataset_corpus(
        destination=tmp_path / "export.json",
        explicit_confirm=True,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entry_count"] == 1
    assert "vector" not in payload["entries"][0]
    assert "composition" not in payload["entries"][0]


def test_stale_fingerprint_filter(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    settings = load_embedding_settings()
    index = ProjectSimilarityIndex(settings=settings)
    a = _load("similar_rhythm_a.json")
    b = _load("similar_rhythm_b.json")
    index.build_index_from_compositions(
        [
            ("proj-a", a, [EmbedScopeComposition()]),
            ("proj-b", b, [EmbedScopeComposition()]),
        ]
    )
    # Mutate project-b composition fingerprint without rebuilding index.
    mutated_b = CompositionV2.model_validate(b.model_dump(mode="json"))
    events = list(mutated_b.tracks[0].events)
    events[0] = events[0].model_copy(update={"pitch": "C5"})
    mutated_b = mutated_b.model_copy(
        update={
            "tracks": [
                mutated_b.tracks[0].model_copy(update={"events": events}),
                *mutated_b.tracks[1:],
            ]
        }
    )
    current = {
        "proj-a": composition_source_fingerprint(a),
        "proj-b": composition_source_fingerprint(mutated_b),
    }
    assert current["proj-b"] != composition_source_fingerprint(b)

    query = embed_composition_scope(a)
    hits = index.search(
        query.vector,
        top_k=5,
        exclude_project_id="proj-a",
        current_fingerprints=current,
        filter_stale=True,
    )
    assert hits == []

    hits_unfiltered = index.search(
        query.vector,
        top_k=5,
        exclude_project_id="proj-a",
        current_fingerprints=current,
        filter_stale=False,
    )
    assert [h.target.project_id for h in hits_unfiltered] == ["proj-b"]


def test_empty_index_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    settings = load_embedding_settings()
    index = ProjectSimilarityIndex(settings=settings)
    with pytest.raises(SimilarityIndexError) as exc:
        index.search([0.0] * 81, top_k=3)
    assert exc.value.code == "similarity_index_empty"


def test_compute_embedding_orchestration_uses_cache(tmp_path, monkeypatch):
    from app.ai_runtime import registry as registry_mod
    from app.embeddings.cache import EmbeddingCache, get_default_embedding_cache
    from app.services.composition_embedding import compute_embedding

    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.clear_registry_for_tests()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    registry_mod.reload_registry(env)
    settings = load_embedding_settings()
    cache = EmbeddingCache(settings=settings)
    get_default_embedding_cache(settings, reset=True)

    composition = _load("similar_rhythm_a.json")
    first = compute_embedding(
        composition, EmbedScopeComposition(), project_id="orch-1", cache=cache
    )
    second = compute_embedding(
        composition, EmbedScopeComposition(), project_id="orch-1", cache=cache
    )
    assert first.vector == second.vector
    assert first.model_id == "local:symbolic-features-v1"
    assert cache.lru_size >= 1
    registry_mod.clear_registry_for_tests()
    get_default_embedding_cache(reset=True)
