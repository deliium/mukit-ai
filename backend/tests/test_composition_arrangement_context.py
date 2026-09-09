"""Tests for composition arrangement source-context extraction."""

from __future__ import annotations

import copy
from unittest.mock import patch

import pytest

from app.arrangement_schemas import (
    ARRANGEMENT_DEFAULT_CONTEXT_BUDGET_CHARS,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_arrangement_context import (
    ARRANGEMENT_CONTEXT_VERSION,
    ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES,
    arrangement_context_prompt_payload,
    build_arrangement_source_context,
    lookup_source_note,
)
from app.services.composition_edit_fingerprint import composition_edit_fingerprint
from app.services.instrument_catalog import get_catalog
from tests.test_composition_v2_schema import minimal_v2


def _part(
    part_id: str,
    instrument_id: str,
    *,
    role: str | None = None,
    source_track_ids: list[str] | None = None,
) -> dict:
    return {
        "part_id": part_id,
        "instrument_id": instrument_id,
        "role": role,
        "source_track_ids": source_track_ids or [],
        "doubling_policy": "none",
    }


def _piano_sketch_v2(**overrides) -> CompositionV2:
    """Acceptance baseline: piano melody + piano accompaniment + bass."""
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
                "id": "piano-melody",
                "name": "Piano Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"id": "m1", "pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 84},
                    {"id": "m2", "pitch": "G4", "start_tick": 480, "duration_ticks": 480, "velocity": 82},
                    {"id": "m3", "pitch": "C5", "start_tick": 960, "duration_ticks": 960, "velocity": 88},
                    {"id": "m4", "pitch": "D5", "start_tick": 1920, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "piano-accomp",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "a1", "pitch": "C3", "start_tick": 0, "duration_ticks": 960, "velocity": 64},
                    {"id": "a2", "pitch": "E3", "start_tick": 0, "duration_ticks": 960, "velocity": 60},
                    {"id": "a3", "pitch": "G3", "start_tick": 0, "duration_ticks": 960, "velocity": 60},
                    {"id": "a4", "pitch": "F3", "start_tick": 1920, "duration_ticks": 960, "velocity": 62},
                    {"id": "a5", "pitch": "A3", "start_tick": 1920, "duration_ticks": 960, "velocity": 58},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "events": [
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 1920, "velocity": 70},
                    {"id": "b2", "pitch": "F2", "start_tick": 1920, "duration_ticks": 1920, "velocity": 68},
                ],
            },
        ],
        harmony=[
            {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
            {"start_tick": 1920, "duration_ticks": 1920, "chord": "F"},
        ],
    )
    data.update(overrides)
    return CompositionV2.model_validate(data)


def _acceptance_instrumentation() -> dict:
    return {
        "before": [
            _part(
                "b-melody",
                "acoustic_grand_piano",
                role="melody",
                source_track_ids=["piano-melody"],
            ),
            _part(
                "b-accomp",
                "acoustic_grand_piano",
                role="harmony",
                source_track_ids=["piano-accomp"],
            ),
            _part("b-bass", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
        ],
        "after": [
            _part("a-piano", "acoustic_grand_piano", role="melody"),
            _part("a-cello", "cello", role="bass"),
            _part("a-strings", "string_ensemble_1", role="harmony"),
        ],
    }


def _request(composition: CompositionV2, **overrides) -> CompositionArrangementPreviewRequest:
    payload = {
        "composition": composition,
        "operation": "piano_to_ensemble",
        "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
        "protected_track_ids": [],
        "instrumentation": _acceptance_instrumentation(),
        "candidate_count": 1,
        "preserve_melody": True,
        "preserve_harmony": True,
    }
    payload.update(overrides)
    return CompositionArrangementPreviewRequest.model_validate(payload)


def test_acceptance_baseline_separates_piano_roles_and_target_ranges():
    composition = _piano_sketch_v2()
    context = build_arrangement_source_context(_request(composition))
    assert context.version == ARRANGEMENT_CONTEXT_VERSION
    assert context.before_part_count == 3
    assert context.after_part_count == 3
    assert context.instrument_counts.get("acoustic_grand_piano") == 2
    authored = context.authored_role_counts
    assert authored.get("melody") == 1
    assert authored.get("harmony") == 1
    assert authored.get("bass") == 1
    assert context.melody_lead.source == "declared"
    assert context.melody_lead.track_ids == ("piano-melody",)

    cello = next(item for item in context.target_ranges if item.instrument_id == "cello")
    strings = next(item for item in context.target_ranges if item.instrument_id == "string_ensemble_1")
    assert cello.playable_low == 36
    assert cello.playable_high == 72
    assert strings.range_policy == "absolute"
    assert context.analysis_advisory is True


def test_deterministic_source_note_references():
    composition = _piano_sketch_v2()
    request = _request(composition)
    first = build_arrangement_source_context(request)
    second = build_arrangement_source_context(request)
    assert [note.ref for note in first.source_notes] == [note.ref for note in second.source_notes]
    assert [note.pitch for note in first.source_notes] == [note.pitch for note in second.source_notes]
    assert first.source_notes[0].ref == "sn00001"
    assert first.edit_source_fingerprint == composition_edit_fingerprint(composition)


def test_id_less_events_receive_refs():
    composition = _piano_sketch_v2(
        tracks=[
            {
                "id": "piano-melody",
                "name": "Piano Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 84},
                    {"pitch": "G4", "start_tick": 480, "duration_ticks": 480, "velocity": 82},
                ],
            },
            {
                "id": "piano-accomp",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"pitch": "C3", "start_tick": 0, "duration_ticks": 960, "velocity": 64},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "events": [
                    {"pitch": "C2", "start_tick": 0, "duration_ticks": 1920, "velocity": 70},
                ],
            },
        ]
    )
    context = build_arrangement_source_context(_request(composition))
    assert len(context.source_notes) == 4
    assert all(note.source_event_ids == () for note in context.source_notes)
    assert [note.ref for note in context.source_notes] == [
        "sn00001",
        "sn00002",
        "sn00003",
        "sn00004",
    ]


