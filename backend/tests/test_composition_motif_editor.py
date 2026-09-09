"""Focused service tests for motif apply orchestration."""

from __future__ import annotations

import asyncio

import pytest

from app.composition_schemas import (
    CompositionV2,
    MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
)
from app.llm_settings import LLMSettings
from app.motif_schemas import MotifApplyError, MotifApplyRequest, MotifDestinationSelector, MotifSourceSelector
from app.services.composition_motif_editor import apply_motif_operation
from app.services.composition_region_patch import canonical_json_dumps
from tests.test_composition_v2_schema import _motif_definition, _motif_source_events, _motif_track, minimal_v2


BAR_TICKS = 1920


def _run(coro):
    return asyncio.run(coro)


def _settings() -> LLMSettings:
    return LLMSettings(providers=(), default_provider=None, request_timeout_seconds=30, temperature=0.2)


def _two_section_composition(**overrides):
    payload = minimal_v2(
        bar_count=4,
        duration_ticks=7680,
        sections=[
            {
                "id": "verse",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "id": "chorus",
                "type": "chorus",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        tracks=[_motif_track()],
        motifs=[_motif_definition()],
    )
    payload.update(overrides)
    return CompositionV2.model_validate(payload)


def _apply_request(composition: CompositionV2, **overrides) -> MotifApplyRequest:
    body = {
        "composition": composition,
        "source": MotifSourceSelector(motif_id="motif-a", occurrence_id="occ-orig"),
        "destination": MotifDestinationSelector(
            section_id="chorus",
            track_id="melody-1",
            start_bar=3,
        ),
        "operation": "repeat",
        "parameters": {},
    }
    body.update(overrides)
    return MotifApplyRequest.model_validate(body)


def test_motif_apply_self_overlap_rejected():
    composition = _two_section_composition()
    request = _apply_request(
        composition,
        destination=MotifDestinationSelector(section_id="verse", track_id="melody-1", start_bar=1),
    )

    with pytest.raises(MotifApplyError) as exc_info:
        _run(apply_motif_operation(request, _settings()))

    assert exc_info.value.code == "motif_overlap_rejected"


def test_motif_apply_replaces_existing_usage_and_reconciles():
    repeat_events = [
        {"type": "note", "pitch": "G4", "start_tick": 3840, "duration_ticks": 480, "velocity": 70, "id": "r1"},
        {"type": "note", "pitch": "A4", "start_tick": 4320, "duration_ticks": 480, "velocity": 70, "id": "r2"},
        {"type": "note", "pitch": "B4", "start_tick": 4800, "duration_ticks": 480, "velocity": 70, "id": "r3"},
    ]
    composition = _two_section_composition(
        tracks=[_motif_track(events=_motif_source_events() + repeat_events)],
        motifs=[
            _motif_definition(
                occurrences=[
                    {
                        "id": "occ-orig",
                        "track_id": "melody-1",
                        "event_ids": ["n1", "n2", "n3"],
                        "relationship": "original",
                    },
                    {
                        "id": "occ-repeat",
                        "track_id": "melody-1",
                        "event_ids": ["r1", "r2", "r3"],
                        "relationship": "repeat",
                    },
                ]
            )
        ],
    )
    request = _apply_request(
        composition,
        source=MotifSourceSelector(motif_id="motif-a", occurrence_id="occ-orig"),
    )

    outcome = _run(apply_motif_operation(request, _settings()))

    motif = next(item for item in outcome.composition.motifs if item.id == "motif-a")
    assert all(item.id != "occ-repeat" for item in motif.occurrences)
    assert MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED in outcome.warnings
    assert outcome.result.new_occurrence_id in {item.id for item in motif.occurrences}


def test_motif_apply_original_source_protection():
    repeat_events = [
        {"type": "note", "pitch": "G4", "start_tick": 3840, "duration_ticks": 480, "velocity": 70, "id": "r1"},
        {"type": "note", "pitch": "A4", "start_tick": 4320, "duration_ticks": 480, "velocity": 70, "id": "r2"},
        {"type": "note", "pitch": "B4", "start_tick": 4800, "duration_ticks": 480, "velocity": 70, "id": "r3"},
    ]
    composition = _two_section_composition(
        tracks=[_motif_track(events=_motif_source_events() + repeat_events)],
        motifs=[
            _motif_definition(
                occurrences=[
                    {
                        "id": "occ-orig",
                        "track_id": "melody-1",
                        "event_ids": ["n1", "n2", "n3"],
                        "relationship": "original",
                    },
                    {
                        "id": "occ-repeat",
                        "track_id": "melody-1",
                        "event_ids": ["r1", "r2", "r3"],
                        "relationship": "repeat",
                    },
                ]
            )
        ],
    )
    request = _apply_request(
        composition,
        source=MotifSourceSelector(motif_id="motif-a", occurrence_id="occ-repeat"),
        destination=MotifDestinationSelector(section_id="verse", track_id="melody-1", start_bar=1, start_tick=960),
    )

    with pytest.raises(MotifApplyError) as exc_info:
        _run(apply_motif_operation(request, _settings()))

    assert exc_info.value.code == "motif_source_protection"


def test_motif_apply_partial_tie_collision_rejected():
    events = _motif_source_events()
    events.extend(
        [
            {
                "type": "note",
                "pitch": "C4",
                "start_tick": 4800,
                "duration_ticks": 480,
                "velocity": 80,
                "id": "tie-start",
                "tie": {"group_id": "tie-x", "type": "start"},
            },
            {
                "type": "note",
                "pitch": "C4",
                "start_tick": 5280,
                "duration_ticks": 480,
                "velocity": 80,
                "id": "tie-stop",
                "tie": {"group_id": "tie-x", "type": "stop"},
            },
        ]
    )
    composition = _two_section_composition(tracks=[_motif_track(events=events)])

    with pytest.raises(MotifApplyError) as exc_info:
        _run(apply_motif_operation(_apply_request(composition), _settings()))

    assert exc_info.value.code == "motif_boundary_crossing_tie_chain"


def test_motif_apply_preserves_destination_track_metadata():
    track = _motif_track(
        expression=96,
        dynamic_marks=[{"tick": 0, "level": "mf"}],
        sustain_pedals=[{"start_tick": 0, "duration_ticks": 480}],
    )
    composition = _two_section_composition(tracks=[track])
    original_track = composition.tracks[0]

    outcome = _run(apply_motif_operation(_apply_request(composition), _settings()))
    updated_track = next(item for item in outcome.composition.tracks if item.id == "melody-1")

    assert updated_track.expression == original_track.expression
    assert updated_track.dynamic_marks == original_track.dynamic_marks
    assert updated_track.sustain_pedals == original_track.sustain_pedals


def test_motif_apply_failed_operation_is_atomic():
    composition = _two_section_composition()
    original_dump = composition.model_dump(mode="json")
    request = _apply_request(
        composition,
        destination=MotifDestinationSelector(section_id="verse", track_id="melody-1", start_bar=1),
    )

    with pytest.raises(MotifApplyError):
        _run(apply_motif_operation(request, _settings()))

    assert composition.model_dump(mode="json") == original_dump


def test_motif_apply_has_no_symbolic_placeholders():
    outcome = _run(apply_motif_operation(_apply_request(_two_section_composition()), _settings()))
    dumped = outcome.composition.model_dump(mode="json")
    for motif in dumped["motifs"]:
        assert "pitch" not in motif
        assert "notes" not in motif
        assert "events" not in motif
        for occurrence in motif["occurrences"]:
            assert "pitch" not in occurrence
            assert "notes" not in occurrence
            assert "events" not in occurrence


def test_motif_apply_preserves_outside_span_events_byte_equal():
    composition = _two_section_composition()
    original_n4 = next(event for event in composition.tracks[0].events if event.id == "n4")

    outcome = _run(apply_motif_operation(_apply_request(composition), _settings()))
    updated_n4 = next(event for event in outcome.composition.tracks[0].events if event.id == "n4")

    assert canonical_json_dumps(updated_n4.model_dump(mode="json")) == canonical_json_dumps(
        original_n4.model_dump(mode="json")
    )
