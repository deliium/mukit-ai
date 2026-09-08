"""HTTP and LLM-projection coverage for composition analysis API."""

from __future__ import annotations

import asyncio
import logging

import pytest
from fastapi.testclient import TestClient

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION, ANALYSIS_SCHEMA_VERSION
from app.llm_settings import LLMProviderSettings, LLMSettings
from app.main import app
from app.schemas import LLMCompositionEditRequest
from app.services import llm_composition_editor
from app.services.composition_analysis import (
    LLM_ANALYSIS_CONTEXT_ADVISORY_HEADER,
    LLM_ANALYSIS_CONTEXT_MAX_CHARS,
    analyze_composition,
    build_llm_analysis_context,
)
from app.services.composition_region_patch import summarize_region_selection
from app.services.llm_composition_editor import (
    _analysis_scope_for_edit,
    _build_draft_prompt,
    _safe_edit_analysis_context,
    edit_composition_region,
)
from app.services.llm_music_generator import (
    InvalidLLMOutputError,
    _append_repair_diagnostics,
    _safe_repair_analysis_context,
)
from app.services.composition_planner import ValidationDiagnostic
from tests.test_composition_analysis import _c_major_material, _note, _track, _v2_shell
from tests.test_composition_region_patch import _sixteen_bar_composition


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_analysis_route_composition_scope_ok(client):
    composition = _v2_shell(bar_count=4, tracks=[_track(_c_major_material(4))])
    response = client.post(
        "/analysis/composition",
        json={"composition": composition, "scope": {"kind": "composition"}},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema_version"] == ANALYSIS_SCHEMA_VERSION
    assert payload["algorithm_version"] == ANALYSIS_ALGORITHM_VERSION
    assert payload["resolved_scope"]["kind"] == "composition"
    assert "source_fingerprint" in payload
    assert "warnings" in payload


def test_analysis_route_section_and_track_scope(client):
    composition = _v2_shell(
        bar_count=4,
        tracks=[
            _track(_c_major_material(4), track_id="mel", role="melody"),
            _track([_note("C3", 0, 1920)], track_id="bass", role="bass"),
        ],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "type": "chorus",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
    )
    section = client.post(
        "/analysis/composition",
        json={"composition": composition, "scope": {"kind": "section", "section_index": 1}},
    )
    assert section.status_code == 200, section.text
    assert section.json()["resolved_scope"]["kind"] == "section"
    assert section.json()["resolved_scope"]["section_index"] == 1

    track = client.post(
        "/analysis/composition",
        json={"composition": composition, "scope": {"kind": "track", "track_id": "bass"}},
    )
    assert track.status_code == 200, track.text
    assert track.json()["resolved_scope"]["kind"] == "track"
    assert track.json()["resolved_scope"]["track_id"] == "bass"


def test_analysis_route_warnings_are_http_200(client):
    composition = _v2_shell(
        bar_count=2,
        tracks=[
            _track(_c_major_material(2), track_id="mel"),
            _track([], track_id="empty", role="harmony"),
        ],
    )
    response = client.post(
        "/analysis/composition",
        json={"composition": composition, "scope": {"kind": "track", "track_id": "empty"}},
    )
    assert response.status_code == 200, response.text
    codes = [item["code"] for item in response.json()["warnings"]]
    assert "empty_analysis_scope" in codes


def test_analysis_route_structured_422_invalid_composition(client, caplog):
    with caplog.at_level(logging.INFO):
        response = client.post(
            "/analysis/composition",
            json={
                "composition": {"schema_version": "composition.v1", "tracks": []},
                "scope": {"kind": "composition"},
            },
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "analysis_invalid_composition"
    assert "message" in detail
    assert '"events"' not in response.text
    assert "C4" not in caplog.text


def test_analysis_route_structured_422_invalid_scope(client):
    composition = _v2_shell(bar_count=2, tracks=[_track(_c_major_material(2))])
    response = client.post(
        "/analysis/composition",
        json={
            "composition": composition,
            "scope": {"kind": "track", "track_id": "missing-track"},
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "analysis_invalid_scope"
    assert "events" not in str(detail).lower()


def test_build_llm_analysis_context_advisory_and_budget():
    composition = _v2_shell(bar_count=4, tracks=[_track(_c_major_material(4))])
    report = analyze_composition(composition, {"kind": "composition"})
    text = build_llm_analysis_context(report)
    assert LLM_ANALYSIS_CONTEXT_ADVISORY_HEADER.split(".")[0] in text
    assert "non-authoritative" in text
    assert "source of truth" in text
    assert len(text) <= LLM_ANALYSIS_CONTEXT_MAX_CHARS
    assert '"spans": [' not in text
    assert '"profiles": [' not in text

    tiny = build_llm_analysis_context(report, max_chars=300)
    assert len(tiny) <= 300
    assert "[analysis_context_truncated]" in tiny


def test_edit_analysis_scope_mapping_and_prompt_labeling():
    composition = _sixteen_bar_composition()
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": composition.model_dump(mode="json"),
            "edit": {
                "instruction": "make melody more active",
                "selection": {"start_bar": 9, "end_bar": 12, "track_ids": ["melody-1"]},
                "allow_added_tracks": False,
                "allow_harmony_changes": False,
            },
            "selection": {"provider": "openai", "model": "test"},
            "options": {"max_retries": 1},
        }
    )
    summary = summarize_region_selection(request.composition, request.edit.selection)
    scope = _analysis_scope_for_edit(request.composition, summary)
    assert scope["kind"] in {"composition", "section", "track"}
    context = _safe_edit_analysis_context(request.composition, summary)
    assert "ADVISORY" in context
    prompt = _build_draft_prompt(request, summary, diagnostics=None, analysis_context=context)
    assert "ADVISORY" in prompt
    assert "source of truth" in prompt


def test_edit_repair_refreshes_analysis_context(monkeypatch):
    composition = _sixteen_bar_composition()
    request = LLMCompositionEditRequest.model_validate(
        {
            "composition": composition.model_dump(mode="json"),
            "edit": {
                "instruction": "regenerate melody",
                "selection": {"start_bar": 9, "end_bar": 12, "track_ids": ["melody-1"]},
            },
            "selection": {"provider": "openai", "model": "test"},
            "options": {"max_retries": 1},
        }
    )
    calls: list[str] = []

    def fake_safe(_comp, _summ):
        calls.append("refresh")
        return "ADVISORY refreshed-analysis-context"

    monkeypatch.setattr(llm_composition_editor, "_safe_edit_analysis_context", fake_safe)
    captured: list[str] = []

    async def fake_chat_repair(_state, prompt: str):
        captured.append(prompt)
        return (
            '{"schema_version":"composition.v2","operation":"replace_region",'
            '"start_bar":9,"end_bar":12,"target_track_ids":["melody-1"],'
            '"replace_tracks":[{"track_id":"melody-1","events":['
            '{"type":"note","pitch":"C4","start_tick":0,"duration_ticks":480,"velocity":90}'
            ']}],"added_tracks":[],"harmony_patch":null,"warnings":[]}'
        )

    monkeypatch.setattr(llm_composition_editor, "_invoke_edit_chat", fake_chat_repair)
    settings = LLMSettings(
        providers=(
            LLMProviderSettings(
                provider="openai",
                model="test-model",
                api_key="secret",
                is_default=True,
            ),
        ),
        default_provider="openai",
        request_timeout_seconds=60,
        temperature=0.2,
    )
    with pytest.raises(InvalidLLMOutputError):
        asyncio.run(edit_composition_region(request, settings=settings))

    assert any("refreshed-analysis-context" in item for item in captured)
    assert len(calls) >= 2


def test_generation_repair_appends_analysis_without_changing_hard_constraints():
    composition = _sixteen_bar_composition()
    context = _safe_repair_analysis_context(composition)
    assert "ADVISORY" in context
    prompt = "Hard constraints: key=C major\nStage prompt body"
    diagnostics = [
        ValidationDiagnostic(code="sparse_melody", message="Melody too sparse", severity="error")
    ]
    repaired = _append_repair_diagnostics(prompt, diagnostics, analysis_context=context)
    assert "Hard constraints above remain immutable" in repaired
    assert "ADVISORY" in repaired
    assert repaired.index("Hard constraints: key=C major") < repaired.index("ADVISORY")
