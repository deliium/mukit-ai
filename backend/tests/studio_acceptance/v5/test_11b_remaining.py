"""V5 matrix 11b: remaining Composer OS surfaces (fake/status smoke)."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from tests.studio_acceptance.invariants import (
    assert_ardour_corruption_guards,
    assert_composition_source_of_truth,
)
from tests.studio_acceptance.v5.helpers import create_v2_project, open_composition

ROOT = Path(__file__).resolve().parents[4]


def test_personal_composer_status(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Personal V5")
    listed = client.get("/personal-composer/adapters")
    assert listed.status_code in {200, 403, 404, 503}
    open_composition(client, project_id)


def test_preference_ranking_surface(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Prefs V5")
    settings = client.get("/preferences/settings")
    assert settings.status_code in {200, 403, 404}
    open_composition(client, project_id)


def test_distributed_inference_nodes(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Nodes V5")
    nodes = client.get("/ai/execution-nodes")
    assert nodes.status_code in {200, 403, 503}
    schedule = client.get("/ai/scheduling/policy")
    assert schedule.status_code in {200, 403, 404, 503}
    open_composition(client, project_id)


def test_webgpu_fallback_stub() -> None:
    """Public browser-model assets exist; no private weight tree / composition.v5."""
    manifest = ROOT / "frontend" / "public" / "browser-models"
    assert manifest.is_dir()
    names = {path.name for path in manifest.iterdir()}
    assert any("manifest" in name or name.endswith(".json") for name in names) or names
    blob = " ".join(names)
    assert "composition.v5" not in blob


def test_expressive_midi_stub() -> None:
    """Expressive MIDI helpers live in SPA utils; ship no alternate score schema."""
    midi_dir = ROOT / "frontend" / "src" / "utils" / "midiExpressive"
    assert midi_dir.is_dir()
    for path in midi_dir.rglob("*.js"):
        text = path.read_text(encoding="utf-8")
        assert "composition.v5" not in text


def test_ai_conductor_performance(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, composition = create_v2_project(client, name="Conductor V5")
    catalog = client.get(f"/projects/{project_id}/performance-plans/presets")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["presets"]
    cloned = client.post(
        f"/projects/{project_id}/performance-plans",
        json={"preset_id": "intimate", "composition": composition},
    )
    assert cloned.status_code in {200, 201}, cloned.text
    open_composition(client, project_id)


def test_spatial_preview(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Spatial V5")
    catalog = client.get(f"/projects/{project_id}/spatial-scenes/presets")
    assert catalog.status_code == 200, catalog.text
    open_composition(client, project_id)


def test_ardour_roundtrip_guards(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Ardour V5")
    status = client.get("/ardour/exchange/status")
    assert status.status_code == 200
    assert_ardour_corruption_guards()
    open_composition(client, project_id)


def test_asset_pack_plan_preview(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Pack V5")
    preview = client.post(
        "/asset-packs/plan/preview",
        json={
            "brief": {
                "schema_version": "asset.pack.brief.v1",
                "title": "V5 pack",
                "slots": [{"role": "theme", "label": "Theme A"}],
            }
        },
    )
    # Brief shape may refine; accept plan preview or validation refuse without write.
    assert preview.status_code in {200, 422}
    if preview.status_code == 200:
        plan = preview.json().get("plan") or preview.json()
        assert "tracks" not in json.dumps(plan)
    open_composition(client, project_id)


def test_provenance_status(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Prov V5")
    status = client.get("/content-provenance/status")
    assert status.status_code == 200
    open_composition(client, project_id)


def test_rights_enforcement(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Rights V5")
    status = client.get("/rights-governance/status")
    assert status.status_code == 200
    assert status.json().get("registry_enabled") is True
    from app.rights_governance_schemas import project_allowed_uses

    evaluate = client.post(
        "/rights-governance/evaluate",
        json={
            "use": "train",
            "entry": {
                "schema_version": "rights.registry.entry.v1",
                "entry_id": "rights_" + ("a" * 16),
                "source_kind": "project",
                "source_id": project_id,
                "ownership_class": "licensed",
                "use_policy": "reference_only",
                "allowed_uses": list(project_allowed_uses("reference_only")),
                "license": None,
                "license_spdx": None,
                "verification_status": "verified",
                "entry_version": 1,
                "created_at": "2026-10-04T00:00:00Z",
                "updated_at": "2026-10-04T00:00:00Z",
            },
        },
    )
    assert evaluate.status_code == 200, evaluate.text
    assert evaluate.json()["allowed"] is False
    open_composition(client, project_id)


def test_model_lab_status(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Lab V5")
    status = client.get("/model-lab/status")
    assert status.status_code == 200
    listed = client.get("/model-lab/experiments")
    assert listed.status_code == 200
    open_composition(client, project_id)


def test_model_ensemble_status(v5_studio_client) -> None:
    client, _db, _tmp = v5_studio_client
    project_id, _ = create_v2_project(client, name="Ensemble V5")
    status = client.get("/ensemble/arbitration/status")
    assert status.status_code == 200
    strategies = client.get("/ensemble/arbitration/strategies")
    assert strategies.status_code == 200
    open_composition(client, project_id)


def test_source_of_truth_invariant_helper() -> None:
    composition = {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 1,
        "duration_ticks": 1920,
        "sections": [],
        "tracks": [
            {
                "id": "t1",
                "name": "Piano",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "id": "n1",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ],
        "harmony": [],
        "markers": [],
        "key_changes": [],
        "motifs": [],
    }
    assert_composition_source_of_truth(composition)