def test_tie_chains_collapse_to_one_logical_ref():
    composition = _piano_sketch_v2(
        tracks=[
            {
                "id": "piano-melody",
                "name": "Piano Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "id": "t1",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "tie": {"group_id": "tie-a", "type": "start"},
                    },
                    {
                        "id": "t2",
                        "pitch": "C4",
                        "start_tick": 480,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "tie": {"group_id": "tie-a", "type": "stop"},
                    },
                ],
            },
            {
                "id": "piano-accomp",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "a1", "pitch": "G3", "start_tick": 0, "duration_ticks": 960, "velocity": 60},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "events": [
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 960, "velocity": 70},
                ],
            },
        ]
    )
    context = build_arrangement_source_context(_request(composition))
    melody_notes = [note for note in context.source_notes if note.track_id == "piano-melody"]
    assert len(melody_notes) == 1
    assert melody_notes[0].duration_ticks == 960
    assert melody_notes[0].tie_group_id == "tie-a"
    assert melody_notes[0].member_count == 2
    assert set(melody_notes[0].source_event_ids) == {"t1", "t2"}


def test_same_instrument_different_roles_are_distinct():
    composition = _piano_sketch_v2()
    context = build_arrangement_source_context(_request(composition))
    piano_views = [view for view in context.track_roles if view.instrument_id == "acoustic_grand_piano"]
    roles = sorted(view.authored_role for view in piano_views)
    assert roles == ["harmony", "melody"]
    assert context.instrument_counts["acoustic_grand_piano"] == 2


def test_ambiguous_roles_warning_when_inferred_melody_conflicts():
    composition = _piano_sketch_v2(
        tracks=[
            {
                "id": "piano-melody",
                "name": "Line A",
                "instrument": "piano",
                "role": "other",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"id": "m1", "pitch": "E5", "start_tick": 0, "duration_ticks": 480, "velocity": 84},
                    {"id": "m2", "pitch": "G5", "start_tick": 480, "duration_ticks": 480, "velocity": 82},
                ],
            },
            {
                "id": "piano-accomp",
                "name": "Line B",
                "instrument": "piano",
                "role": "other",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "a1", "pitch": "F5", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"id": "a2", "pitch": "A5", "start_tick": 480, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "events": [
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 1920, "velocity": 70},
                ],
            },
        ]
    )
    instrumentation = {
        "before": [
            _part("b1", "acoustic_grand_piano", role="other", source_track_ids=["piano-melody"]),
            _part("b2", "acoustic_grand_piano", role="other", source_track_ids=["piano-accomp"]),
            _part("b3", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
        ],
        "after": _acceptance_instrumentation()["after"],
    }
    context = build_arrangement_source_context(
        _request(composition, instrumentation=instrumentation)
    )
    assert context.melody_lead.source in {"inferred", "ambiguous", "none"}
    # Authored roles remain authoritative on track views.
    for view in context.track_roles:
        if view.track_id in {"piano-melody", "piano-accomp"}:
            assert view.effective_role == "other"
            assert view.authored_role == "other"


def test_explicit_inventory_counts_and_protected_sets():
    composition = _piano_sketch_v2()
    request = _request(
        composition,
        source_track_ids=["piano-accomp", "bass-1"],
        protected_track_ids=["piano-melody"],
        instrumentation={
            "before": [
                _part(
                    "b-accomp",
                    "acoustic_grand_piano",
                    role="harmony",
                    source_track_ids=["piano-accomp"],
                ),
                _part("b-bass", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
            ],
            "after": [
                _part("a-cello", "cello", role="bass"),
                _part("a-strings", "string_ensemble_1", role="harmony"),
            ],
        },
        operation="orchestrate_selected_tracks",
    )
    context = build_arrangement_source_context(request)
    assert context.before_part_count == 2
    assert context.after_part_count == 2
    assert context.protected_track_ids == ("piano-melody",)
    assert context.melody_lead.source == "protected"
    assert context.melody_lead.track_ids == ("piano-melody",)
    assert set(context.source_track_ids) == {"piano-accomp", "bass-1"}
    assert len(context.actual_before_inventory) == 2


def test_empty_harmony_is_valid():
    composition = _piano_sketch_v2(harmony=[])
    context = build_arrangement_source_context(_request(composition))
    assert "empty_harmony_context" in context.warning_codes
    assert context.harmony_summary == ()
    payload = arrangement_context_prompt_payload(context)
    assert payload["harmony_advisory"] == []


def test_mixed_meter_context_builds():
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
                    "id": "piano-melody",
                    "name": "Melody",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"id": "m1", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    ],
                },
                {
                    "id": "piano-accomp",
                    "name": "Accomp",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 2,
                    "events": [
                        {"id": "a1", "pitch": "G3", "start_tick": 0, "duration_ticks": 480, "velocity": 60},
                    ],
                },
                {
                    "id": "bass-1",
                    "name": "Bass",
                    "instrument": "acoustic bass",
                    "role": "bass",
                    "midi_program": 32,
                    "channel": 3,
                    "events": [
                        {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 480, "velocity": 70},
                    ],
                },
            ],
        )
    )
    context = build_arrangement_source_context(_request(composition))
    assert context.timeline.bar_boundaries[1] == 1440
    assert len(context.source_notes) == 3


