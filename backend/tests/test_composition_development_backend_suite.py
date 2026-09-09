"""Backend regression, scale, and export coverage for composition development (Task 6)."""

from __future__ import annotations

import asyncio
import copy
import logging

import pytest

from app.composition_development_schemas import (
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
    DEVELOPMENT_MAX_OUTPUT_BARS,
)
from app.composition_schemas import CompositionV2
from app.llm_settings import load_llm_settings
from app.services.composition_edit_fingerprint import (
    canonical_edit_json_dumps,
    composition_edit_fingerprint,
    full_document_edit_projection,
)
from app.services.composition_midi import render_midi
from app.services.llm_composition_development import run_composition_development_preview
from app.services.music_json_renderer import render_musicxml
from tests.test_composition_development_patch import _sixteen_bar_a
from tests.test_composition_v2_schema import minimal_v2


def _preview(composition: CompositionV2, **overrides):
    payload = {
        "composition": composition,
        "operation": "continue",
        "output_bars": 4,
        "variation_strength": "balanced",
        "development_intent": "continue",
        "candidate_count": 1,
        "selection": {"provider": "fake", "model": "fake-deterministic"},
        "instruction": "UNIQUE_DEV_INSTRUCTION_MUST_NOT_LOG",
    }
    payload.update(overrides)
    request = CompositionDevelopmentPreviewRequest.model_validate(payload)
    return asyncio.run(run_composition_development_preview(request, settings=load_llm_settings()))


def _metered_piece(time_signature: str, bar_count: int = 4) -> CompositionV2:
    from app.composition_schemas import bar_duration_ticks

    bar_ticks = bar_duration_ticks(time_signature, 480)
    duration = bar_ticks * bar_count
    events = [
        {
            "id": f"n-{i}",
            "pitch": "C4",
            "start_tick": i * bar_ticks,
            "duration_ticks": min(480, bar_ticks),
            "velocity": 80,
        }
        for i in range(bar_count)
    ]
    return CompositionV2.model_validate(
        minimal_v2(
            time_signature=time_signature,
            bar_count=bar_count,
            duration_ticks=duration,
            sections=[
                {
                    "id": "a",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": bar_count,
                    "start_tick": 0,
                    "duration_ticks": duration,
                }
            ],
            tracks=[
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": events,
                },
                {
                    "id": "bass-1",
                    "name": "Bass",
                    "instrument": "bass",
                    "role": "bass",
                    "midi_program": 32,
                    "channel": 2,
                    "events": [
                        {
                            "id": f"b-{i}",
                            "pitch": "C2",
                            "start_tick": i * bar_ticks,
                            "duration_ticks": bar_ticks,
                            "velocity": 70,
                        }
                        for i in range(bar_count)
                    ],
                },
            ],
            harmony=[],
        )
    )


@pytest.fixture(autouse=True)
def _fake_mode(monkeypatch):
    monkeypatch.setenv("LLM_FAKE_MODE", "1")
    monkeypatch.delenv("LLM_FAKE_INJECT_MALFORMED", raising=False)


@pytest.mark.parametrize("time_signature", ["4/4", "3/4", "6/8"])
def test_continue_preserves_prefix_under_meters(time_signature):
    source = _metered_piece(time_signature, bar_count=4)
    source_bytes = canonical_edit_json_dumps(full_document_edit_projection(source))
    response = _preview(source, output_bars=2)
    candidate = response.candidates[0].composition
    assert candidate.bar_count == 6
    # Exact prefix: first N events per track match source serialization for those events.
    for src_track, out_track in zip(source.tracks, candidate.tracks, strict=True):
        assert src_track.id == out_track.id
        for src_ev, out_ev in zip(src_track.events, out_track.events[: len(src_track.events)], strict=True):
            assert src_ev.model_dump(mode="json") == out_ev.model_dump(mode="json")
    # Source document itself unchanged.
    assert canonical_edit_json_dumps(full_document_edit_projection(source)) == source_bytes


def test_mixed_meter_append_uses_ending_meter_duration():
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=3,
            time_signature="3/4",
            time_signature_changes=[{"tick": 1440, "time_signature": "4/4"}],
            duration_ticks=1440 + 1920 + 1920,
            sections=[
                {
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 3,
                    "start_tick": 0,
                    "duration_ticks": 1440 + 1920 + 1920,
                }
            ],
            tracks=[
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"id": "n1", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    ],
                }
            ],
            harmony=[],
        )
    )
    response = _preview(composition, output_bars=2)
    candidate = response.candidates[0].composition
    assert candidate.bar_count == 5
    # Ending meter is 4/4 → two bars = 3840 ticks after seam.
    assert candidate.duration_ticks == composition.duration_ticks + 3840


