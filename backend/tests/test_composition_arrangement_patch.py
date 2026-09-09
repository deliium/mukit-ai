"""Tests for composition arrangement draft realization (Task 4)."""

from __future__ import annotations

import copy

import pytest

from app.arrangement_schemas import (
    CompositionArrangementDraft,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_arrangement_context import build_arrangement_source_context
from app.services.composition_arrangement_patch import realize_arrangement_draft
from app.services.composition_midi import (
    CompositionMidiError,
    assert_shared_channel_program_compatible,
    render_midi_with_report,
)
from app.services.music_json_renderer import render_musicxml
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


def test_piano_to_ensemble_distributes_not_clones():
    composition = _piano_sketch_v2()
    snapshot = composition.model_dump(mode="json")
    request = _request(composition)
    context = build_arrangement_source_context(request)
    melody_refs = _refs_for_track(context, "piano-melody")
    accomp_refs = _refs_for_track(context, "piano-accomp")
    bass_refs = _refs_for_track(context, "bass-1")

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
                "source_track_ids": ["piano-accomp"],
                "source_note_refs": accomp_refs,
            },
        ]
    )
    result = realize_arrangement_draft(request, draft, context=context)
    assert composition.model_dump(mode="json") == snapshot
    assert len(result.composition.tracks) == 3
    instruments = {track.instrument.lower() for track in result.composition.tracks}
    assert any("piano" in name for name in instruments)
    assert any("cello" in name for name in instruments)
    assert any("string" in name for name in instruments)
    by_role = {track.role: track for track in result.composition.tracks}
    assert len(by_role["melody"].events) == 4
    assert len(by_role["bass"].events) == 2
    assert len(by_role["harmony"].events) == 5
    assert all(track.events for track in result.composition.tracks)
    assert result.event_counts.moved == 11
    assert "piano-melody" in result.manifest.removed_track_ids
    assert result.composition.harmony == composition.harmony
    assert result.composition.key == composition.key
    assert result.composition.tempo == composition.tempo
    assert result.composition.duration_ticks == composition.duration_ticks


def test_change_instrumentation_preserves_events_and_ids():
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
    draft = _draft(
        [
            {
                "action": "reinstrument",
                "part_id": "a-melody",
                "source_track_ids": ["piano-melody"],
            }
        ]
    )
    result = realize_arrangement_draft(request, draft)
    melody = next(track for track in result.composition.tracks if track.id == "piano-melody")
    assert melody.midi_program == 40  # violin
    assert "violin" in melody.instrument.lower()
    assert [event.model_dump(mode="json") for event in melody.events] == [
        event.model_dump(mode="json") for event in composition.tracks[0].events
    ]
    assert "piano-melody" in result.manifest.reinstrumented_track_ids


def test_add_and_remove_accompaniment_topology():
    composition = _piano_sketch_v2()
    add_request = CompositionArrangementPreviewRequest.model_validate(
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
    add_draft = _draft(
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
            {
                "action": "retain",
                "part_id": "b-bass",
                "source_track_ids": ["bass-1"],
            },
            {
                "action": "add",
                "part_id": "a-pad",
                "source_track_ids": [],
                "notes": [
                    {
                        "pitch": "C4",
                        "relative_start_tick": 0,
                        "duration_ticks": 1920,
                        "velocity": 50,
                    }
                ],
            },
        ]
    )
    added = realize_arrangement_draft(add_request, add_draft)
    assert len(added.composition.tracks) == 4
    assert added.event_counts.generated == 1
    assert len(added.manifest.added_track_ids) == 1

    remove_request = CompositionArrangementPreviewRequest.model_validate(
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
    remove_draft = _draft(
        [
            {
                "action": "remove",
                "part_id": "b-accomp",
                "source_track_ids": ["piano-accomp"],
            }
        ]
    )
    removed = realize_arrangement_draft(remove_request, remove_draft)
    ids = {track.id for track in removed.composition.tracks}
    assert "piano-accomp" not in ids
    assert "piano-melody" in ids
    assert "bass-1" in ids


def test_deterministic_ids_stable_across_runs():
    composition = _piano_sketch_v2()
    request = _request(composition)
    context = build_arrangement_source_context(request)
    melody_refs = _refs_for_track(context, "piano-melody")
    accomp_refs = _refs_for_track(context, "piano-accomp")
    bass_refs = _refs_for_track(context, "bass-1")
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
                "source_track_ids": ["piano-accomp"],
                "source_note_refs": accomp_refs,
            },
        ]
    )
    first = realize_arrangement_draft(request, draft, context=context, candidate_ordinal=1)
    second = realize_arrangement_draft(request, draft, context=context, candidate_ordinal=1)
    assert [track.id for track in first.composition.tracks] == [
        track.id for track in second.composition.tracks
    ]
    assert [
        [event.id for event in track.events] for track in first.composition.tracks
    ] == [[event.id for event in track.events] for track in second.composition.tracks]


