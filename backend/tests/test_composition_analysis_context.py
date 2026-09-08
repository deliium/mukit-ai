"""Tests for composition analysis context and logical-note collapse."""

from __future__ import annotations

import copy

import pytest

from app.analysis_schemas import CompositionAnalysisError, CompositionAnalysisScopeSection
from app.composition_schemas import CompositionV2
from app.services.composition_analysis_context import (
    allocate_crossing_note_to_bars,
    build_analysis_context,
)
from app.services.composition_logical_notes import (
    attack_in_interval,
    clip_occupancy,
    collapse_track_tie_chains,
    note_overlaps_interval,
)
from app.services.composition_midi import _collapse_tie_chains as midi_collapse
from app.services.composition_timeline import compile_timeline


def minimal_v2(**overrides):
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 2,
        "duration_ticks": 3840,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            },
            {
                "type": "verse",
                "start_bar": 2,
                "bar_count": 1,
                "start_tick": 1920,
                "duration_ticks": 1920,
            },
        ],
        "tracks": [
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def mixed_meter_v2(**overrides):
    # 4/4 (1920) -> 3/4 at 1920 (1440) -> 6/8 at 3360 (1440) = 4800 ticks / 3 bars
    data = {
        "schema_version": "composition.v2",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 3,
        "duration_ticks": 4800,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 3,
                "start_tick": 0,
                "duration_ticks": 4800,
            }
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "violin",
                "role": "melody",
                "midi_program": 40,
                "channel": 1,
                "events": [
                    {
                        "id": "n1",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 2400,
                        "velocity": 80,
                    },
                    {
                        "id": "n2",
                        "pitch": "E4",
                        "start_tick": 100,
                        "duration_ticks": 200,
                        "velocity": 70,
                    },
                    {
                        "id": "n3",
                        "pitch": "G4",
                        "start_tick": 50,
                        "duration_ticks": 100,
                        "velocity": 60,
                    },
                ],
            },
            {
                "id": "drums-1",
                "name": "Drums",
                "instrument": "drums",
                "role": "rhythm",
                "midi_program": 0,
                "channel": 10,
                "is_drum": True,
                "events": [
                    {
                        "pitch": "C2",
                        "start_tick": 0,
                        "duration_ticks": 120,
                        "velocity": 100,
                    }
                ],
            },
        ],
        "harmony": [],
        "tempo_changes": [{"tick": 1920, "bpm": 90}],
        "time_signature_changes": [
            {"tick": 1920, "time_signature": "3/4"},
            {"tick": 3360, "time_signature": "6/8"},
        ],
        "key_changes": [{"tick": 1920, "key": "G major"}],
        "markers": [],
    }
    data.update(overrides)
    return data


def test_collapse_ties_and_midi_parity():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "id": "a",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "tie": {"group_id": "t1", "type": "start"},
                        },
                        {
                            "id": "b",
                            "pitch": "C4",
                            "start_tick": 480,
                            "duration_ticks": 480,
                            "velocity": 40,
                            "tie": {"group_id": "t1", "type": "stop"},
                        },
                        {
                            "id": "c",
                            "pitch": "E4",
                            "start_tick": 0,
                            "duration_ticks": 240,
                            "velocity": 70,
                        },
                    ],
                }
            ]
        )
    )
    track = composition.tracks[0]
    logical = collapse_track_tie_chains(track)
    assert len(logical) == 2
    tied = next(note for note in logical if note.tie_group_id == "t1")
    assert tied.start_tick == 0
    assert tied.duration_ticks == 960
    assert tied.velocity == 80
    assert tied.source_event_ids == ("a", "b")

    midi_notes = midi_collapse(track)
    assert [(n.pitch, n.start_tick, n.duration_ticks, n.velocity) for n in midi_notes] == [
        (n.pitch, n.start_tick, n.duration_ticks, n.velocity) for n in logical
    ]


def test_overlap_attack_and_clip_semantics():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 1800,
                            "duration_ticks": 400,
                            "velocity": 80,
                        }
                    ],
                }
            ]
        )
    )
    note = collapse_track_tie_chains(composition.tracks[0])[0]
    # Section 1 is [0, 1920); note overlaps but attack is inside.
    assert note_overlaps_interval(note, 0, 1920)
    assert attack_in_interval(note, 0, 1920)
    assert clip_occupancy(note, 0, 1920) == 120
    # Section 2 [1920, 3840): overlaps, attack not in section.
    assert note_overlaps_interval(note, 1920, 3840)
    assert not attack_in_interval(note, 1920, 3840)
    assert clip_occupancy(note, 1920, 3840) == 280