def test_drums_included_in_refs_and_inventory():
    composition = _piano_sketch_v2(
        tracks=[
            {
                "id": "piano-melody",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"id": "m1", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "piano-accomp",
                "name": "Accomp",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "a1", "pitch": "G3", "start_tick": 0, "duration_ticks": 480, "velocity": 60},
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
                    {"id": "d1", "pitch": "C2", "start_tick": 0, "duration_ticks": 240, "velocity": 90},
                    {"id": "d2", "pitch": "D2", "start_tick": 480, "duration_ticks": 240, "velocity": 85},
                ],
            },
        ]
    )
    request = _request(
        composition,
        source_track_ids=["piano-melody", "piano-accomp", "drums-1"],
        instrumentation={
            "before": [
                _part(
                    "b-melody",
                    "acoustic_grand_piano",
                    role="melody",
                    source_track_ids=["piano-melody"],
                ),
                _part(
                    "b-accomp",
                    "acoustic_grand_piano",
                    role="harmony",
                    source_track_ids=["piano-accomp"],
                ),
                _part(
                    "b-drums",
                    "standard_drum_kit",
                    role="drums",
                    source_track_ids=["drums-1"],
                ),
            ],
            "after": [
                _part("a-piano", "acoustic_grand_piano", role="melody"),
                _part("a-drums", "standard_drum_kit", role="drums"),
                _part("a-strings", "string_ensemble_1", role="harmony"),
            ],
        },
    )
    context = build_arrangement_source_context(request)
    drum_notes = [note for note in context.source_notes if note.is_drum]
    assert len(drum_notes) == 2
    drum_target = next(item for item in context.target_ranges if item.instrument_id == "standard_drum_kit")
    assert drum_target.range_policy == "unknown"


def test_selected_note_refs_are_non_truncatable():
    composition = _piano_sketch_v2()
    context = build_arrangement_source_context(
        _request(
            composition,
            options={"context_budget_chars": 3_000, "max_repairs": 0},
        )
    )
    payload = arrangement_context_prompt_payload(context)
    assert len(payload["selected_note_refs"]) == len(context.source_notes)
    assert len(payload["selected_note_refs"]) >= 1
    # Lookup round-trip
    first = lookup_source_note(context, context.source_notes[0].ref)
    assert first.pitch == context.source_notes[0].pitch


def test_advisory_truncation_order_prefers_dropping_analysis():
    composition = _piano_sketch_v2()
    with patch(
        "app.services.composition_arrangement_context.build_llm_analysis_context",
        return_value="ADVISORY " * 400 + "\n[analysis_context_truncated]",
    ):
        context = build_arrangement_source_context(
            _request(
                composition,
                options={"context_budget_chars": 4_500, "max_repairs": 0},
            )
        )
    payload = arrangement_context_prompt_payload(context)
    assert len(payload["selected_note_refs"]) == len(context.source_notes)
    assert context.truncated is True or "context_truncated" in context.warning_codes
    # Note table never lost even when advisory is truncated/dropped.
    assert payload["selected_note_refs"]


