"""OpenAPI schema assertions for composition.v2 response contracts."""

from __future__ import annotations

import json

from app.main import app


def _component(name: str) -> dict:
    schema = app.openapi()
    return schema["components"]["schemas"][name]


def test_openapi_generation_response_music_is_composition_v2():
    response = _component("LLMMusicGenerationResponse")
    music_ref = response["properties"]["music"]["$ref"]
    assert music_ref.endswith("/CompositionV2")
    music = _component("CompositionV2")
    assert music["properties"]["schema_version"]["const"] == "composition.v2"


def test_openapi_edit_response_composition_and_patch_are_v2():
    response = _component("LLMCompositionEditResponse")
    composition_ref = response["properties"]["composition"]["$ref"]
    patch_ref = response["properties"]["patch"]["$ref"]
    assert composition_ref.endswith("/CompositionV2")
    assert patch_ref.endswith("/CompositionRegionReplacementPatch")

    patch = _component("CompositionRegionReplacementPatch")
    schema_version = patch["properties"]["schema_version"]
    assert "composition.v2" in schema_version.get("const", schema_version.get("enum", []))


def test_openapi_edit_request_accepts_v1_and_v2_composition_input():
    request = _component("LLMCompositionEditRequest")
    composition = request["properties"]["composition"]
    # Discriminated union or anyOf for V1/V2 input branches.
    serialized = json.dumps(composition)
    assert "CompositionV1" in serialized or "composition.v1" in serialized
    assert "CompositionV2" in serialized or "composition.v2" in serialized


def test_openapi_project_detail_returns_composition_v2():
    detail = _component("ProjectDetailResponse")
    composition = detail["properties"]["composition"]
    ref = composition.get("$ref") or composition.get("anyOf", [{}])[0].get("$ref", "")
    assert ref.endswith("/CompositionV2") or "CompositionV2" in json.dumps(composition)


def test_openapi_import_endpoints_and_response_contract():
    schema = app.openapi()
    paths = schema["paths"]
    assert "/imports/midi" in paths
    assert "/imports/musicxml" in paths
    midi = paths["/imports/midi"]["post"]
    assert "multipart/form-data" in json.dumps(midi.get("requestBody", {}))
    response = _component("CompositionImportResponse")
    assert response["properties"]["composition"]["$ref"].endswith("/CompositionV2")
    assert "import_report" in response["properties"]
    assert "musicxml" in response["properties"]
    assert "notation_report" in response["properties"]


def test_openapi_analysis_endpoint_and_response_contract():
    schema = app.openapi()
    paths = schema["paths"]
    assert "/analysis/composition" in paths
    post = paths["/analysis/composition"]["post"]
    assert "application/json" in json.dumps(post.get("requestBody", {}))
    response = _component("CompositionAnalysisReport")
    assert response["properties"]["schema_version"]["const"] == "composition.analysis.v1"
    assert "resolved_scope" in response["properties"]
    assert "source_fingerprint" in response["properties"]
    assert "warnings" in response["properties"]


def test_openapi_composition_development_preview_contract():
    schema = app.openapi()
    paths = schema["paths"]
    assert "/composition/development/preview" in paths
    post = paths["/composition/development/preview"]["post"]
    assert "application/json" in json.dumps(post.get("requestBody", {}))
    request = _component("CompositionDevelopmentPreviewRequest")
    composition = request["properties"]["composition"]
    assert composition["$ref"].endswith("/CompositionV2")
    assert "CompositionV1" not in json.dumps(composition)
    response = _component("CompositionDevelopmentPreviewResponse")
    assert response["properties"]["edit_source_fingerprint"]["type"] == "string"
    candidates = response["properties"]["candidates"]
    assert candidates["minItems"] == 1
    assert candidates["maxItems"] == 4
    candidate = _component("DevelopmentCandidate")
    assert candidate["properties"]["composition"]["$ref"].endswith("/CompositionV2")
    assert "candidate_id" in candidate["properties"]
    assert "candidate_fingerprint" in candidate["properties"]
