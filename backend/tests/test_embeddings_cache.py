"""Embedding cache: hit/miss and fingerprint invalidation."""

from __future__ import annotations

import json
from pathlib import Path

from app.composition_schemas import CompositionV2
from app.embeddings.cache import EmbeddingCache, get_default_embedding_cache, hash_cache_key
from app.embeddings.features import embed_composition_scope
from app.embeddings.schemas import EmbedScopeComposition, embed_scope_digest
from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_PROFILE_ID,
    load_embedding_settings,
)
from app.services.composition_fingerprint import composition_source_fingerprint


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "embeddings"


def _load(name: str) -> CompositionV2:
    return CompositionV2.model_validate(json.loads((FIXTURES / name).read_text(encoding="utf-8")))


def _key_parts(composition: CompositionV2, *, model_id: str = EMBEDDING_DEFAULT_MODEL_ID):
    scope = EmbedScopeComposition()
    return (
        model_id,
        EMBEDDING_PROFILE_ID,
        EMBEDDING_ALGORITHM_VERSION,
        composition_source_fingerprint(composition),
        embed_scope_digest(scope),
    )


def test_hash_cache_key_stable():
    parts = ("m", "p", "a", "fingerprint-abcdefghijklmnop", "scopedigest0123456789abcdef")
    assert hash_cache_key(parts) == hash_cache_key(parts)
    assert len(hash_cache_key(parts)) == 64


def test_same_key_hits(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setenv("EMBEDDING_CACHE_LRU_SIZE", "32")
    settings = load_embedding_settings()
    cache = EmbeddingCache(settings=settings, enable_filesystem=True)
    composition = _load("similar_rhythm_a.json")
    key = _key_parts(composition)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return embed_composition_scope(composition)

    first = cache.get_or_compute(key, compute, project_id="proj-a")
    second = cache.get_or_compute(key, compute, project_id="proj-a")
    assert calls["n"] == 1
    assert first.vector == second.vector
    assert first.source_fingerprint == second.source_fingerprint
    # Disk entry exists.
    object_id = hash_cache_key(key)
    assert (settings.cache_dir / f"{object_id}.embedding.json").is_file()


def test_changing_one_note_changes_fingerprint_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    settings = load_embedding_settings()
    cache = EmbeddingCache(settings=settings, enable_filesystem=True)
    composition = _load("similar_rhythm_a.json")
    key = _key_parts(composition)
    calls = {"n": 0}

    def compute_a():
        calls["n"] += 1
        return embed_composition_scope(composition)

    cache.get_or_compute(key, compute_a, project_id="proj-a")
    assert calls["n"] == 1

    mutated = CompositionV2.model_validate(composition.model_dump(mode="json"))
    events = list(mutated.tracks[0].events)
    first = events[0]
    events[0] = first.model_copy(update={"pitch": "D4"})
    mutated = mutated.model_copy(
        update={
            "tracks": [
                mutated.tracks[0].model_copy(update={"events": events}),
                *mutated.tracks[1:],
            ]
        }
    )
    assert composition_source_fingerprint(mutated) != composition_source_fingerprint(composition)
    key_b = _key_parts(mutated)

    def compute_b():
        calls["n"] += 1
        return embed_composition_scope(mutated)

    card_b = cache.get_or_compute(key_b, compute_b, project_id="proj-a")
    assert calls["n"] == 2
    assert card_b.source_fingerprint == composition_source_fingerprint(mutated)


def test_invalidate_by_project_and_fingerprint(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    settings = load_embedding_settings()
    cache = EmbeddingCache(settings=settings)
    composition = _load("similar_rhythm_a.json")
    key = _key_parts(composition)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return embed_composition_scope(composition)

    cache.get_or_compute(key, compute, project_id="proj-x")
    removed = cache.invalidate_by_project("proj-x")
    assert removed >= 1
    cache.get_or_compute(key, compute, project_id="proj-x")
    assert calls["n"] == 2

    fp = composition_source_fingerprint(composition)
    cache.invalidate_by_fingerprint(fp)
    cache.get_or_compute(key, compute, project_id="proj-x")
    assert calls["n"] == 3

    cache.invalidate_all()
    cache.get_or_compute(key, compute)
    assert calls["n"] == 4


def test_filesystem_hit_after_lru_eviction(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    monkeypatch.setenv("EMBEDDING_CACHE_LRU_SIZE", "8")
    settings = load_embedding_settings()
    cache = EmbeddingCache(settings=settings)
    composition = _load("similar_rhythm_a.json")
    key = _key_parts(composition)
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return embed_composition_scope(composition)

    cache.get_or_compute(key, compute, project_id="p1")
    # Fill LRU with distinct fake keys by embedding other scopes' digests via recompute stubs.
    for i in range(12):
        fake_key = (
            EMBEDDING_DEFAULT_MODEL_ID,
            EMBEDDING_PROFILE_ID,
            EMBEDDING_ALGORITHM_VERSION,
            f"{'a' * 16}{i:04d}{'b' * 44}",
            f"{'c' * 16}{i:04d}{'d' * 12}",
        )

        def make_compute(idx: int = i):
            def _fn():
                card = embed_composition_scope(composition)
                return card.model_copy(
                    update={
                        "source_fingerprint": fake_key[3],
                        "scope_digest": fake_key[4],
                    }
                )

            return _fn

        cache.get_or_compute(fake_key, make_compute())

    # Original key should still hit from disk without recomputing.
    before = calls["n"]
    cache.get_or_compute(key, compute, project_id="p1")
    assert calls["n"] == before


def test_default_cache_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    settings = load_embedding_settings()
    cache = get_default_embedding_cache(settings, reset=True)
    assert cache.lru_size == 0
    get_default_embedding_cache(reset=True)