def test_channel_10_reserved_and_exhaustion():
    tracks = []
    for index in range(16):
        tracks.append(
            {
                "id": f"t{index}",
                "name": f"T{index}",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": index,
                "channel": (index % 15) + 1 if index < 15 else 16,
                "events": [
                    {
                        "id": f"e{index}",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        )
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=1,
            duration_ticks=1920,
            tracks=tracks,
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
    )
    source_ids = [f"t{i}" for i in range(16)]
    before = [
        _part(f"b{i}", "acoustic_grand_piano", role="harmony", source_track_ids=[f"t{i}"])
        for i in range(16)
    ]
    instrument_ids = [
        "acoustic_grand_piano",
        "bright_acoustic_piano",
        "electric_piano_1",
        "electric_piano_2",
        "church_organ",
        "acoustic_guitar_nylon",
        "acoustic_guitar_steel",
        "electric_guitar_clean",
        "acoustic_bass",
        "electric_bass_finger",
        "violin",
        "viola",
        "cello",
        "contrabass",
        "flute",
        "clarinet",
    ]
    after = [_part(f"a{i}", instrument_ids[i], role="harmony") for i in range(16)]
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "orchestrate_selected_tracks",
            "source_track_ids": source_ids,
            "instrumentation": {"before": before, "after": after},
        }
    )
    context = build_arrangement_source_context(request)
    draft_parts = []
    for index in range(16):
        refs = _refs_for_track(context, f"t{index}")
        draft_parts.append(
            {
                "action": "redistribute",
                "part_id": f"a{index}",
                "source_track_ids": [f"t{index}"],
                "source_note_refs": refs,
            }
        )
    with pytest.raises(CompositionArrangementError) as exc:
        realize_arrangement_draft(request, _draft(draft_parts), context=context)
    assert exc.value.details.get("reason") == "channel_exhausted"


