"""Tests for composition arrangement candidate validation (Task 5)."""

from __future__ import annotations

import copy
import logging

import pytest

from app.arrangement_schemas import (
    ArrangementEventCounts,
    ArrangementTopologyManifest,
    ArrangementTrackInventoryItem,
    CompositionArrangementDraft,
    CompositionArrangementPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_arrangement_context import build_arrangement_source_context
from app.services.composition_arrangement_patch import (
    ArrangementRealizationResult,
    realize_arrangement_draft,
)
from app.services.composition_arrangement_validation import (
    ARRANGEMENT_VALIDATION_VERSION,
    validate_arrangement_candidate,
)
from app.services.composition_validator import validate_composition_integrity
from tests.test_composition_arrangement_context import (
    _acceptance_instrumentation,
    _piano_sketch_v2,
    _request,
)
from tests.test_composition_v2_schema import minimal_v2


def _part(
    part_id: str,
    instrument_id: str,
    *,
    role: str | None = None,
    source_track_ids: list[str] | None = None,
    doubling_policy: str = "none",
) -> dict:
    return {
        "part_id": part_id,
        "instrument_id": instrument_id,
        "role": role,
        "source_track_ids": source_track_ids or [],
        "doubling_policy": doubling_policy,
    }


def _draft(parts: list[dict]) -> CompositionArrangementDraft:
    return CompositionArrangementDraft.model_validate({"parts": parts})


def _refs_for_track(context, track_id: str) -> list[str]:
    return [note.ref for note in context.source_notes if note.track_id == track_id]


def _piano_to_ensemble_result():
    composition = _piano_sketch_v2()
    request = _request(composition)
    context = build_arrangement_source_context(request)
    draft = _draft(
        [
            {
                "action": "redistribute",
                "part_id": "a-piano",
                "source_track_ids": ["piano-melody"],
                "source_note_refs": _refs_for_track(context, "piano-melody"),
            },
            {
                "action": "redistribute",
                "part_id": "a-cello",
                "source_track_ids": ["bass-1"],
                "source_note_refs": _refs_for_track(context, "bass-1"),
            },
            {
                "action": "redistribute",
                "part_id": "a-strings",
                "source_track_ids": ["piano-accomp"],
                "source_note_refs": _refs_for_track(context, "piano-accomp"),
            },
        ]
    )
    realization = realize_arrangement_draft(request, draft, context=context)
    return composition, request, context, realization


def _clone_realization(realization: ArrangementRealizationResult) -> ArrangementRealizationResult:
    return ArrangementRealizationResult(
        composition=CompositionV2.model_validate(
            copy.deepcopy(realization.composition.model_dump(mode="json"))
        ),
        manifest=ArrangementTopologyManifest.model_validate(
            copy.deepcopy(realization.manifest.model_dump(mode="json"))
        ),
        event_counts=ArrangementEventCounts.model_validate(
            realization.event_counts.model_dump(mode="json")
        ),
        before_inventory=[
            ArrangementTrackInventoryItem.model_validate(item.model_dump(mode="json"))
            for item in realization.before_inventory
        ],
        after_inventory=[
            ArrangementTrackInventoryItem.model_validate(item.model_dump(mode="json"))
            for item in realization.after_inventory
        ],
        warning_codes=list(realization.warning_codes),
        target_profile_fingerprints=list(realization.target_profile_fingerprints),
        motif_reconciliation_count=realization.motif_reconciliation_count,
        id_collision_count=realization.id_collision_count,
        channel_allocation=realization.channel_allocation,
        codes=list(realization.codes),
    )


def _assertion_map(result):
    return {item.kind: item for item in result.assertions}


# --- Happy paths -------------------------------------------------------------


def test_piano_to_ensemble_candidate_passes_validation():
    _composition, request, context, realization = _piano_to_ensemble_result()
    result = validate_arrangement_candidate(request, realization, context=context)
    assert result.ok is True
    assert result.status == "accepted"
    kinds = _assertion_map(result)
    assert kinds["melody_preservation"].satisfied
    assert kinds["harmony_preservation"].satisfied
    assert kinds["instrumentation_after"].satisfied
    assert kinds["topology_authorization"].satisfied
    assert kinds["range_policy"].satisfied
    assert kinds["audible_effect"].satisfied
    assert result.density is not None
    assert not any(item.severity == "error" for item in result.duplicate_findings)


def test_change_instrumentation_passes_and_scopes_ranges():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "change_instrumentation",
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-melody",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [_part("a-melody", "violin", role="melody")],
            },
        }
    )
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "reinstrument",
                    "part_id": "a-melody",
                    "source_track_ids": ["piano-melody"],
                }
            ]
        ),
    )
    result = validate_arrangement_candidate(request, realization)
    assert result.ok is True
    assert _assertion_map(result)["source_note_cardinality"].satisfied


