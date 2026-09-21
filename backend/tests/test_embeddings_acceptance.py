"""Backend acceptance smoke for symbolic embeddings (registry + routes + eval)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.ai_runtime import registry as registry_mod
from app.ai_runtime.operations import AiOperation
from app.ai_runtime.routing import resolve_model_for_operation
from app.main import app
from app.routers.ai_models import list_ai_models


@pytest.fixture(autouse=True)
def _clear_registry(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    registry_mod.clear_registry_for_tests()
    registry_mod.reload_registry(dict(**{k: v for k, v in __import__("os").environ.items()}))
    yield
    registry_mod.clear_registry_for_tests()


def test_acceptance_registry_lists_ready_symbolic_embedder():
    response = asyncio.run(list_ai_models(capability="embedding"))
    ready = [m for m in response.models if m.status == "ready"]
    assert any(m.id == "local:symbolic-features-v1" for m in ready)
    assert response.operation_defaults.get("embed") == "local:symbolic-features-v1"


def test_acceptance_resolve_embed_op():
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    resolved = resolve_model_for_operation(AiOperation.EMBED, None, env=env)
    assert resolved.resolved_model_id == "local:symbolic-features-v1"
    assert resolved.descriptor.runtime == "symbolic_features"


def test_acceptance_compute_and_dataset_corpus_forbidden():
    client = TestClient(app)
    composition = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 2,
        "duration_ticks": 3840,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            }
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    compute = client.post(
        "/embeddings/compute",
        json={"composition": composition, "scope": {"kind": "composition"}},
    )
    assert compute.status_code == 200, compute.text
    body = compute.json()
    assert body["embedding"]["dims"] == 81
    assert body["embedding"]["artist_label_used"] is False
    assert body["embedding"]["profile_id"] == "symbolic.features.v1"

    forbidden = client.post(
        "/embeddings/similarity",
        json={
            "query": {
                "schema_version": "composition.similarity_query.v1",
                "composition": composition,
                "scope": {"kind": "composition"},
                "corpus": {"kind": "dataset"},
                "top_k": 5,
            }
        },
    )
    assert forbidden.status_code == 403
    detail = forbidden.json().get("detail") or {}
    if isinstance(detail, dict):
        assert detail.get("code") in {
            "dataset_export_forbidden",
            "similarity_corpus_forbidden",
        }