def test_channel_sharing_same_program():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "orchestrate_selected_tracks",
            "source_track_ids": ["piano-melody", "piano-accomp"],
            "instrumentation": {
                "before": [
                    _part(
                        "b1",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    ),
                    _part(
                        "b2",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["piano-accomp"],
                    ),
                ],
                "after": [
                    _part("a1", "violin", role="melody"),
                    _part("a2", "violin", role="harmony"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    draft = _draft(
        [
            {
                "action": "redistribute",
                "part_id": "a1",
                "source_track_ids": ["piano-melody"],
                "source_note_refs": _refs_for_track(context, "piano-melody"),
            },
            {
                "action": "redistribute",
                "part_id": "a2",
                "source_track_ids": ["piano-accomp"],
                "source_note_refs": _refs_for_track(context, "piano-accomp"),
            },
        ]
    )
    result = realize_arrangement_draft(request, draft, context=context)
    violins = [track for track in result.composition.tracks if track.midi_program == 40]
    assert len(violins) == 2
    assert violins[0].channel == violins[1].channel
    assert result.channel_allocation.shared_channel_count >= 1


def test_exact_note_copy_and_generated_materialization():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "create_countermelody",
            "source_track_ids": ["piano-melody"],
            "instrumentation": {
                "before": [
                    _part(
                        "b1",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["piano-melody"],
                    )
                ],
                "after": [
                    _part("a-mel", "acoustic_grand_piano", role="melody"),
                    _part("a-cm", "flute", role="countermelody"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    draft = _draft(
        [
            {
                "action": "retain",
                "part_id": "a-mel",
                "source_track_ids": ["piano-melody"],
            },
            {
                "action": "add",
                "part_id": "a-cm",
                "notes": [
                    {
                        "pitch": "G5",
                        "relative_start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 70,
                        "articulations": ["staccato"],
                    }
                ],
            },
        ]
    )
    result = realize_arrangement_draft(request, draft, context=context)
    melody = next(track for track in result.composition.tracks if track.id == "piano-melody")
    assert [e.pitch for e in melody.events] == ["E4", "G4", "C5", "D5"]
    counter = next(track for track in result.composition.tracks if track.role == "countermelody")
    assert counter.events[0].pitch == "G5"
    assert counter.events[0].articulations == ["staccato"]
    assert result.event_counts.generated == 1


def test_octave_policy_folds_unprotected_only():
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
                    {"id": "m1", "pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
                ],
            },
            {
                "id": "harm-1",
                "name": "Harmony",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "h1", "pitch": "C6", "start_tick": 0, "duration_ticks": 480, "velocity": 60}
                ],
            },
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
            "operation": "orchestrate_selected_tracks",
            "source_track_ids": ["melody-1", "harm-1"],
            "range_adjustment": "octave_shift_unprotected",
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["melody-1"],
                    ),
                    _part(
                        "b-h",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["harm-1"],
                    ),
                ],
                "after": [
                    _part("a-m", "flute", role="melody"),
                    _part("a-h", "cello", role="harmony"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    draft = _draft(
        [
            {
                "action": "redistribute",
                "part_id": "a-m",
                "source_track_ids": ["melody-1"],
                "source_note_refs": _refs_for_track(context, "melody-1"),
            },
            {
                "action": "redistribute",
                "part_id": "a-h",
                "source_track_ids": ["harm-1"],
                "source_note_refs": _refs_for_track(context, "harm-1"),
            },
        ]
    )
    result = realize_arrangement_draft(request, draft, context=context)
    cello = next(track for track in result.composition.tracks if "cello" in track.instrument.lower())
    assert cello.events[0].pitch == "C5"
    assert result.event_counts.octave_adjusted >= 1
    assert "octave_adjustment_applied" in result.warning_codes


def test_expressive_tie_preservation():
    data = minimal_v2(
        bar_count=2,
        duration_ticks=3840,
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
                        "id": "t1",
                        "pitch": "G4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "tie": {"group_id": "g1", "type": "start"},
                    },
                    {
                        "id": "t2",
                        "pitch": "G4",
                        "start_tick": 480,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "tie": {"group_id": "g1", "type": "stop"},
                    },
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
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 960, "velocity": 70}
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
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "piano_to_ensemble",
            "source_track_ids": ["melody-1", "bass-1"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["melody-1"],
                    ),
                    _part("b-b", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
                ],
                "after": [
                    _part("a-v", "violin", role="melody"),
                    _part("a-c", "cello", role="bass"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    assert len(_refs_for_track(context, "melody-1")) == 1
    draft = _draft(
        [
            {
                "action": "redistribute",
                "part_id": "a-v",
                "source_track_ids": ["melody-1"],
                "source_note_refs": _refs_for_track(context, "melody-1"),
            },
            {
                "action": "redistribute",
                "part_id": "a-c",
                "source_track_ids": ["bass-1"],
                "source_note_refs": _refs_for_track(context, "bass-1"),
            },
        ]
    )
    result = realize_arrangement_draft(request, draft, context=context)
    violin = next(track for track in result.composition.tracks if track.role == "melody")
    assert len(violin.events) == 2
    assert violin.events[0].tie is not None
    assert violin.events[1].tie is not None
    assert violin.events[0].tie.group_id == violin.events[1].tie.group_id
    assert violin.events[0].tie.type == "start"
    assert violin.events[1].tie.type == "stop"


def test_motif_one_target_remap_and_split_rejection():
    data = minimal_v2(
        bar_count=2,
        duration_ticks=3840,
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"id": "m1", "pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"id": "m2", "pitch": "G4", "start_tick": 480, "duration_ticks": 480, "velocity": 80},
                    {"id": "m3", "pitch": "C5", "start_tick": 960, "duration_ticks": 480, "velocity": 80},
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
                    {"id": "b1", "pitch": "C2", "start_tick": 0, "duration_ticks": 960, "velocity": 70}
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
        motifs=[
            {
                "id": "motif-1",
                "label": "hook",
                "occurrences": [
                    {
                        "id": "occ-1",
                        "track_id": "melody-1",
                        "event_ids": ["m1", "m2", "m3"],
                        "relationship": "original",
                    }
                ],
            }
        ],
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "piano_to_ensemble",
            "source_track_ids": ["melody-1", "bass-1"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["melody-1"],
                    ),
                    _part("b-b", "acoustic_bass", role="bass", source_track_ids=["bass-1"]),
                ],
                "after": [
                    _part("a-v", "violin", role="melody"),
                    _part("a-c", "cello", role="bass"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    melody_refs = _refs_for_track(context, "melody-1")
    ok = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-v",
                    "source_track_ids": ["melody-1"],
                    "source_note_refs": melody_refs,
                },
                {
                    "action": "redistribute",
                    "part_id": "a-c",
                    "source_track_ids": ["bass-1"],
                    "source_note_refs": _refs_for_track(context, "bass-1"),
                },
            ]
        ),
        context=context,
    )
    assert len(ok.composition.motifs) == 1
    occ = ok.composition.motifs[0].occurrences[0]
    violin = next(track for track in ok.composition.tracks if track.role == "melody")
    assert occ.track_id == violin.id
    assert set(occ.event_ids) == {event.id for event in violin.events}

    with pytest.raises(CompositionArrangementError) as exc:
        realize_arrangement_draft(
            request,
            _draft(
                [
                    {
                        "action": "redistribute",
                        "part_id": "a-v",
                        "source_track_ids": ["melody-1"],
                        "source_note_refs": [melody_refs[0]],
                    },
                    {
                        "action": "redistribute",
                        "part_id": "a-c",
                        "source_track_ids": ["melody-1", "bass-1"],
                        "source_note_refs": melody_refs[1:] + _refs_for_track(context, "bass-1"),
                    },
                ]
            ),
            context=context,
        )
    assert exc.value.details.get("reason") == "motif_occurrence_split"


def test_authorized_motif_prune_on_remove():
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
                    {"id": "m1", "pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
                ],
            },
            {
                "id": "harm-1",
                "name": "Harmony",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 2,
                "events": [
                    {"id": "h1", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 60},
                    {"id": "h2", "pitch": "E4", "start_tick": 480, "duration_ticks": 480, "velocity": 60},
                    {"id": "h3", "pitch": "G4", "start_tick": 960, "duration_ticks": 480, "velocity": 60},
                ],
            },
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
        motifs=[
            {
                "id": "motif-h",
                "label": "pad",
                "occurrences": [
                    {
                        "id": "occ-h",
                        "track_id": "harm-1",
                        "event_ids": ["h1", "h2", "h3"],
                        "relationship": "original",
                    }
                ],
            }
        ],
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "remove_accompaniment",
            "source_track_ids": ["harm-1"],
            "protected_track_ids": ["melody-1"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-h",
                        "acoustic_grand_piano",
                        role="harmony",
                        source_track_ids=["harm-1"],
                    )
                ],
                "after": [_part("a-m", "acoustic_grand_piano", role="melody")],
            },
            "allow_unlisted_after": True,
        }
    )
    result = realize_arrangement_draft(
        request,
        _draft([{"action": "remove", "part_id": "b-h", "source_track_ids": ["harm-1"]}]),
    )
    assert result.composition.motifs == []
    assert "motif_occurrence_pruned" in result.warning_codes