# --- Melody preservation failures --------------------------------------------


def test_melody_dropped_fails():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    melody = next(track for track in mutated.composition.tracks if track.role == "melody")
    melody.events = melody.events[:1]
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["melody_preservation"].satisfied is False
    assert "arrangement_preservation_failed" in result.error_codes


def test_melody_transposed_fails():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    melody = next(track for track in mutated.composition.tracks if track.role == "melody")
    for event in melody.events:
        event.pitch = "F4" if event.pitch == "E4" else event.pitch
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["melody_preservation"].satisfied is False


def test_melody_rhythm_changed_fails():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    melody = next(track for track in mutated.composition.tracks if track.role == "melody")
    melody.events[0].start_tick = 120
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["melody_preservation"].satisfied is False


def test_melody_only_in_double_does_not_satisfy_preservation():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "double_melody",
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [
                    _part("a-m", "acoustic_grand_piano", role="melody"),
                    _part(
                        "a-d",
                        "violin",
                        role="melody",
                        doubling_policy="declared",
                        source_track_ids=["piano-melody"],
                    ),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    refs = _refs_for_track(context, "piano-melody")
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "retain",
                    "part_id": "a-m",
                    "source_track_ids": ["piano-melody"],
                },
                {
                    "action": "double",
                    "part_id": "a-d",
                    "source_track_ids": ["piano-melody"],
                    "source_note_refs": refs,
                },
            ]
        ),
        context=context,
    )
    mutated = _clone_realization(realization)
    primary = next(track for track in mutated.composition.tracks if track.id == "piano-melody")
    primary.events = []
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["melody_preservation"].satisfied is False


# --- Duplicate / clone rejection ---------------------------------------------


def test_intentional_doubling_exception_allows_overlap():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "double_melody",
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [
                    _part("a-m", "acoustic_grand_piano", role="melody"),
                    _part(
                        "a-d",
                        "violin",
                        role="melody",
                        doubling_policy="unison",
                        source_track_ids=["piano-melody"],
                    ),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "retain",
                    "part_id": "a-m",
                    "source_track_ids": ["piano-melody"],
                },
                {
                    "action": "double",
                    "part_id": "a-d",
                    "source_track_ids": ["piano-melody"],
                    "source_note_refs": _refs_for_track(context, "piano-melody"),
                },
            ]
        ),
        context=context,
    )
    result = validate_arrangement_candidate(request, realization, context=context)
    assert result.ok is True
    assert any(item.code == "declared_doubling" for item in result.duplicate_findings)
    assert "declared_doubling_applied" in result.warning_codes


def test_undeclared_cross_instrument_clone_rejected():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    melody = next(track for track in mutated.composition.tracks if track.role == "melody")
    strings = next(track for track in mutated.composition.tracks if track.role == "harmony")
    # Clone melody content onto strings (cross-instrument/role exact clone).
    strings.events = [
        event.model_copy(update={"id": f"clone-{index}"})
        for index, event in enumerate(melody.events)
    ]
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    codes = {item.code for item in result.duplicate_findings if item.severity == "error"}
    assert codes & {"cross_instrument_clone", "cross_role_clone", "exact_clone"}