def test_context_unsorted_events_stable_and_id_less(caplog):
    raw = mixed_meter_v2()
    before = copy.deepcopy(raw)
    with caplog.at_level("INFO"):
        context = build_analysis_context(raw, {"kind": "composition"})
    assert raw == before
    assert context.timeline.bar_count == 3
    # Unsorted authored events become stable analytical order.
    melodic = context.tracks[0].logical_notes
    starts = [note.start_tick for note in melodic]
    assert starts == sorted(starts)
    assert "Analysis context build complete" in caplog.text


def test_crossing_note_allocated_across_bars_not_start_only():
    composition = CompositionV2.model_validate(mixed_meter_v2())
    timeline = compile_timeline(composition)
    note = collapse_track_tie_chains(composition.tracks[0])[0]
    assert note.duration_ticks == 2400
    allocations = allocate_crossing_note_to_bars(note, timeline)
    bars = [bar for bar, _ticks in allocations]
    assert bars == [1, 2]
    assert sum(ticks for _bar, ticks in allocations) == 2400
    assert allocations[0][1] == 1920
    assert allocations[1][1] == 480


def test_section_scope_and_track_scope_isolation():
    context_section = build_analysis_context(
        mixed_meter_v2(),
        CompositionAnalysisScopeSection(section_index=0),
    )
    # Whole-composition section spans all bars in fixture.
    assert context_section.resolved_scope.kind == "section"

    # Two-section document for section clipping
    two_section = minimal_v2(
        tracks=[
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    },
                    {
                        "pitch": "D4",
                        "start_tick": 1920,
                        "duration_ticks": 480,
                        "velocity": 80,
                    },
                ],
            }
        ]
    )
    section0 = build_analysis_context(two_section, {"kind": "section", "section_index": 0})
    attacks0 = section0.attacks_in_scope()
    assert len(attacks0) == 1
    assert attacks0[0][2].pitch == "C4"

    section1 = build_analysis_context(two_section, {"kind": "section", "section_index": 1})
    attacks1 = section1.attacks_in_scope()
    assert len(attacks1) == 1
    assert attacks1[0][2].pitch == "D4"

    track_ctx = build_analysis_context(mixed_meter_v2(), {"kind": "track", "track_id": "drums-1"})
    assert all(track_ctx.tracks[t_idx].is_drum for t_idx, _n, _note in track_ctx.scoped_logical_notes)


def test_polyphony_bar_histogram_and_prefix_sums():
    context = build_analysis_context(mixed_meter_v2(), {"kind": "composition"})
    bar1 = context.bar_histograms[0]
    assert bar1.bar == 1
    assert bar1.attack_count >= 3  # melody unsorted + drum
    # Non-drum PC mass for C (0) and E (4) and G (7)
    assert bar1.pitch_class_mass[0] > 0
    # Prefix: bar 1 mass equals prefix[1] - prefix[0]
    prefix_delta = [
        context.bar_pc_prefix[1][pc] - context.bar_pc_prefix[0][pc] for pc in range(12)
    ]
    assert prefix_delta == list(bar1.pitch_class_mass)
    # Multi-bar query helper
    mass = context.pitch_class_mass_for_bars(1, 3)
    assert sum(mass) > 0


def test_empty_track_and_drums_classified():
    context = build_analysis_context(minimal_v2(), {"kind": "composition"})
    assert context.tracks[0].logical_notes == ()
    assert context.attacks_in_scope() == []

    drums = build_analysis_context(mixed_meter_v2(), {"kind": "composition"})
    assert drums.tracks[1].is_drum is True
    non_drum_attacks = drums.attacks_in_scope(drums=False)
    assert all(not drums.tracks[t].is_drum for t, _i, _n in non_drum_attacks)


def test_sweep_points_onset_before_offset_same_tick():
    composition = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        },
                        {
                            "pitch": "E4",
                            "start_tick": 480,
                            "duration_ticks": 480,
                            "velocity": 80,
                        },
                    ],
                }
            ]
        )
    )
    context = build_analysis_context(composition, {"kind": "composition"})
    at_480 = [point for point in context.sweep_points if point.tick == 480]
    assert at_480
    # First point at 480 should be an onset (+1) before any offset (-1).
    assert at_480[0].delta == 1


def test_invalid_scope_raises():
    with pytest.raises(CompositionAnalysisError) as exc:
        build_analysis_context(minimal_v2(), {"kind": "track", "track_id": "missing"})
    assert exc.value.code == "analysis_invalid_scope"
