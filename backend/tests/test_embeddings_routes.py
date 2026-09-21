"""HTTP coverage for symbolic embeddings routes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.embeddings.settings import EMBEDDING_DEFAULT_MODEL_ID
from app.main import app
from tests.test_composition_v2_schema import minimal_v2


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "embeddings"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_compute_composition_scope_dims(client):
    composition = _load_fixture("similar_rhythm_a.json")
    response = client.post(
        "/embeddings/compute",
        json={"composition": composition, "scope": {"kind": "composition"}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    embedding = payload["embedding"]
    assert embedding["dims"] == 81
    assert len(embedding["vector"]) == 81
    assert embedding["model_id"] == EMBEDDING_DEFAULT_MODEL_ID
    assert embedding["profile_id"] == "symbolic.features.v1"
    assert embedding["artist_label_used"] is False
    assert embedding["scope"]["kind"] == "composition"


def test_compute_empty_scope_422(client):
    composition = minimal_v2()
    response = client.post(
        "/embeddings/compute",
        json={"composition": composition, "scope": {"kind": "composition"}},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "embed_empty_scope"


def test_similarity_in_request_ranked_hits(client):
    composition = _load_fixture("similar_rhythm_a.json")
    response = client.post(
        "/embeddings/similarity",
        json={
            "query": {
                "composition": composition,
                "scope": {"kind": "composition"},
                "corpus": {
                    "kind": "in_request",
                    "include_composition_sections": True,
                    "include_motifs": False,
                },
                "top_k": 5,
            }
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_id"] == EMBEDDING_DEFAULT_MODEL_ID
    assert payload["musical_quality_claim"] is False
    hits = payload["hits"]
    assert len(hits) >= 1
    ranks = [hit["rank"] for hit in hits]
    assert ranks == list(range(1, len(hits) + 1))
    scores = [hit["score"] for hit in hits]
    assert scores == sorted(scores, reverse=True)
    assert all(hit["target"]["corpus"] == "in_request" for hit in hits)
    assert all(hit["target"]["scope"]["kind"] == "section" for hit in hits)


def test_similarity_dataset_corpus_403(client):
    composition = _load_fixture("similar_rhythm_a.json")
    response = client.post(
        "/embeddings/similarity",
        json={
            "query": {
                "composition": composition,
                "scope": {"kind": "composition"},
                "corpus": {"kind": "dataset"},
                "top_k": 5,
            }
        },
    )
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert detail["code"] in {"similarity_corpus_forbidden", "dataset_export_forbidden"}


def test_related_motifs_fixture(client):
    composition = _load_fixture("motif_scope.json")
    # Add a second motif so related search has an in-composition candidate.
    composition = {
        **composition,
        "motifs": [
            *composition["motifs"],
            {
                "id": "motif-b",
                "label": "Motif B",
                "occurrences": [
                    {
                        "id": "occ-b",
                        "track_id": "melody-1",
                        "event_ids": ["n2", "n3", "n4"],
                        "relationship": "original",
                    }
                ],
            },
        ],
    }
    response = client.post(
        "/embeddings/related-motifs",
        json={
            "composition": composition,
            "motif_id": "motif-a",
            "top_k": 5,
            "search_cross_project": False,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_id"] == EMBEDDING_DEFAULT_MODEL_ID
    assert payload["musical_quality_claim"] is False
    assert len(payload["hits"]) >= 1
    assert payload["hits"][0]["target"]["scope"]["kind"] == "motif"
    assert payload["hits"][0]["target"]["scope"]["motif_id"] == "motif-b"