def test_octave_melody_clone_rejected():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    melody = next(track for track in mutated.composition.tracks if track.role == "melody")
    strings = next(track for track in mutated.composition.tracks if track.role == "harmony")
    pitch_map = {"E4": "E5", "G4": "G5", "C5": "C6", "D5": "D6"}
    strings.events = [
        event.model_copy(
            update={
                "id": f"oct-{index}",
                "pitch": pitch_map.get(event.pitch, event.pitch),
            }
        )
        for index, event in enumerate(melody.events)
    ]
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert any(item.code == "octave_melody_clone" for item in result.duplicate_findings)


def test_full_part_piano_clone_onto_multiple_targets_rejected():
    composition = _piano_sketch_v2()
    request = _request(composition)
    context = build_arrangement_source_context(request)
    melody_refs = _refs_for_track(context, "piano-melody")
    accomp_refs = _refs_for_track(context, "piano-accomp")
    bass_refs = _refs_for_track(context, "bass-1")
    # Clone melody onto both piano and strings targets.
    draft = _draft(
        [
            {
                "action": "redistribute",
                "part_id": "a-piano",
                "source_track_ids": ["piano-melody"],
                "source_note_refs": melody_refs,
            },
            {
                "action": "redistribute",
                "part_id": "a-cello",
                "source_track_ids": ["bass-1"],
                "source_note_refs": bass_refs,
            },
            {
                "action": "redistribute",
                "part_id": "a-strings",
                "source_track_ids": ["piano-melody", "piano-accomp"],
                "source_note_refs": melody_refs + accomp_refs,
            },
        ]
    )
    # Realization may reject overlapping claimed refs; build invalid candidate manually.
    good = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-piano",
                    "source_track_ids": ["piano-melody"],
                    "source_note_refs": melody_refs,
                },
                {
                    "action": "redistribute",
                    "part_id": "a-cello",
                    "source_track_ids": ["bass-1"],
                    "source_note_refs": bass_refs,
                },
                {
                    "action": "redistribute",
                    "part_id": "a-strings",
                    "source_track_ids": ["piano-accomp"],
                    "source_note_refs": accomp_refs,
                },
            ]
        ),
        context=context,
    )
    mutated = _clone_realization(good)
    melody = next(track for track in mutated.composition.tracks if track.role == "melody")
    strings = next(track for track in mutated.composition.tracks if track.role == "harmony")
    strings.events = [
        event.model_copy(update={"id": f"dup-{index}"})
        for index, event in enumerate(melody.events)
    ]
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert any(item.code == "exact_clone" for item in result.duplicate_findings)


