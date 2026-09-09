"""HTTP coverage for arrangement catalog and preview routes."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.arrangement_schemas import CompositionArrangementError
from app.composition_schemas import SUPPORTED_TRACK_ROLES
from app.main import app
from app.ready import build_readiness_report
from app.services.instrument_catalog import clear_catalog_cache
from tests.test_composition_arrangement_context import (
    _acceptance_instrumentation,
    _part,
    _piano_sketch_v2,
)
from tests.test_llm_composition_arrangement import _operation_request


FRONTEND_ROLES_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "utils"
    / "musicJsonValidation.js"
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "arrangement-routes.db"))
    clear_catalog_cache()
    with TestClient(app) as test_client:
        yield test_client
    clear_catalog_cache()


def _preview_body(**overrides):
    composition = _piano_sketch_v2()
    payload = {
        "composition": composition.model_dump(mode="json"),
        "operation": "piano_to_ensemble",
        "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
        "protected_track_ids": [],
        "instrumentation": _acceptance_instrumentation(),
        "candidate_count": 1,
        "preserve_melody": True,
        "preserve_harmony": True,
        "selection": {"provider": "fake", "model": "fake-deterministic"},
        "options": {"max_repairs": 1, "context_budget_chars": 8000},
    }
    payload.update(overrides)
    return payload


def _operation_body(operation: str, **overrides):
    request = _operation_request(operation)
    payload = request.model_dump(mode="json")
    payload.update(overrides)
    return payload


def test_instruments_catalog_success(client):
    response = client.get("/composition/arrangement/instruments")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["catalog_version"]
    assert payload["range_policy_version"]
    assert len(payload["fingerprint"]) == 64
    assert payload["source_path_category"] == "packaged"
    assert len(payload["instruments"]) >= 10
    piano = next(
        item for item in payload["instruments"] if item["instrument_id"] == "acoustic_grand_piano"
    )
    assert piano["midi_program"] == 0
    assert "melody" in piano["suggested_roles"]
    assert set(payload["track_roles"]) == SUPPORTED_TRACK_ROLES
    assert "other" in payload["track_roles"]


def test_canonical_role_parity_with_frontend_mirror():
    text = FRONTEND_ROLES_PATH.read_text(encoding="utf-8")
    assert "SUPPORTED_TRACK_ROLES" in text
    for role in sorted(SUPPORTED_TRACK_ROLES):
        assert f"'{role}'" in text or f'"{role}"' in text


@pytest.mark.parametrize(
    "operation",
    [
        "change_instrumentation",
        "add_accompaniment",
        "remove_accompaniment",
        "orchestrate_selected_tracks",
        "piano_to_ensemble",
        "simplify_arrangement",
        "increase_texture_density",
        "decrease_texture_density",
        "create_countermelody",
        "double_melody",
    ],
)
def test_preview_all_operations(client, operation):
    response = client.post(
        "/composition/arrangement/preview",
        json=_operation_body(operation),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["operation"] == operation
    assert payload["provider"] == "fake"
    assert 1 <= len(payload["candidates"]) <= 4
    assert payload["catalog_fingerprint"]
    for candidate in payload["candidates"]:
        assert candidate["composition"]["schema_version"] == "composition.v2"
        assert candidate["edit_source_fingerprint"] == payload["edit_source_fingerprint"]
        assert candidate["catalog_fingerprint"] == payload["catalog_fingerprint"]


def test_preview_rejects_v1_composition(client):
    body = _preview_body(
        composition={
            "schema_version": "composition.v1",
            "tempo": 100,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "bar_count": 2,
            "duration_ticks": 3840,
            "sections": [{"type": "intro", "start_bar": 1, "bar_count": 2}],
            "tracks": [
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [],
                }
            ],
            "harmony": [],
        }
    )
    response = client.post("/composition/arrangement/preview", json=body)
    assert response.status_code == 422


def test_preview_rejects_invalid_source(client):
    response = client.post(
        "/composition/arrangement/preview",
        json=_preview_body(source_track_ids=["missing-track"]),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] in {
        "arrangement_invalid_source",
        "arrangement_inventory_mismatch",
        "arrangement_invalid_operation",
    }
    assert "message" in detail


def test_preview_rejects_invalid_inventory(client):
    response = client.post(
        "/composition/arrangement/preview",
        json=_preview_body(
            instrumentation={
                "before": _acceptance_instrumentation()["before"],
                "after": [
                    _part("a-piano", "acoustic_grand_piano", role="melody"),
                    _part("a-cello", "cello", role="bass"),
                    _part("a-bogus", "not_a_real_instrument", role="harmony"),
                ],
            }
        ),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "arrangement_inventory_mismatch"


def test_preview_preflight_range_422(client):
    with patch(
        "app.routers.arrangement.run_composition_arrangement_preview",
        new=AsyncMock(
            side_effect=CompositionArrangementError(
                "arrangement_range_failed",
                http_status=422,
                details={"reason": "impossible_target_range", "part_id": "a-cb"},
            )
        ),
    ):
        response = client.post("/composition/arrangement/preview", json=_preview_body())
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "arrangement_range_failed"
    assert detail["details"]["reason"] == "impossible_target_range"


def test_preview_provider_range_exhaustion_502(client):
    body = _preview_body(
        operation="orchestrate_selected_tracks",
        source_track_ids=["piano-melody"],
        instrumentation={
            "before": [
                _part(
                    "b-m",
                    "acoustic_grand_piano",
                    role="melody",
                    source_track_ids=["piano-melody"],
                )
            ],
            "after": [_part("a-cb", "contrabass", role="melody")],
        },
        preserve_melody=False,
        options={"max_repairs": 0},
    )
    response = client.post("/composition/arrangement/preview", json=body)
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "arrangement_candidate_exhausted"


def test_preview_no_provider_503(client, monkeypatch):
    for key in ["OPENAI_API_KEY", "DEEPSEEK_API_KEY", "DEFAULT_LLM_PROVIDER", "LLM_FAKE_MODE"]:
        monkeypatch.delenv(key, raising=False)
    response = client.post(
        "/composition/arrangement/preview",
        json=_preview_body(selection={"provider": "openai", "model": "gpt-test"}),
    )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "arrangement_provider_unavailable"


def test_invalid_catalog_override_ready_false_and_instruments_503(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_DB_PATH", str(tmp_path / "catalog-ready.db"))
    bad = tmp_path / "bad-catalog.json"
    bad.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("ARRANGEMENT_INSTRUMENT_CATALOG_PATH", str(bad))
    clear_catalog_cache()
    try:
        report = build_readiness_report()
        assert report["ready"] is False
        assert report["arrangement_catalog"]["ok"] is False
        assert report["arrangement_catalog"]["error_code"] == "catalog_invalid_json"
        assert str(bad) not in json.dumps(report)
        assert "{not-json" not in json.dumps(report)

        with TestClient(app) as test_client:
            instruments = test_client.get("/composition/arrangement/instruments")
        assert instruments.status_code == 503
        assert instruments.json()["detail"]["code"] == "arrangement_catalog_unavailable"
    finally:
        clear_catalog_cache()


def test_preview_partial_provider_failure(client):
    from app.services import llm_composition_arrangement as module

    real_generate = module._generate_one_candidate_draft
    state = {"n": 0}

    async def flaky_generate(**kwargs):
        state["n"] += 1
        if state["n"] == 1:
            raise CompositionArrangementError(
                "arrangement_draft_invalid",
                details={"error_codes": ["arrangement_draft_invalid"]},
            )
        return await real_generate(**kwargs)

    with patch.object(module, "_generate_one_candidate_draft", new=flaky_generate):
        response = client.post(
            "/composition/arrangement/preview",
            json=_preview_body(candidate_count=2, options={"max_repairs": 0}),
        )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["candidates"]) == 1
    assert len(payload["rejected_attempts"]) >= 1
    assert "candidate_partial_success" in payload["warning_codes"]


def test_preview_total_provider_failure_502(client, monkeypatch):
    monkeypatch.setenv("LLM_FAKE_INJECT_MALFORMED", "arrangement")
    response = client.post(
        "/composition/arrangement/preview",
        json=_preview_body(options={"max_repairs": 0}),
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "arrangement_candidate_exhausted"


def test_preview_source_immutability(client):
    body = _preview_body()
    source = copy.deepcopy(body["composition"])
    response = client.post("/composition/arrangement/preview", json=body)
    assert response.status_code == 200, response.text
    assert body["composition"] == source


def test_preview_no_sqlite_writes(client):
    response = client.post("/composition/arrangement/preview", json=_preview_body())
    assert response.status_code == 200, response.text
    listed = client.get("/projects")
    assert listed.status_code == 200
    assert listed.json()["projects"] == []


def test_cors_allows_arrangement_endpoints(client):
    for path, method in (
        ("/composition/arrangement/instruments", "GET"),
        ("/composition/arrangement/preview", "POST"),
    ):
        response = client.options(
            path,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": method,
            },
        )
        assert response.status_code in {200, 204}
        assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_openapi_includes_arrangement_paths(client):
    schema = client.get("/openapi.json").json()
    assert "/composition/arrangement/instruments" in schema["paths"]
    assert "/composition/arrangement/preview" in schema["paths"]
    assert "get" in schema["paths"]["/composition/arrangement/instruments"]
    assert "post" in schema["paths"]["/composition/arrangement/preview"]


def test_preview_safe_error_details_omit_payloads(client):
    response = client.post(
        "/composition/arrangement/preview",
        json=_preview_body(source_track_ids=["nope"]),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    blob = json.dumps(detail)
    assert "events" not in blob
    assert "prompt" not in blob
    assert isinstance(detail["code"], str)
    assert isinstance(detail["message"], str)
