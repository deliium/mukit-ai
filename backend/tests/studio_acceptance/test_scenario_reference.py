"""Scenario B: a generated melody does not copy the reference pitch list."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.composition_schemas import CompositionV2
from tests.studio_acceptance.invariants import assert_melody_not_copied, melody_pitches

_PHRASE = Path(__file__).resolve().parents[1] / "fixtures" / "studio" / "reference_phrase.json"
_EXPRESSIVE = (
    Path(__file__).resolve().parents[2]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)
_TOKEN = "F#5"


def test_reference_melody_is_not_copied(
    studio_client: tuple[TestClient, object],
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _db_path = studio_client
    caplog.set_level(logging.INFO)
    reference = json.loads(_PHRASE.read_text(encoding="utf-8"))
    CompositionV2.model_validate(reference)
    expressive = _EXPRESSIVE.read_text(encoding="utf-8")
    assert _TOKEN not in expressive
    assert melody_pitches(reference) == [_TOKEN, _TOKEN]
    sent = json.loads(json.dumps(reference))

    created = client.post("/projects", json={"name": "Reference"})
    assert created.status_code == 201
    project_id = created.json()["id"]
    patched = client.patch(f"/projects/{project_id}", json={"composition": reference})
    assert patched.status_code == 200, patched.text

    analyzed = client.post(
        "/reference-features/analyze",
        json={
            "composition": reference,
            "requested_dimensions": ["melodic_contour"],
            "scope": {"kind": "composition"},
        },
    )
    assert analyzed.status_code == 200, analyzed.text
    assert reference == sent

    generated = client.post(
        "/llm/generate-music-json",
        json={
            "prompt": {
                "genre": "classical",
                "mood": "calm",
                "time_signature": "4/4",
                "tempo_min": 90,
                "tempo_max": 110,
                "instruments": ["piano", "bass"],
                "complexity": "moderate",
                "duration_bars": 4,
            },
            "selection": {"provider": "fake", "model": "fake-v1"},
            "options": {"pipeline": "llm_only", "max_retries": 0},
            "style_references": [
                {
                    "composition": reference,
                    "scope": {"kind": "composition"},
                    "dimensions": ["texture"],
                    "mode": "prompt_features",
                }
            ],
            "reference_conditioning_policy": {
                "schema_version": "reference.conditioning.policy.v1",
                "preserve_dimensions": [],
                "regenerate_dimensions": ["melodic_contour"],
                "dimension_strengths": {},
                "default_borrow_strength": "normal",
                "allow_motif_reuse": False,
                "strict_partition": True,
            },
        },
    )
    assert generated.status_code == 200, generated.text
    body = generated.json()
    music = body["music"]
    parameters = json.dumps(body.get("generation_parameters") or {})
    assert _TOKEN not in parameters
    assert_melody_not_copied(music, reference)
    instruments = {track["instrument"] for track in music["tracks"]}
    assert instruments <= {"piano", "bass"}
    assert "piano" in instruments and "bass" in instruments
    stored = client.get(f"/projects/{project_id}")
    assert melody_pitches(stored.json()["composition"]) == [_TOKEN, _TOKEN]
    assert json.dumps(reference.get("tracks")) not in caplog.text
