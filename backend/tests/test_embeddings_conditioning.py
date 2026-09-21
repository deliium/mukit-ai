"""Style/reference conditioning for development preview + provenance apply path."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from app.db import initialize_database, reset_database_initialization_cache
from app.embeddings.cache import EmbeddingCache
from app.embeddings.features import embed_composition_scope
from app.embeddings.schemas import EmbedScopeComposition, embed_scope_digest
from app.embeddings.settings import (
    EMBEDDING_ALGORITHM_VERSION,
    EMBEDDING_DEFAULT_MODEL_ID,
    EMBEDDING_PROFILE_ID,
    load_embedding_settings,
)
from app.main import app
from app.project_history_schemas import (
    AiProvenance,
    DurableCommitRequest,
    RevisionOperationType,
)
from app.services import project_history as history
from app.services import project_store as store
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from app.services.composition_fingerprint import composition_source_fingerprint
from app.db.connection import get_connection
from tests.test_composition_development_patch import _sixteen_bar_a


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "embeddings"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def project_db(tmp_path, monkeypatch):
    db_path = tmp_path / "projects.db"
    monkeypatch.setenv("PROJECT_DB_PATH", str(db_path))
    monkeypatch.setenv("EMBEDDING_CACHE_DIR", str(tmp_path / "embedding_cache"))
    reset_database_initialization_cache()
    initialize_database()
    yield db_path
    reset_database_initialization_cache()


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _vary_body(*, composition: dict, style_reference: dict | None = None, **overrides):
    payload = {
        "composition": composition,
        "operation": "vary_section",
        "source": {"section_id": "a"},
        "variation_strength": "balanced",
        "development_intent": "develop",
        "candidate_count": 1,
        "selection": {"provider": "fake", "model": "fake-deterministic"},
        "options": {"max_repairs": 1, "context_budget_chars": 8000},
    }
    if style_reference is not None:
        payload["style_reference"] = style_reference
    payload.update(overrides)
    return payload


def test_reference_resolve_route(client):
    composition = _load_fixture("similar_rhythm_a.json")
    response = client.post(
        "/embeddings/reference/resolve",
        json={
            "style_reference": {
                "composition": composition,
                "scope": {"kind": "section", "section_index": 0, "section_id": "s1"},
                "mode": "prompt_features",
            }
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["embedding"]["dims"] == 81
    assert payload["provenance"]["artist_label_used"] is False
    assert payload["conditioning"]["mode"] == "prompt_features"
    assert "top_pitch_classes" in payload["conditioning"]["feature_summary"]
    assert "vector" not in payload["conditioning"]["feature_summary"].lower()
    fragment = payload["provenance"]
    assert "source_fingerprint" in fragment
    assert len(fragment["source_fingerprint"]) >= 16


def test_conditioned_vary_section_fake_mode(client):
    working = _sixteen_bar_a().model_dump(mode="json")
    reference = _load_fixture("similar_rhythm_a.json")
    reference_fp = composition_source_fingerprint(CompositionV2.model_validate(reference))

    response = client.post(
        "/composition/development/preview",
        json=_vary_body(
            composition=working,
            style_reference={
                "composition": reference,
                "scope": {"kind": "composition"},
                "mode": "prompt_features",
            },
        ),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reference_provenance"] is not None
    assert payload["style_conditioning"] is not None
    assert payload["style_conditioning"]["feature_summary"]
    assert "reference_similarity_delta" in payload["warning_codes"]

    candidate = payload["candidates"][0]
    assert candidate["composition"]["schema_version"] == "composition.v2"
    candidate_fp = composition_edit_fingerprint(CompositionV2.model_validate(candidate["composition"]))
    assert candidate_fp != reference_fp
    assert candidate["edit_source_fingerprint"] == payload["edit_source_fingerprint"]

    # Outside-range / topology preservation still present.
    kinds = {item["kind"] for item in candidate["preservation"]}
    assert "outside_range" in kinds or "track_topology" in kinds
    assert all(item["satisfied"] for item in candidate["preservation"] if item["required"])


def test_conditioned_apply_persists_reference_provenance(project_db, client, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    working = _sixteen_bar_a()
    created = store.create_project(
        "Conditioned Develop",
        composition=working.model_dump(mode="json"),
        db_path=project_db,
    )
    record = store.get_project(created.id, db_path=project_db)
    branch_id = record.active_branch_id
    assert branch_id is not None

    from app.db.connection import get_connection

    with get_connection(project_db) as conn:
        branch = conn.execute(
            "SELECT working_fingerprint, working_version FROM project_branches WHERE id = ?",
            (branch_id,),
        ).fetchone()

    reference = _load_fixture("similar_rhythm_a.json")
    preview = client.post(
        "/composition/development/preview",
        json=_vary_body(
            composition=working.model_dump(mode="json"),
            style_reference={
                "composition": reference,
                "scope": {"kind": "composition"},
                "mode": "prompt_features",
            },
        ),
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    candidate = preview_body["candidates"][0]
    provenance = preview_body["reference_provenance"]
    assert provenance is not None
    gen_params = {
        "reference_provenance": {
            "schema_version": provenance["schema_version"],
            "project_id": provenance.get("project_id"),
            "revision_id": provenance.get("revision_id"),
            "scope_kind": provenance["scope"]["kind"],
            "scope_digest_prefix": provenance["scope_digest"][:12],
            "source_fingerprint_prefix": provenance["source_fingerprint"][:12],
            "embedding_model_id": provenance["embedding_model_id"],
            "profile_id": provenance["profile_id"],
            "algorithm_version": provenance["algorithm_version"],
            "artist_label_used": False,
        }
    }

    reference_fp = composition_source_fingerprint(CompositionV2.model_validate(reference))
    applied = history.commit_revision(
        created.id,
        DurableCommitRequest(
            branch_id=branch_id,
            expected_active_branch_id=branch_id,
            expected_working_version=int(branch["working_version"]),
            expected_head_revision_id=record.current_revision_id,
            expected_source_fingerprint=branch["working_fingerprint"],
            composition=CompositionV2.model_validate(candidate["composition"]),
            operation_type=RevisionOperationType.DEVELOPMENT_APPLY,
            ai=AiProvenance(
                provider="fake",
                model="fake-deterministic",
                candidate_id=candidate["candidate_id"],
                candidate_fingerprint=candidate["candidate_fingerprint"],
                generation_parameters=gen_params,
                warning_codes=["reference_similarity_delta"],
            ),
        ),
        db_path=project_db,
    )
    assert applied.revision_created is True
    assert applied.working_fingerprint != reference_fp

    detail = history.get_revision_detail(
        created.id,
        applied.current_revision_id,
        db_path=project_db,
    )
    summary = detail.revision.summary
    assert "generation_parameters" in summary
    assert "reference_provenance" in summary["generation_parameters"]
    ref_frag = summary["generation_parameters"]["reference_provenance"]
    assert ref_frag["artist_label_used"] is False
    assert ref_frag["embedding_model_id"]
    assert ref_frag["source_fingerprint_prefix"]


def test_autosave_invalidates_cache_only_when_fingerprint_changes(project_db, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    settings = load_embedding_settings()
    cache = EmbeddingCache(settings=settings, enable_filesystem=True)
    # Replace process default so invalidate hooks hit this cache.
    monkeypatch.setattr(
        "app.services.composition_embedding_invalidation.get_default_embedding_cache",
        lambda _settings=None: cache,
    )

    composition = CompositionV2.model_validate(_load_fixture("similar_rhythm_a.json"))
    created = store.create_project(
        "Cache Hook",
        composition=composition.model_dump(mode="json"),
        db_path=project_db,
    )
    key = (
        EMBEDDING_DEFAULT_MODEL_ID,
        EMBEDDING_PROFILE_ID,
        EMBEDDING_ALGORITHM_VERSION,
        composition_source_fingerprint(composition),
        embed_scope_digest(EmbedScopeComposition()),
    )
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return embed_composition_scope(composition)

    cache.get_or_compute(key, compute, project_id=created.id)
    assert calls["n"] == 1

    record = store.get_project(created.id, db_path=project_db)
    with get_connection(project_db) as conn:
        branch = conn.execute(
            "SELECT id, working_fingerprint, working_version FROM project_branches WHERE id = ?",
            (record.active_branch_id,),
        ).fetchone()

    # Same composition content → fingerprint unchanged → no invalidation thrash.
    store.update_project(
        created.id,
        composition=composition.model_dump(mode="json"),
        expected_active_branch_id=record.active_branch_id,
        expected_working_version=int(branch["working_version"]),
        expected_source_fingerprint=branch["working_fingerprint"],
        db_path=project_db,
    )
    # Cache entry should still hit without recompute when fingerprint unchanged.
    cache.get_or_compute(key, compute, project_id=created.id)
    assert calls["n"] == 1

    record2 = store.get_project(created.id, db_path=project_db)
    with get_connection(project_db) as conn:
        branch2 = conn.execute(
            "SELECT id, working_fingerprint, working_version FROM project_branches WHERE id = ?",
            (record2.active_branch_id,),
        ).fetchone()

    mutated = copy.deepcopy(composition.model_dump(mode="json"))
    mutated["tracks"][0]["events"][0]["pitch"] = "G5"
    store.update_project(
        created.id,
        composition=mutated,
        expected_active_branch_id=record2.active_branch_id,
        expected_working_version=int(branch2["working_version"]),
        expected_source_fingerprint=branch2["working_fingerprint"],
        db_path=project_db,
    )
    # After fingerprint change, project cache entries are gone → recompute.
    cache.get_or_compute(key, compute, project_id=created.id)
    assert calls["n"] == 2