def test_four_candidates_and_export_smoke():
    source = _sixteen_bar_a()
    source_fp = composition_edit_fingerprint(source)
    response = _preview(source, output_bars=8, candidate_count=4)
    assert len(response.candidates) == 4
    assert {c.edit_source_fingerprint for c in response.candidates} == {source_fp}
    assert len({c.candidate_id for c in response.candidates}) == 4

    candidate = response.candidates[2].composition
    midi = render_midi(candidate)
    musicxml, report = render_musicxml(candidate)
    assert isinstance(midi, (bytes, bytearray)) and len(midi) > 100
    assert "score-partwise" in musicxml or "score-timewise" in musicxml
    assert report is not None


def test_vary_outside_range_exact_and_empty_harmony():
    source = _sixteen_bar_a()
    before = copy.deepcopy(source.model_dump(mode="json"))
    response = _preview(
        source,
        operation="vary_section",
        output_bars=None,
        source={"start_bar": 9, "end_bar": 12},
        development_intent="contrast",
        variation_strength="experimental",
    )
    candidate = response.candidates[0].composition
    assert candidate.bar_count == 16
    # Bars 1-8 and 13-16 events exact outside half-open [start, end).
    start_tick = 8 * 1920
    end_tick = 12 * 1920
    for src_track, out_track in zip(source.tracks, candidate.tracks, strict=True):
        src_outside = [
            e.model_dump(mode="json")
            for e in src_track.events
            if e.start_tick + e.duration_ticks <= start_tick or e.start_tick >= end_tick
        ]
        out_outside = [
            e.model_dump(mode="json")
            for e in out_track.events
            if e.start_tick + e.duration_ticks <= start_tick or e.start_tick >= end_tick
        ]
        assert src_outside == out_outside
    assert source.model_dump(mode="json") == before


def test_over_limit_output_bars_rejected_before_provider(caplog):
    source = _sixteen_bar_a()
    with pytest.raises(Exception):
        CompositionDevelopmentPreviewRequest.model_validate(
            {
                "composition": source,
                "operation": "continue",
                "output_bars": DEVELOPMENT_MAX_OUTPUT_BARS + 1,
                "variation_strength": "balanced",
                "candidate_count": 1,
                "selection": {"provider": "fake"},
            }
        )
    # Ensure no provider stage log was emitted for this invalid request construction.
    assert "Composition development candidate stage" not in caplog.text


def test_development_log_hygiene(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-development-must-never-appear")
    source = _sixteen_bar_a()
    with caplog.at_level(logging.DEBUG):
        response = _preview(source, output_bars=8, candidate_count=2)
    assert len(response.candidates) == 2
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "Composition development preview started" in joined
    assert "Composition development candidate stage" in joined
    assert "sk-development-must-never-appear" not in joined
    assert "UNIQUE_DEV_INSTRUCTION_MUST_NOT_LOG" not in joined
    # Distinctive source pitches / event IDs must not appear in logs.
    assert "m-0" not in joined
    assert '"pitch": "C4"' not in joined
    assert "schema_version" not in joined or "Composition V2 validation" in joined


def test_drums_and_sparse_tracks_continue():
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=4,
            duration_ticks=7680,
            sections=[
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 4,
                    "start_tick": 0,
                    "duration_ticks": 7680,
                }
            ],
            tracks=[
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"id": "n1", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    ],
                },
                {
                    "id": "drums-1",
                    "name": "Drums",
                    "instrument": "drums",
                    "role": "drums",
                    "midi_program": 0,
                    "channel": 10,
                    "is_drum": True,
                    "events": [
                        {"id": "d1", "pitch": "C2", "start_tick": 0, "duration_ticks": 120, "velocity": 90},
                    ],
                },
                {
                    "id": "pad-1",
                    "name": "Pad",
                    "instrument": "pad",
                    "role": "pad",
                    "midi_program": 88,
                    "channel": 3,
                    "events": [],
                },
            ],
            harmony=[],
        )
    )
    response = _preview(composition, output_bars=2, variation_strength="conservative")
    assert response.candidates[0].composition.bar_count == 6
    assert {t.id for t in response.candidates[0].composition.tracks} == {
        "melody-1",
        "drums-1",
        "pad-1",
    }