def test_over_limit_note_count_rejects_before_provider():
    events = [
        {
            "id": f"n{i}",
            "pitch": "C4",
            "start_tick": i * 30,
            "duration_ticks": 20,
            "velocity": 70,
        }
        for i in range(6)
    ]
    composition = _piano_sketch_v2(
        tracks=[
            {
                "id": "piano-melody",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": events,
            },
            {
                "id": "piano-accomp",
                "name": "Accomp",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "a1", "pitch": "G3", "start_tick": 0, "duration_ticks": 480, "velocity": 60},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "events": [
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 480, "velocity": 70},
                ],
            },
        ],
    )
    with patch(
        "app.services.composition_arrangement_context.ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES",
        5,
    ):
        with pytest.raises(CompositionArrangementError) as exc:
            build_arrangement_source_context(_request(composition))
    assert exc.value.code == "arrangement_request_too_large"
    assert exc.value.http_status == 422
    assert ARRANGEMENT_MAX_SELECTED_LOGICAL_NOTES >= 5


def test_over_budget_note_table_rejects_before_provider():
    # Many notes that fit under the hard count but exceed a tiny context budget.
    events = [
        {
            "id": f"n{i}",
            "pitch": "C4",
            "start_tick": i * 30,
            "duration_ticks": 20,
            "velocity": 70,
        }
        for i in range(80)
    ]
    composition = _piano_sketch_v2(
        duration_ticks=max(7680, 80 * 30),
        tracks=[
            {
                "id": "piano-melody",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": events,
            },
            {
                "id": "piano-accomp",
                "name": "Accomp",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "a1", "pitch": "G3", "start_tick": 0, "duration_ticks": 480, "velocity": 60},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "events": [
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 480, "velocity": 70},
                ],
            },
        ],
    )
    with pytest.raises(CompositionArrangementError) as exc:
        build_arrangement_source_context(
            _request(
                composition,
                options={"context_budget_chars": 1_000, "max_repairs": 0},
            )
        )
    assert exc.value.code == "arrangement_request_too_large"
    assert exc.value.details.get("reason") == "selected_note_table_exceeds_budget"


def test_analysis_failure_fallback_does_not_fail_context():
    composition = _piano_sketch_v2()
    with patch(
        "app.services.composition_arrangement_context.analyze_composition",
        side_effect=RuntimeError("boom"),
    ):
        context = build_arrangement_source_context(_request(composition))
    assert context.advisory_analysis is None
    assert len(context.source_notes) > 0


def test_source_composition_immutability():
    composition = _piano_sketch_v2()
    before = copy.deepcopy(composition.model_dump(mode="json"))
    request = _request(composition)
    context = build_arrangement_source_context(request)
    assert composition.model_dump(mode="json") == before
    assert request.composition.model_dump(mode="json") == before
    # Context holds an independent copy.
    context.composition.tracks[0].events[0].velocity = 1
    assert composition.tracks[0].events[0].velocity != 1


def test_invalid_source_track_rejected():
    composition = _piano_sketch_v2()
    with pytest.raises(CompositionArrangementError) as exc:
        build_arrangement_source_context(
            _request(composition, source_track_ids=["piano-melody", "missing", "bass-1"])
        )
    assert exc.value.code == "arrangement_invalid_source"
    assert exc.value.http_status == 422


def test_before_inventory_mismatch_rejected():
    composition = _piano_sketch_v2()
    bad = {
        "before": [
            _part("b1", "violin", role="melody", source_track_ids=["piano-melody"]),
            _part(
                "b2",
                "acoustic_grand_piano",
                role="harmony",
                source_track_ids=["piano-accomp"],
            ),
            _part("b3", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
        ],
        "after": _acceptance_instrumentation()["after"],
    }
    with pytest.raises(CompositionArrangementError) as exc:
        build_arrangement_source_context(_request(composition, instrumentation=bad))
    assert exc.value.code == "arrangement_inventory_mismatch"


def test_prompt_payload_marks_harmony_advisory_and_keeps_authored_roles():
    composition = _piano_sketch_v2()
    context = build_arrangement_source_context(_request(composition))
    payload = arrangement_context_prompt_payload(context)
    assert payload["analysis_advisory"] is True
    assert all(item.get("advisory") is True for item in payload["harmony_advisory"])
    track_payload = {item["track_id"]: item for item in payload["tracks"]}
    assert track_payload["piano-melody"]["authored_role"] == "melody"
    assert track_payload["piano-accomp"]["authored_role"] == "harmony"
    catalog = get_catalog()
    assert context.catalog_fingerprint == catalog.fingerprint
    assert context.context_char_count > 0
    assert context.context_char_count <= ARRANGEMENT_DEFAULT_CONTEXT_BUDGET_CHARS or context.truncated
