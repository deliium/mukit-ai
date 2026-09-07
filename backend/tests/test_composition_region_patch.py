import logging

import pytest

from app.schemas import (
    Composition,
    CompositionEditSelection,
    CompositionRegionReplacementPatch,
    CompositionRegionTrackReplacement,
    NoteEvent,
)
from app.services.composition_region_patch import (
    CompositionRegionPatchError,
    apply_region_replacement_patch,
    canonical_json_dumps,
    compare_preserved_regions,
    selection_tick_bounds,
    summarize_region_selection,
)
from app.services.composition_timing import bar_duration_ticks


BAR_TICKS_4_4 = 1920


def _note(pitch: str, start_tick: int, duration_ticks: int = 480, velocity: int = 80, note_id: str | None = None):
    payload = {
        "type": "note",
        "pitch": pitch,
        "start_tick": start_tick,
        "duration_ticks": duration_ticks,
        "velocity": velocity,
    }
    if note_id is not None:
        payload["id"] = note_id
    return payload


def _sixteen_bar_composition(**overrides) -> Composition:
    payload = {
        "schema_version": "composition.v1",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 16,
        "duration_ticks": 16 * BAR_TICKS_4_4,
        "sections": [
            {"type": "intro", "start_bar": 1, "bar_count": 4, "start_tick": 0, "duration_ticks": 4 * BAR_TICKS_4_4},
            {
                "type": "verse",
                "start_bar": 5,
                "bar_count": 8,
                "start_tick": 4 * BAR_TICKS_4_4,
                "duration_ticks": 8 * BAR_TICKS_4_4,
            },
            {
                "type": "outro",
                "start_bar": 13,
                "bar_count": 4,
                "start_tick": 12 * BAR_TICKS_4_4,
                "duration_ticks": 4 * BAR_TICKS_4_4,
            },
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "staff": "treble",
                "events": [
                    _note("C4", bar * BAR_TICKS_4_4, note_id=f"melody-bar-{bar}")
                    for bar in range(16)
                ],
            },
            {
                "id": "harmony-1",
                "name": "Harmony",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "staff": "treble",
                "events": [
                    _note("E4", bar * BAR_TICKS_4_4, note_id=f"harmony-bar-{bar}")
                    for bar in range(16)
                ],
            },
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 3,
                "staff": "bass",
                "events": [
                    _note("C2", bar * BAR_TICKS_4_4, note_id=f"bass-bar-{bar}")
                    for bar in range(16)
                ],
            },
        ],
        "harmony": [{"bar": bar, "chord": "C"} for bar in range(1, 17)],
    }
    payload.update(overrides)
    return Composition.model_validate(payload)


def test_selection_tick_bounds_converts_inclusive_bars():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])

    bounds = selection_tick_bounds(composition, selection)

    assert bounds.bar_ticks == bar_duration_ticks("4/4", 480)
    assert bounds.start_tick == 8 * BAR_TICKS_4_4
    assert bounds.end_tick == 12 * BAR_TICKS_4_4


def test_summarize_region_selection_counts_target_events():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])

    summary = summarize_region_selection(composition, selection)

    assert summary.target_track_ids == ["melody-1"]
    assert summary.in_region_event_counts["melody-1"] == 4
    assert summary.in_region_event_counts["bass-1"] == 0
    assert summary.total_in_region_events == 4


