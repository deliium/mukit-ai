"""Mutation, logging, and normal-limit scale coverage for composition analysis."""

from __future__ import annotations

import copy
import json
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_MAX_MOTIFS,
    ANALYSIS_MAX_SECTION_SUMMARIES,
    ANALYSIS_MAX_SPANS,
    ANALYSIS_MAX_TRACK_SUMMARIES,
    CompositionAnalysisError,
)
from app.composition_schemas import CompositionV2
from app.main import app
from app.services.composition_analysis import analyze_composition, build_llm_analysis_context
from tests.fixtures.analysis import load_analysis_expected_vectors, load_analysis_fixture
from tests.fixtures.analysis.builders import build_scale_composition


# Generous non-flaky ceiling for ~100k-note / 64-track / 512-bar analysis.
SCALE_RUNTIME_CEILING_SECONDS = 180.0
SCALE_RESPONSE_BYTE_CEILING = 2_000_000


def test_mutated_model_after_construction_raises_controlled_error():
    raw = load_analysis_fixture("major_tonality")
    model = CompositionV2.model_validate(raw)
    # Bypass field validators by mutating after construction.
    model.duration_ticks = 100
    with pytest.raises(CompositionAnalysisError) as exc_info:
        analyze_composition(model)
    assert exc_info.value.code == "analysis_invalid_composition"


def test_mutated_event_pitch_raises_controlled_error():
    raw = load_analysis_fixture("major_tonality")
    model = CompositionV2.model_validate(raw)
    model.tracks[0].events[0].pitch = "NOT_A_PITCH"
    with pytest.raises(CompositionAnalysisError) as exc_info:
        analyze_composition(model)
    assert exc_info.value.code == "analysis_invalid_composition"


def test_malformed_json_raises_controlled_error():
    with pytest.raises(CompositionAnalysisError) as exc_info:
        analyze_composition({"schema_version": "composition.v2", "tempo": "fast"})
    assert exc_info.value.code == "analysis_invalid_composition"

    with pytest.raises(CompositionAnalysisError) as exc_info:
        analyze_composition({"schema_version": "composition.v1", "tracks": []})
    assert exc_info.value.code == "analysis_invalid_composition"


def test_route_malformed_json_returns_structured_422():
    client = TestClient(app)
    response = client.post(
        "/analysis/composition",
        json={
            "composition": {
                "schema_version": "composition.v2",
                "tempo": 100,
                "key": "C major",
                "time_signature": "4/4",
                "ticks_per_quarter": 480,
                "bar_count": 1,
                "duration_ticks": 100,
                "sections": [
                    {
                        "type": "verse",
                        "start_bar": 1,
                        "bar_count": 1,
                        "start_tick": 0,
                        "duration_ticks": 1920,
                    }
                ],
                "tracks": [
                    {
                        "id": "bad",
                        "name": "bad",
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
            },
            "scope": {"kind": "composition"},
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "analysis_invalid_composition"
    assert "message" in detail
    assert '"events"' not in response.text


def test_analyzers_leave_pydantic_model_dump_unchanged():
    raw = load_analysis_fixture("chord_inversions_nct")
    model = CompositionV2.model_validate(raw)
    before = model.model_dump(mode="json")
    analyze_composition(model)
    assert model.model_dump(mode="json") == before


def test_caplog_stage_timings_codes_and_sanitized_failures(caplog):
    raw = load_analysis_fixture("major_tonality")
    with caplog.at_level(logging.DEBUG):
        report = analyze_composition(raw)
    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Analysis stage start" in messages or "Composition analysis complete" in messages
    assert "Composition analysis complete" in caplog.text
    assert report.algorithm_version == ANALYSIS_ALGORITHM_VERSION
    # Stage / count extras should not dump note payloads.
    assert "C4-D4-E4-F4" not in messages
    assert '"events"' not in messages

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        with pytest.raises(CompositionAnalysisError):
            analyze_composition({"schema_version": "composition.v2", "tempo": -1})
    error_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "Composition analysis" in error_text or "validation" in error_text.lower() or error_text == ""
    # Must not include raw malformed payload dumps.
    assert "tempo\": -1" not in error_text


def test_caplog_warning_codes_without_fixture_sentinels(caplog):
    expected = load_analysis_expected_vectors()
    sentinels = expected["sentinels"]
    raw = load_analysis_fixture("hygiene_sentinel")
    with caplog.at_level(logging.DEBUG):
        analyze_composition(raw)
        build_llm_analysis_context(analyze_composition(raw))
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert sentinels["joined_pitches"] not in joined
    assert sentinels["label"] not in joined
    for pitch in sentinels["pitch_sequence"]:
        assert pitch not in joined
    # Full JSON / prompt bodies must not appear in log messages.
    assert '"schema_version": "composition.v2"' not in joined
    assert "Hard constraints, user instructions" not in joined
    assert "ADVISORY musical analysis" not in joined


def test_normal_limit_scale_bounded_runtime_caps_and_truncation():
    raw = build_scale_composition(note_count=100_000, track_count=64, bar_count=512)
    note_total = sum(len(track["events"]) for track in raw["tracks"])
    assert note_total >= 99_000
    assert len(raw["tracks"]) == 64
    assert raw["bar_count"] == 512

    before = copy.deepcopy(raw)
    started = time.perf_counter()
    report = analyze_composition(raw)
    elapsed = time.perf_counter() - started
    assert raw == before
    assert elapsed < SCALE_RUNTIME_CEILING_SECONDS

    payload = json.dumps(report.model_dump(mode="json"), separators=(",", ":"))
    assert len(payload) < SCALE_RESPONSE_BYTE_CEILING

    assert len(report.repetition.motifs) <= ANALYSIS_MAX_MOTIFS
    assert len(report.harmony.spans) <= ANALYSIS_MAX_SPANS
    assert len(report.roles.tracks) <= ANALYSIS_MAX_TRACK_SUMMARIES
    assert len(report.section_summaries) <= ANALYSIS_MAX_SECTION_SUMMARIES

    codes = {warning.code for warning in report.warnings}
    assert report.repetition.inference.status == "truncated" or "motif_search_truncated" in codes
    assert len(report.harmony.spans) <= ANALYSIS_MAX_SPANS
    assert len(report.repetition.motifs) <= ANALYSIS_MAX_MOTIFS
    # Explicit truncation or hard caps exercised at import scale.
    assert (
        "motif_search_truncated" in codes
        or "result_truncated" in codes
        or len(report.harmony.spans) == ANALYSIS_MAX_SPANS
        or len(report.repetition.motifs) == ANALYSIS_MAX_MOTIFS
    )
    # Complexity/count assertions rather than microbenchmarks.
    assert report.density.metrics.max_simultaneity >= 1
    assert report.status in {"ok", "partial"}