def test_no_input_mutation_and_canonical_ordering():
    composition = _piano_sketch_v2()
    snapshot = copy.deepcopy(composition.model_dump(mode="json"))
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
    result = realize_arrangement_draft(request, draft, context=context)
    assert composition.model_dump(mode="json") == snapshot
    for track in result.composition.tracks:
        starts = [event.start_tick for event in track.events]
        assert starts == sorted(starts)


def test_shared_channel_program_conflict_helper():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "a",
                    "name": "A",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {"pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
                    ],
                },
                {
                    "id": "b",
                    "name": "B",
                    "instrument": "violin",
                    "role": "melody",
                    "midi_program": 40,
                    "channel": 1,
                    "events": [
                        {"pitch": "E4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
                    ],
                },
            ]
        )
    )
    with pytest.raises(CompositionMidiError, match="same midi_program"):
        assert_shared_channel_program_compatible(composition.tracks)


def test_double_melody_keeps_source_and_adds_copy():
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
                        doubling_policy="octave",
                        source_track_ids=["piano-melody"],
                    ),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    refs = _refs_for_track(context, "piano-melody")
    draft = _draft(
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
    )
    result = realize_arrangement_draft(request, draft, context=context)
    assert any(track.id == "piano-melody" for track in result.composition.tracks)
    doubles = [
        track
        for track in result.composition.tracks
        if track.id != "piano-melody" and track.midi_program == 40
    ]
    assert len(doubles) == 1
    assert len(doubles[0].events) == 4
    assert result.event_counts.copied == 4