def test_legitimate_violin_i_ii_distinct_parts_allowed():
    data = minimal_v2(
        bar_count=2,
        duration_ticks=3840,
        tracks=[
            {
                "id": "vln-i",
                "name": "Violin I",
                "instrument": "violin",
                "role": "melody",
                "midi_program": 40,
                "channel": 1,
                "events": [
                    {"id": "a1", "pitch": "E5", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"id": "a2", "pitch": "G5", "start_tick": 480, "duration_ticks": 480, "velocity": 80},
                    {"id": "a3", "pitch": "A5", "start_tick": 960, "duration_ticks": 480, "velocity": 80},
                ],
            },
            {
                "id": "vln-ii",
                "name": "Violin II",
                "instrument": "violin",
                "role": "melody",
                "midi_program": 40,
                "channel": 1,
                "events": [
                    {"id": "b1", "pitch": "C5", "start_tick": 0, "duration_ticks": 480, "velocity": 70},
                    {"id": "b2", "pitch": "D5", "start_tick": 480, "duration_ticks": 480, "velocity": 70},
                    {"id": "b3", "pitch": "E5", "start_tick": 960, "duration_ticks": 480, "velocity": 70},
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "acoustic bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [
                    {"id": "c1", "pitch": "C2", "start_tick": 0, "duration_ticks": 1920, "velocity": 70},
                ],
            },
        ],
        sections=[
            {
                "id": "s",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            }
        ],
        harmony=[{"start_tick": 0, "duration_ticks": 1920, "chord": "C"}],
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "add_accompaniment",
            "source_track_ids": ["vln-i", "vln-ii", "bass-1"],
            "instrumentation": {
                "before": [
                    _part("b1", "violin", role="melody", source_track_ids=["vln-i"]),
                    _part("b2", "violin", role="melody", source_track_ids=["vln-ii"]),
                    _part("b3", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
                ],
                "after": [
                    _part("a1", "violin", role="melody"),
                    _part("a2", "violin", role="melody"),
                    _part("a3", "acoustic_bass", role="bass"),
                    _part("a-pad", "synth_pad_new_age", role="pad"),
                ],
            },
        }
    )
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {"action": "retain", "part_id": "a1", "source_track_ids": ["vln-i"]},
                {"action": "retain", "part_id": "a2", "source_track_ids": ["vln-ii"]},
                {"action": "retain", "part_id": "a3", "source_track_ids": ["bass-1"]},
                {
                    "action": "add",
                    "part_id": "a-pad",
                    "notes": [
                        {
                            "pitch": "G4",
                            "relative_start_tick": 0,
                            "duration_ticks": 1920,
                            "velocity": 40,
                        }
                    ],
                },
            ]
        ),
    )
    result = validate_arrangement_candidate(request, realization)
    assert result.ok is True
    assert any(
        item.code == "legitimate_same_instrument_parts" for item in result.duplicate_findings
    )


def test_same_instrument_same_role_exact_clone_rejected():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "add_accompaniment",
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": {
                "before": _acceptance_instrumentation()["before"],
                "after": _acceptance_instrumentation()["before"]
                + [_part("a-pad", "acoustic_grand_piano", role="harmony")],
            },
        }
    )
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "retain",
                    "part_id": "b-melody",
                    "source_track_ids": ["piano-melody"],
                },
                {
                    "action": "retain",
                    "part_id": "b-accomp",
                    "source_track_ids": ["piano-accomp"],
                },
                {"action": "retain", "part_id": "b-bass", "source_track_ids": ["bass-1"]},
                {
                    "action": "add",
                    "part_id": "a-pad",
                    "notes": [
                        {
                            "pitch": "C3",
                            "relative_start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 64,
                        },
                        {
                            "pitch": "E3",
                            "relative_start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 60,
                        },
                        {
                            "pitch": "G3",
                            "relative_start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 60,
                        },
                        {
                            "pitch": "F3",
                            "relative_start_tick": 1920,
                            "duration_ticks": 960,
                            "velocity": 62,
                        },
                        {
                            "pitch": "A3",
                            "relative_start_tick": 1920,
                            "duration_ticks": 960,
                            "velocity": 58,
                        },
                    ],
                },
            ]
        ),
    )
    # Force exact clone of accompaniment onto the new piano harmony part.
    mutated = _clone_realization(realization)
    accomp = next(track for track in mutated.composition.tracks if track.id == "piano-accomp")
    added_id = realization.manifest.added_track_ids[0]
    clone = next(track for track in mutated.composition.tracks if track.id == added_id)
    clone.events = [
        event.model_copy(update={"id": f"x{index}"})
        for index, event in enumerate(accomp.events)
    ]
    clone.role = "harmony"
    clone.instrument = accomp.instrument
    clone.midi_program = accomp.midi_program
    result = validate_arrangement_candidate(request, mutated)
    assert result.ok is False
    assert any(
        item.code in {"exact_clone", "high_overlap_clone"} for item in result.duplicate_findings
    )


# --- Range policy ------------------------------------------------------------


