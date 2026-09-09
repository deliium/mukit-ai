"""Tests for immutable V2 harmony timeline range operations."""

from __future__ import annotations

import pytest

from app.composition_schemas import CompositionV2
from app.harmony_schemas import (
    HarmonyAddOperation,
    HarmonyMoveOperation,
    HarmonyRemoveOperation,
    HarmonyReplaceOperation,
    HarmonyResizeOperation,
    HarmonySpanInput,
    HarmonyTimelineError,
)
from app.services.composition_harmony_timeline import apply_harmony_timeline_operation
from app.services.composition_region_patch import (
    CompositionRegionPatchError,
    apply_region_replacement_patch,
)
from app.schemas import CompositionRegionReplacementPatch, CompositionRegionTrackReplacement, NoteEvent


def _composition(**overrides) -> CompositionV2:
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 4,
        "duration_ticks": 7680,
        "sections": [
            {
                "id": "sec-1",
                "type": "intro",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 7680,
            }
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "id": "n1",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
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
                    {
                        "type": "note",
                        "id": "b1",
                        "pitch": "C2",
                        "start_tick": 1920,
                        "duration_ticks": 480,
                        "velocity": 70,
                    }
                ],
            },
        ],
        "harmony": [
            {"start_tick": 0, "duration_ticks": 3840, "chord": "C"},
            {"start_tick": 3840, "duration_ticks": 3840, "chord": "G"},
        ],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
        "motifs": [],
    }
    data.update(overrides)
    return CompositionV2.model_validate(data)