def test_midi_and_musicxml_smoke():
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
    result = realize_arrangement_draft(request, draft, context=context)
    midi = render_midi_with_report(result.composition)
    assert len(midi.midi_bytes) > 20
    xml, _report = render_musicxml(result.composition)
    assert "score-partwise" in xml


def test_simplify_and_density_operations_topology():
    composition = _piano_sketch_v2()
    request = CompositionArrangementPreviewRequest.model_validate(
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
                "after": [
                    _part("a-a", "acoustic_grand_piano", role="harmony"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    refs = _refs_for_track(context, "piano-accomp")[:2]
    result = realize_arrangement_draft(
        request,
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
        context=context,
    )
    assert len(result.composition.tracks) >= 2
    harm = next(track for track in result.composition.tracks if track.role == "harmony")
    assert len(harm.events) == 2

    inc = CompositionArrangementPreviewRequest.model_validate(
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
                "after": [
                    _part("a-a", "acoustic_grand_piano", role="harmony"),
                ],
            },
        }
    )
    ctx2 = build_arrangement_source_context(inc)
    denser = realize_arrangement_draft(
        inc,
        _draft(
            [
                {
                    "action": "redistribute",
                    "part_id": "a-a",
                    "source_track_ids": ["piano-accomp"],
                    "source_note_refs": _refs_for_track(ctx2, "piano-accomp"),
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
        context=ctx2,
    )
    harm2 = next(track for track in denser.composition.tracks if track.role == "harmony")
    assert len(harm2.events) == 6
    assert denser.event_counts.generated == 1


def test_drum_channel_allocation():
    data = minimal_v2(
        tracks=[
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {"id": "m1", "pitch": "C4", "start_tick": 0, "duration_ticks": 480, "velocity": 80}
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
                    {"id": "d1", "pitch": "C2", "start_tick": 0, "duration_ticks": 240, "velocity": 90}
                ],
            },
        ]
    )
    composition = CompositionV2.model_validate(data)
    request = CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "add_accompaniment",
            "source_track_ids": ["melody-1", "drums-1"],
            "instrumentation": {
                "before": [
                    _part(
                        "b-m",
                        "acoustic_grand_piano",
                        role="melody",
                        source_track_ids=["melody-1"],
                    ),
                    _part(
                        "b-d",
                        "standard_drum_kit",
                        role="drums",
                        source_track_ids=["drums-1"],
                    ),
                ],
                "after": [
                    _part("a-m", "acoustic_grand_piano", role="melody"),
                    _part("a-d", "standard_drum_kit", role="drums"),
                    _part("a-p", "synth_pad_new_age", role="pad"),
                ],
            },
        }
    )
    context = build_arrangement_source_context(request)
    result = realize_arrangement_draft(
        request,
        _draft(
            [
                {
                    "action": "retain",
                    "part_id": "a-m",
                    "source_track_ids": ["melody-1"],
                },
                {
                    "action": "retain",
                    "part_id": "a-d",
                    "source_track_ids": ["drums-1"],
                },
                {
                    "action": "add",
                    "part_id": "a-p",
                    "notes": [
                        {
                            "pitch": "C4",
                            "relative_start_tick": 0,
                            "duration_ticks": 960,
                            "velocity": 40,
                        }
                    ],
                },
            ]
        ),
        context=context,
    )
    drums = next(track for track in result.composition.tracks if track.is_drum)
    assert drums.channel == 10