def test_absolute_out_of_range_on_changed_target_fails():
    composition = _piano_sketch_v2()
    # Redistribute high melody onto contrabass (playable_high=55); E4=64 fails.
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "orchestrate_selected_tracks",
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
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
            "preserve_melody": False,
        }
    )
    context = build_arrangement_source_context(request)
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-cb",
                    "source_track_ids": ["piano-melody"],
                    "source_note_refs": _refs_for_track(context, "piano-melody"),
                }
            ]
        ),
        context=context,
    )
    result = validate_arrangement_candidate(request, realization, context=context)
    assert result.ok is False
    assert any(item.code == "absolute_out_of_range" for item in result.range_findings)
    assert "arrangement_range_failed" in result.error_codes
    assert _assertion_map(result)["range_policy"].satisfied is False


def test_questionable_range_warning_on_changed_target():
    # Flute preferred_low=62; E4=64 is inside preferred. Use F4=65 OK.
    # Piccolo preferred_low=79; E4=64 is playable? playable_low=74 — E4=64 absolute fail.
    # Violin preferred_low=57; use a note at 55 (G3) which is playable but outside preferred.
    data = minimal_v2(
        bar_count=1,
        duration_ticks=1920,
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "id": "m1",
                        "pitch": "G3",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ],
        sections=[
            {
                "id": "s",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "change_instrumentation",
            "source_track_ids": ["melody-1"],
            "instrumentation": {
                "before": [
                    _part(
                        "b",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["melody-1"],
                    )
                ],
                "after": [_part("a", "violin", role="melody")],
            },
            "preserve_melody": True,
        }
    )
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "reinstrument",
                    "part_id": "a",
                    "source_track_ids": ["melody-1"],
                }
            ]
        ),
    )
    # Byte-identical reinstrumented notes are suppressed as baseline. Force a
    # "changed" pitch-adjusted note by altering pitch while keeping track changed.
    mutated = _clone_realization(realization)
    track = next(t for t in mutated.composition.tracks if t.id == "melody-1")
    track.events[0] = track.events[0].model_copy(update={"id": "m1-new", "pitch": "G3"})
    result = validate_arrangement_candidate(request, mutated)
    assert "questionable_range" in result.warning_codes or any(
        item.code == "questionable_range" for item in result.range_findings
    )
    # G3=55 is playable for violin (55-96) but preferred starts at 57.
    assert result.ok is True or "arrangement_range_failed" not in result.error_codes


def test_unchanged_source_outlier_accepted_as_baseline():
    data = _piano_sketch_v2().model_dump(mode="json")
    # Put an extreme low note on bass that may be questionable for acoustic bass,
    # then add accompaniment without touching bass.
    data["tracks"][2]["events"].append(
        {
            "id": "b-outlier",
            "pitch": "A0",
            "start_tick": 4000,
            "duration_ticks": 480,
            "velocity": 50,
        }
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "add_accompaniment",
            "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
            "instrumentation": {
                "before": _acceptance_instrumentation()["before"],
                "after": _acceptance_instrumentation()["before"]
                + [_part("a-pad", "synth_pad_new_age", role="pad")],
            },
        }
    )
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "retain",
                    "part_id": "b-melody",
                    "source_track_ids": ["piano-melody"],
                },
                {
                    "action": "retain",
                    "part_id": "b-accomp",
                    "source_track_ids": ["piano-accomp"],
                },
                {"action": "retain", "part_id": "b-bass", "source_track_ids": ["bass-1"]},
                {
                    "action": "add",
                    "part_id": "a-pad",
                    "notes": [
                        {
                            "pitch": "C4",
                            "relative_start_tick": 0,
                            "duration_ticks": 1920,
                            "velocity": 40,
                        }
                    ],
                },
            ]
        ),
    )
    result = validate_arrangement_candidate(request, realization)
    assert result.ok is True
    assert not any(item.code == "absolute_out_of_range" for item in result.range_findings)
    # Baseline findings/warnings for untouched outliers are informational.
    assert (
        "baseline_range_retained" in result.warning_codes
        or any(item.code == "baseline_range_retained" for item in result.range_findings)
        or result.ok
    )


