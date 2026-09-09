"""Tests for composition development source-context extraction."""

from __future__ import annotations

import copy

import pytest

from app.composition_development_schemas import (
    CompositionDevelopmentError,
    CompositionDevelopmentPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_development_context import (
    build_development_source_context,
    development_context_prompt_payload,
    resolve_development_scope,
)
from app.services.composition_timeline import compile_timeline
from tests.test_composition_v2_schema import minimal_v2


def _request(composition: CompositionV2, **overrides) -> CompositionDevelopmentPreviewRequest:
    payload = {
        "composition": composition,
        "operation": "continue",
        "output_bars": 8,
        "variation_strength": "balanced",
        "development_intent": "continue",
        "candidate_count": 1,
    }
    payload.update(overrides)
    return CompositionDevelopmentPreviewRequest.model_validate(payload)


def _four_bar_piece(**overrides) -> CompositionV2:
    data = minimal_v2(
        bar_count=4,
        duration_ticks=7680,
        sections=[
            {
                "id": "a-section",
                "type": "verse",
                "label": "A",
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
                    {"id": "n1", "pitch": "C4", "start_tick": 5760, "duration_ticks": 480, "velocity": 80},
                    {"id": "n2", "pitch": "E4", "start_tick": 6240, "duration_ticks": 480, "velocity": 80},
                    {"id": "n3", "pitch": "G4", "start_tick": 6720, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [
                    {"id": "b1", "pitch": "C2", "start_tick": 5760, "duration_ticks": 1920, "velocity": 70},
                ],
            },
        ],
        harmony=[
            {"start_tick": 0, "duration_ticks": 3840, "chord": "C"},
            {"start_tick": 3840, "duration_ticks": 3840, "chord": "G"},
        ],
    )
    data.update(overrides)
    return CompositionV2.model_validate(data)


def test_default_source_uses_trailing_section_for_continue():
    composition = _four_bar_piece()
    request = _request(composition)
    scope = resolve_development_scope(composition, request)
    assert scope.defaulted_source is True
    assert scope.source_start_bar == 1
    assert scope.source_end_bar == 4
    assert scope.output_start_bar == 5
    assert scope.output_bars == 8
    assert scope.seam_tick == composition.duration_ticks


def test_vary_section_resolves_explicit_bars():
    composition = _four_bar_piece()
    request = _request(
        composition,
        operation="vary_section",
        output_bars=None,
        source={"start_bar": 2, "end_bar": 3},
    )
    scope = resolve_development_scope(composition, request)
    assert scope.operation == "vary_section"
    assert scope.source_start_bar == 2
    assert scope.source_end_bar == 3
    assert scope.output_start_bar == 2
    assert scope.output_end_bar == 3
    assert scope.output_bars == 2
    assert scope.seam_tick == scope.source_start_tick


def test_section_id_resolution_and_unknown_section():
    composition = _four_bar_piece()
    request = _request(composition, source={"section_id": "a-section"}, output_bars=4)
    scope = resolve_development_scope(composition, request)
    assert scope.source_section is not None
    assert scope.source_section.section_id == "a-section"

    with pytest.raises(CompositionDevelopmentError) as exc:
        resolve_development_scope(
            composition,
            _request(composition, source={"section_id": "missing"}, output_bars=4),
        )
    assert exc.value.code == "development_invalid_source"


def test_mixed_meter_uses_compiled_boundaries():
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
        )
    )
    timeline = compile_timeline(composition)
    assert timeline.bar_boundaries[1] == 1440
    assert timeline.bar_boundaries[2] == 1440 + 1920
    request = _request(composition, output_bars=2)
    context = build_development_source_context(request)
    assert context.scope.seam_tick == composition.duration_ticks
    assert context.active_time_signature == "4/4"


def test_empty_harmony_and_motifs_are_valid():
    composition = CompositionV2.model_validate(minimal_v2(harmony=[], motifs=[]))
    context = build_development_source_context(_request(composition))
    assert "empty_harmony_context" in context.warning_codes
    assert "empty_motif_context" in context.warning_codes
    assert context.recent_harmony == ()
    assert context.motif_exemplars == ()


def test_context_is_deterministic_and_does_not_mutate_source():
    composition = _four_bar_piece()
    before = copy.deepcopy(composition.model_dump(mode="json"))
    request = _request(composition, development_intent="contrast", variation_strength="experimental")
    first = build_development_source_context(request)
    second = build_development_source_context(request)
    assert first.scope.source_start_tick == second.scope.source_start_tick
    assert first.rhythmic_profile == second.rhythmic_profile
    assert first.active_key == second.active_key
    assert composition.model_dump(mode="json") == before


def test_prompt_payload_omits_absolute_pitches_in_relative_notes():
    composition = _four_bar_piece()
    context = build_development_source_context(_request(composition))
    payload = development_context_prompt_payload(context)
    assert payload["tracks"]
    for track in payload["tracks"]:
        for note in track["relative_notes"]:
            assert "pitch" not in note
            assert "pitch_semitone_offset" in note


def test_id_less_and_duplicate_type_sections():
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=4,
            duration_ticks=7680,
            sections=[
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                },
                {
                    "type": "verse",
                    "start_bar": 3,
                    "bar_count": 2,
                    "start_tick": 3840,
                    "duration_ticks": 3840,
                },
            ],
        )
    )
    request = _request(composition, source={"start_bar": 3, "end_bar": 4}, output_bars=4)
    scope = resolve_development_scope(composition, request)
    assert scope.source_section is not None
    assert scope.source_section.section_id is None
    assert scope.source_section.start_bar == 3