def test_add_into_gap_and_reject_overlap():
    composition = _composition(
        harmony=[{"start_tick": 0, "duration_ticks": 1920, "chord": "C"}],
    )
    result = apply_harmony_timeline_operation(
        composition,
        HarmonyAddOperation(span=HarmonySpanInput(start_tick=3840, duration_ticks=1920, chord="Am")),
    )
    assert [item.model_dump() for item in result.composition.harmony] == [
        {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
        {"start_tick": 3840, "duration_ticks": 1920, "chord": "Am"},
    ]
    # Notes unchanged and source object identity preserved for input.
    assert composition.tracks[0].events[0].id == "n1"
    with pytest.raises(HarmonyTimelineError) as exc:
        apply_harmony_timeline_operation(
            result.composition,
            HarmonyAddOperation(span=HarmonySpanInput(start_tick=0, duration_ticks=480, chord="F")),
        )
    assert exc.value.code == "harmony_overlap"


def test_replace_preserves_fragments_and_explicit_clear():
    composition = _composition()
    replaced = apply_harmony_timeline_operation(
        composition,
        HarmonyReplaceOperation(
            start_tick=1920,
            duration_ticks=3840,
            spans=[HarmonySpanInput(start_tick=1920, duration_ticks=3840, chord="Dm")],
        ),
    )
    assert [item.model_dump() for item in replaced.composition.harmony] == [
        {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
        {"start_tick": 1920, "duration_ticks": 3840, "chord": "Dm"},
        {"start_tick": 5760, "duration_ticks": 1920, "chord": "G"},
    ]

    cleared = apply_harmony_timeline_operation(
        composition,
        HarmonyReplaceOperation(start_tick=0, duration_ticks=7680, spans=[]),
    )
    assert cleared.composition.harmony == []


def test_remove_move_resize_and_merge_identical_adjacent():
    composition = _composition(
        harmony=[
            {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
            {"start_tick": 3840, "duration_ticks": 1920, "chord": "G"},
        ]
    )
    removed = apply_harmony_timeline_operation(
        composition,
        HarmonyRemoveOperation(start_tick=960, duration_ticks=960),
    )
    assert removed.composition.harmony[0].duration_ticks == 960

    moved = apply_harmony_timeline_operation(
        removed.composition,
        HarmonyMoveOperation(source_start_tick=3840, new_start_tick=1920),
    )
    assert moved.composition.harmony[-1].start_tick == 1920

    resized = apply_harmony_timeline_operation(
        _composition(harmony=[{"start_tick": 0, "duration_ticks": 1920, "chord": "C"}]),
        HarmonyResizeOperation(source_start_tick=0, edge="end", new_tick=3840),
    )
    assert resized.composition.harmony[0].duration_ticks == 3840

    merged = apply_harmony_timeline_operation(
        _composition(
            harmony=[
                {"start_tick": 0, "duration_ticks": 1920, "chord": "C"},
                {"start_tick": 3840, "duration_ticks": 1920, "chord": "C"},
            ]
        ),
        HarmonyReplaceOperation(
            start_tick=1920,
            duration_ticks=1920,
            spans=[HarmonySpanInput(start_tick=1920, duration_ticks=1920, chord="C")],
        ),
    )
    assert [item.model_dump() for item in merged.composition.harmony] == [
        {"start_tick": 0, "duration_ticks": 5760, "chord": "C"},
    ]


def test_enharmonic_spellings_are_not_merged():
    composition = _composition(
        harmony=[
            {"start_tick": 0, "duration_ticks": 1920, "chord": "C#"},
            {"start_tick": 1920, "duration_ticks": 1920, "chord": "Db"},
        ]
    )
    result = apply_harmony_timeline_operation(
        composition,
        HarmonyAddOperation(span=HarmonySpanInput(start_tick=5760, duration_ticks=1920, chord="G")),
    )
    chords = [item.chord for item in result.composition.harmony]
    assert "C#" in chords and "Db" in chords
    assert result.composition.harmony[0].duration_ticks == 1920
    assert result.composition.harmony[1].duration_ticks == 1920


def test_region_patch_requires_explicit_replacements_and_preserves_omitted_tracks():
    composition = _composition()
    # Missing explicit replace for declared target must fail at schema validation.
    with pytest.raises(Exception):
        CompositionRegionReplacementPatch(
            start_bar=1,
            end_bar=1,
            target_track_ids=["melody-1", "bass-1"],
            replace_tracks=[
                CompositionRegionTrackReplacement(
                    track_id="melody-1",
                    events=[NoteEvent(pitch="D4", start_tick=0, duration_ticks=480, velocity=80)],
                )
            ],
        )

    # Without target_track_ids, only listed replace_tracks are mutated.
    patch = CompositionRegionReplacementPatch(
        schema_version="composition.v2",
        start_bar=1,
        end_bar=1,
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[NoteEvent(pitch="E4", start_tick=0, duration_ticks=480, velocity=90)],
            )
        ],
    )
    result = apply_region_replacement_patch(composition, patch)
    bass = next(track for track in result.composition.tracks if track.id == "bass-1")
    assert bass.events[0].pitch == "C2"
    assert bass.events[0].id == "b1"
    melody = next(track for track in result.composition.tracks if track.id == "melody-1")
    assert melody.events[0].pitch == "E4"


def test_harmony_metadata_only_region_patch_skips_note_mutation():
    composition = _composition()
    original_events = [
        [event.model_dump(mode="json") for event in track.events] for track in composition.tracks
    ]
    patch = CompositionRegionReplacementPatch(
        schema_version="composition.v2",
        start_bar=1,
        end_bar=2,
        replace_tracks=[],
        target_track_ids=None,
        harmony_patch=[
            {"start_tick": 0, "duration_ticks": 3840, "chord": "Am"},
        ],
    )
    result = apply_region_replacement_patch(
        composition,
        patch,
        allow_harmony_changes=True,
    )
    assert [item.model_dump() for item in result.composition.harmony] == [
        {"start_tick": 0, "duration_ticks": 3840, "chord": "Am"},
        {"start_tick": 3840, "duration_ticks": 3840, "chord": "G"},
    ]
    after_events = [
        [event.model_dump(mode="json") for event in track.events] for track in result.composition.tracks
    ]
    assert after_events == original_events


def test_harmony_only_edit_rejects_bar_shaped_v2_patch():
    with pytest.raises(Exception):
        CompositionRegionReplacementPatch(
            schema_version="composition.v2",
            start_bar=1,
            end_bar=1,
            harmony_patch=[{"bar": 1, "chord": "C"}],
        )