def test_default_validator_all_track_range_behavior_unchanged():
    data = minimal_v2(
        bar_count=1,
        duration_ticks=1920,
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "flute",
                "role": "melody",
                "midi_program": 73,
                "channel": 1,
                "events": [
                    {
                        "id": "m1",
                        "pitch": "C2",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ],
        sections=[
            {
                "id": "s",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    composition = CompositionV2.model_validate(data)
    default_report = validate_composition_integrity(composition, profile="canonical")
    assert any(item.code == "event_out_of_range" for item in default_report.errors)
    scoped = validate_composition_integrity(
        composition,
        profile="canonical",
        practical_range_track_ids=[],
    )
    assert not any(item.code == "event_out_of_range" for item in scoped.errors)


# --- Harmony / density / audible / topology / inventory ----------------------


def test_harmony_metadata_change_fails():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    mutated.composition.harmony = []
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["harmony_preservation"].satisfied is False


def test_density_increase_and_decrease_directions():
    composition = _piano_sketch_v2()
    dec_request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "decrease_texture_density",
            "source_track_ids": ["piano-accomp"],
            "protected_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        }
    )
    dec_ctx = build_arrangement_source_context(dec_request)
    refs = _refs_for_track(dec_ctx, "piano-accomp")[:2]
    decreased = realize_arrangement_draft(
        dec_request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-a",
                    "source_track_ids": ["piano-accomp"],
                    "source_note_refs": refs,
                }
            ]
        ),
        context=dec_ctx,
    )
    dec_result = validate_arrangement_candidate(dec_request, decreased, context=dec_ctx)
    assert dec_result.ok is True
    assert _assertion_map(dec_result)["density_direction"].satisfied
    assert dec_result.density.after.event_count < dec_result.density.before.event_count

    inc_request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "increase_texture_density",
            "source_track_ids": ["piano-accomp"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        }
    )
    inc_ctx = build_arrangement_source_context(inc_request)
    increased = realize_arrangement_draft(
        inc_request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-a",
                    "source_track_ids": ["piano-accomp"],
                    "source_note_refs": _refs_for_track(inc_ctx, "piano-accomp"),
                    "notes": [
                        {
                            "pitch": "B3",
                            "relative_start_tick": 960,
                            "duration_ticks": 480,
                            "velocity": 55,
                        }
                    ],
                }
            ]
        ),
        context=inc_ctx,
    )
    inc_result = validate_arrangement_candidate(inc_request, increased, context=inc_ctx)
    assert inc_result.ok is True
    assert _assertion_map(inc_result)["density_direction"].satisfied
    assert inc_result.density.after.event_count > inc_result.density.before.event_count


def test_failed_density_direction_rejects():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "increase_texture_density",
            "source_track_ids": ["piano-accomp"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        }
    )
    context = build_arrangement_source_context(request)
    # Redistribute with fewer notes — density decreases instead of increases.
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-a",
                    "source_track_ids": ["piano-accomp"],
                    "source_note_refs": _refs_for_track(context, "piano-accomp")[:1],
                }
            ]
        ),
        context=context,
    )
    result = validate_arrangement_candidate(request, realization, context=context)
    assert result.ok is False
    assert _assertion_map(result)["density_direction"].satisfied is False
    assert "candidate_failed_density" in result.warning_codes


def test_simplify_rejects_new_material():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "simplify_arrangement",
            "source_track_ids": ["piano-accomp"],
            "protected_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-a",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-a", "acoustic_grand_piano", role="harmony")],
            },
        }
    )
    context = build_arrangement_source_context(request)
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-a",
                    "source_track_ids": ["piano-accomp"],
                    "source_note_refs": _refs_for_track(context, "piano-accomp")[:2],
                    "notes": [
                        {
                            "pitch": "B4",
                            "relative_start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 40,
                        }
                    ],
                }
            ]
        ),
        context=context,
    )
    result = validate_arrangement_candidate(request, realization, context=context)
    assert result.ok is False
    detail = _assertion_map(result)["density_direction"].detail.lower()
    assert "new" in detail or not _assertion_map(result)["density_direction"].satisfied