def test_melody_only_replacement_preserves_outside_notes_byte_for_byte(caplog):
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        target_track_ids=["melody-1"],
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[
                    NoteEvent(pitch="G4", start_tick=8 * BAR_TICKS_4_4, duration_ticks=960, velocity=96),
                    NoteEvent(pitch="A4", start_tick=8 * BAR_TICKS_4_4 + 960, duration_ticks=960, velocity=96),
                    NoteEvent(pitch="B4", start_tick=10 * BAR_TICKS_4_4, duration_ticks=960, velocity=100),
                    NoteEvent(pitch="C5", start_tick=11 * BAR_TICKS_4_4, duration_ticks=960, velocity=100),
                ],
            )
        ],
        warnings=["dramatic melody phrase"],
    )

    with caplog.at_level(logging.INFO):
        result = apply_region_replacement_patch(
            composition,
            patch,
            selection=selection,
            validate_integrity=True,
        )

    assert "Applying composition region replacement patch" in caplog.text
    assert "Applied composition region replacement patch" in caplog.text
    assert result.replaced_event_count == 4

    comparison = compare_preserved_regions(
        composition,
        result.composition,
        result.summary.bounds,
        ["melody-1"],
    )
    assert comparison["ok"]
    assert "melody-1" in comparison["byte_equal_tracks"]
    assert "harmony-1" in comparison["byte_equal_tracks"]
    assert "bass-1" in comparison["byte_equal_tracks"]

    melody = next(track for track in result.composition.tracks if track.id == "melody-1")
    outside = [
        event.model_dump(mode="json")
        for event in melody.events
        if event.start_tick < 8 * BAR_TICKS_4_4 or event.start_tick >= 12 * BAR_TICKS_4_4
    ]
    original_melody = next(track for track in composition.tracks if track.id == "melody-1")
    original_outside = [
        event.model_dump(mode="json")
        for event in original_melody.events
        if event.start_tick < 8 * BAR_TICKS_4_4 or event.start_tick >= 12 * BAR_TICKS_4_4
    ]
    assert canonical_json_dumps(outside) == canonical_json_dumps(original_outside)
    assert result.composition.tempo == composition.tempo
    assert result.composition.key == composition.key
    assert result.composition.time_signature == composition.time_signature
    assert result.composition.bar_count == composition.bar_count
    assert result.composition.duration_ticks == composition.duration_ticks
    assert canonical_json_dumps([item.model_dump(mode="json") for item in result.composition.harmony]) == (
        canonical_json_dumps([item.model_dump(mode="json") for item in composition.harmony])
    )


def test_all_track_replacement_updates_each_target_track():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12)
    region_starts = [8 * BAR_TICKS_4_4, 9 * BAR_TICKS_4_4, 10 * BAR_TICKS_4_4, 11 * BAR_TICKS_4_4]
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[
                    NoteEvent(pitch="D5", start_tick=start, duration_ticks=480, velocity=90)
                    for start in region_starts
                ],
            ),
            CompositionRegionTrackReplacement(
                track_id="harmony-1",
                events=[
                    NoteEvent(pitch="F4", start_tick=start, duration_ticks=480, velocity=70)
                    for start in region_starts
                ],
            ),
            CompositionRegionTrackReplacement(
                track_id="bass-1",
                events=[
                    NoteEvent(pitch="D2", start_tick=start, duration_ticks=480, velocity=80)
                    for start in region_starts
                ],
            ),
        ],
    )

    result = apply_region_replacement_patch(composition, patch, selection=selection)
    assert result.replaced_event_count == 12
    for track_id in ("melody-1", "harmony-1", "bass-1"):
        track = next(item for item in result.composition.tracks if item.id == track_id)
        in_region = [event for event in track.events if 8 * BAR_TICKS_4_4 <= event.start_tick < 12 * BAR_TICKS_4_4]
        assert len(in_region) == 4


def test_added_counter_melody_track_is_supported():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    region_starts = [8 * BAR_TICKS_4_4, 9 * BAR_TICKS_4_4, 10 * BAR_TICKS_4_4, 11 * BAR_TICKS_4_4]
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        target_track_ids=["melody-1"],
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[
                    NoteEvent(pitch="E5", start_tick=start, duration_ticks=480, velocity=90)
                    for start in region_starts
                ],
            )
        ],
        added_tracks=[
            {
                "id": "counter-1",
                "name": "Counter Melody",
                "instrument": "flute",
                "role": "countermelody",
                "midi_program": 73,
                "channel": 4,
                "staff": "treble",
                "events": [
                    {
                        "pitch": "G5",
                        "start_tick": bar * BAR_TICKS_4_4,
                        "duration_ticks": 480,
                        "velocity": 70,
                    }
                    for bar in range(16)
                ],
            }
        ],
    )

    result = apply_region_replacement_patch(
        composition,
        patch,
        selection=selection,
        allow_added_tracks=True,
    )
    assert result.added_track_count == 1
    assert any(track.id == "counter-1" for track in result.composition.tracks)


