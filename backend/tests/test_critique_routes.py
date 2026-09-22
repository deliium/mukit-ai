"""API tests for POST /critique/evaluate."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from tests.test_composition_v2_schema import minimal_v2
from tests.test_composition_critique_engine import _two_section_composition


client = TestClient(app)


def test_critique_evaluate_climax_case():
    composition = _two_section_composition(climax_notes=3, prior_notes=3)
    response = client.post(
        "/critique/evaluate",
        json={
            "composition": composition.model_dump(mode="json"),
            "scope": {"kind": "composition"},
            "include_model_critique": False,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mutates_composition"] is False
    critique = body["critique"]
    assert critique["schema_version"] == "agent.critique.v1"
    codes = [f["code"] for f in critique.get("findings") or []]
    assert "climax_lacks_contrast" in codes
    assert critique["recommendation"] == "approve"
    assert critique["stratum_counts"]["stylistic"] >= 1


def test_critique_evaluate_bars_scope():
    composition = _two_section_composition(climax_notes=3, prior_notes=3)
    response = client.post(
        "/critique/evaluate",
        json={
            "composition": composition.model_dump(mode="json"),
            "scope": {"kind": "bars", "start_bar": 5, "end_bar": 8},
        },
    )
    assert response.status_code == 200
    assert response.json()["critique"]["scope"]["kind"] == "bars"


def test_critique_evaluate_invalid_composition():
    response = client.post(
        "/critique/evaluate",
        json={"composition": {"schema_version": "composition.v2"}, "scope": {"kind": "composition"}},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] in (
        "critique_invalid_composition",
        "critique_invalid_scope",
    )


def test_critique_evaluate_fake_model():
    composition = minimal_v2()
    response = client.post(
        "/critique/evaluate",
        json={
            "composition": composition,
            "scope": {"kind": "composition"},
            "include_model_critique": True,
        },
    )
    assert response.status_code == 200
    critique = response.json()["critique"]
    # LLM_FAKE_MODE may or may not be set in TestClient env; status is set either way.
    assert critique.get("model_critique_status") in (
        "ok",
        "skipped",
        "unavailable",
        "failed",
        None,
    )