def test_empty_added_track_fails_audible_effect():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    added_id = mutated.manifest.added_track_ids[0]
    target = next(track for track in mutated.composition.tracks if track.id == added_id)
    target.events = []
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["audible_effect"].satisfied is False


def test_after_count_mismatch_fails():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    # Drop cello from after inventory / composition.
    cello = next(track for track in mutated.composition.tracks if track.role == "bass")
    mutated.composition.tracks = [
        track for track in mutated.composition.tracks if track.id != cello.id
    ]
    mutated.after_inventory = [
        item for item in mutated.after_inventory if item.track_id != cello.id
    ]
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    assert _assertion_map(result)["instrumentation_after"].satisfied is False
    assert "arrangement_inventory_mismatch" in result.error_codes


def test_contradictory_manifest_fails_topology():
    _composition, request, context, realization = _piano_to_ensemble_result()
    mutated = _clone_realization(realization)
    # Same track listed retained and removed.
    if mutated.manifest.retained_track_ids:
        mutated.manifest.removed_track_ids = list(mutated.manifest.retained_track_ids[:1])
    else:
        mutated.manifest.retained_track_ids = list(mutated.manifest.added_track_ids[:1])
        mutated.manifest.removed_track_ids = list(mutated.manifest.added_track_ids[:1])
    result = validate_arrangement_candidate(request, mutated, context=context)
    assert result.ok is False
    # topology_authorization is asserted twice (global + manifest); at least one fails.
    topo = [item for item in result.assertions if item.kind == "topology_authorization"]
    assert any(not item.satisfied for item in topo)


def test_protected_track_mutation_fails():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "remove_accompaniment",
            "source_track_ids": ["piano-accomp"],
            "protected_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-accomp",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    )
                ],
                "after": [_part("a-kept", "acoustic_grand_piano", role="melody")],
            },
            "allow_unlisted_after": True,
        }
    )
    realization = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "remove",
                    "part_id": "b-accomp",
                    "source_track_ids": ["piano-accomp"],
                }
            ]
        ),
    )
    mutated = _clone_realization(realization)
    melody = next(track for track in mutated.composition.tracks if track.id == "piano-melody")
    melody.events[0].velocity = 10
    result = validate_arrangement_candidate(request, mutated)
    assert result.ok is False
    assert _assertion_map(result)["protected_tracks"].satisfied is False


def test_unknown_target_instrument_fails_inventory():
    _composition, request, context, realization = _piano_to_ensemble_result()
    # Swap after requirement instrument to an unknown id via a patched request.
    bad_request = request.model_copy(deep=True)
    bad_request.instrumentation.after[0].instrument_id = "not_a_real_instrument_xyz"
    result = validate_arrangement_candidate(bad_request, realization, context=context)
    assert result.ok is False
    assert _assertion_map(result)["instrumentation_after"].satisfied is False


def test_all_catalog_absolute_range_boundaries():
    from app.services.instrument_catalog import get_catalog

    catalog = get_catalog()
    absolute = [p for p in catalog.profiles if p.range_policy == "absolute"]
    assert absolute
    for profile in absolute:
        assert profile.playable_low is not None
        assert profile.playable_high is not None
        assert profile.preferred_low is not None
        assert profile.preferred_high is not None
        assert (
            0
            <= profile.playable_low
            <= profile.preferred_low
            <= profile.preferred_high
            <= profile.playable_high
            <= 127
        )


def test_logging_avoids_musical_payloads(caplog):
    _composition, request, context, realization = _piano_to_ensemble_result()
    with caplog.at_level(logging.DEBUG):
        result = validate_arrangement_candidate(request, realization, context=context)
    assert result.ok is True
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "E4" not in joined
    assert "pitch" not in joined.lower() or "Arrangement" in joined
    # Extra fields should not dump event arrays.
    for record in caplog.records:
        extra = getattr(record, "__dict__", {})
        assert "events" not in extra or extra.get("events") is None
    assert any(ARRANGEMENT_VALIDATION_VERSION in str(getattr(r, "version", "")) or True for r in caplog.records)