def test_harmony_preserving_edit_rejects_harmony_patch_by_default():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        target_track_ids=["melody-1"],
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[NoteEvent(pitch="E5", start_tick=8 * BAR_TICKS_4_4, duration_ticks=480, velocity=90)],
            )
        ],
        harmony_patch=[{"bar": 9, "chord": "G"}],
    )

    with pytest.raises(CompositionRegionPatchError, match="harmony_patch is not allowed") as exc_info:
        apply_region_replacement_patch(composition, patch, selection=selection)

    assert exc_info.value.code == "harmony_patch_not_allowed"


def test_metadata_is_preserved_for_note_scoped_edit():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    region_starts = [8 * BAR_TICKS_4_4, 9 * BAR_TICKS_4_4, 10 * BAR_TICKS_4_4, 11 * BAR_TICKS_4_4]
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        target_track_ids=["melody-1"],
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[
                    NoteEvent(pitch="F4", start_tick=start, duration_ticks=480, velocity=88)
                    for start in region_starts
                ],
            )
        ],
    )

    result = apply_region_replacement_patch(composition, patch, selection=selection)
    assert result.composition.model_dump(mode="json")["sections"] == composition.model_dump(mode="json")["sections"]
    assert result.composition.ticks_per_quarter == composition.ticks_per_quarter


def test_region_edit_preserves_arbitrary_existing_instruments():
    composition = _sixteen_bar_composition()
    tracks = list(composition.tracks)
    unusual = tracks[0].model_copy(update={"instrument": "theremin", "name": "Theremin Lead"})
    tracks[0] = unusual
    composition = composition.model_copy(update={"tracks": tracks})
    before = [(track.id, track.instrument, track.role) for track in composition.tracks]

    selection = CompositionEditSelection(start_bar=1, end_bar=2, track_ids=["melody-1"])
    patch = CompositionRegionReplacementPatch(
        start_bar=1,
        end_bar=2,
        target_track_ids=["melody-1"],
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[
                    NoteEvent(pitch="C5", start_tick=0, duration_ticks=480, velocity=90),
                    NoteEvent(pitch="E5", start_tick=BAR_TICKS_4_4, duration_ticks=480, velocity=90),
                ],
            )
        ],
    )
    result = apply_region_replacement_patch(composition, patch, selection=selection)
    after = [(track.id, track.instrument, track.role) for track in result.composition.tracks]
    assert after == before
    assert result.composition.tracks[0].instrument == "theremin"


def test_invalid_boundaries_are_rejected(caplog):
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    patch = CompositionRegionReplacementPatch(
        start_bar=8,
        end_bar=12,
        target_track_ids=["melody-1"],
        replace_tracks=[],
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(CompositionRegionPatchError, match="bar boundaries must match") as exc_info:
            apply_region_replacement_patch(composition, patch, selection=selection)

    assert exc_info.value.code == "patch_boundary_mismatch"
    assert "Rejected patch with boundary mismatch" in caplog.text


def test_out_of_region_replacement_events_are_rejected():
    composition = _sixteen_bar_composition()
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        target_track_ids=["melody-1"],
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="melody-1",
                events=[NoteEvent(pitch="C5", start_tick=7 * BAR_TICKS_4_4, duration_ticks=480, velocity=90)],
            )
        ],
    )

    with pytest.raises(CompositionRegionPatchError, match="within the selected region") as exc_info:
        apply_region_replacement_patch(composition, patch, selection=selection)

    assert exc_info.value.code == "replacement_event_out_of_region"


def test_outside_region_mutation_is_rejected_by_preservation_check():
    composition = _sixteen_bar_composition()
    # Selection scopes to melody, but patch tries to rewrite bass in-region notes.
    selection = CompositionEditSelection(start_bar=9, end_bar=12, track_ids=["melody-1"])
    patch = CompositionRegionReplacementPatch(
        start_bar=9,
        end_bar=12,
        replace_tracks=[
            CompositionRegionTrackReplacement(
                track_id="bass-1",
                events=[NoteEvent(pitch="G2", start_tick=8 * BAR_TICKS_4_4, duration_ticks=480, velocity=80)],
            )
        ],
    )

    with pytest.raises(CompositionRegionPatchError) as exc_info:
        apply_region_replacement_patch(composition, patch, selection=selection)

    assert exc_info.value.code == "non_target_track_replacement"
