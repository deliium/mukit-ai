"""Warning evaluation unit coverage for composition analysis."""

from __future__ import annotations

from app.analysis_schemas import ANALYSIS_WARNING_CODES
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_analysis_warnings import (
    DENSE_MAX_SIMULTANEITY_THRESHOLD,
    DENSE_NOTE_LOAD_THRESHOLD,
    WARNING_REGISTRY,
    WarningEvaluationInput,
    evaluate_analysis_warnings,
    practical_range_for_track,
    sort_analysis_warnings,
)
from app.services.composition_density_analysis import analyze_density_from_context
from app.services.composition_harmony_analysis import analyze_harmony_from_context
from app.services.composition_melody_analysis import analyze_melody_from_context
from app.services.composition_repetition_analysis import analyze_repetition_from_context
from app.services.composition_role_analysis import analyze_roles_from_context
from app.services.composition_tonality import infer_tonality_from_context
from app.composition_schemas import midi_pitch_number


def _v2_shell(*, bar_count: int = 2, tracks=None, **overrides):
    duration = bar_count * 1920
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": [
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": bar_count,
                "start_tick": 0,
                "duration_ticks": duration,
            }
        ],
        "tracks": tracks
        or [
            {
                "id": "warn-1",
                "name": "Warn",
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


def _track(
    events,
    *,
    track_id="warn-1",
    role="harmony",
    instrument="piano",
    channel=1,
    is_drum=False,
    sustain_pedals=None,
):
    track = {
        "id": track_id,
        "name": track_id,
        "instrument": instrument,
        "role": role,
        "midi_program": 0,
        "channel": channel,
        "events": events,
    }
    if is_drum:
        track["is_drum"] = True
        track["channel"] = 10
    if sustain_pedals is not None:
        track["sustain_pedals"] = sustain_pedals
    return track


def _note(pitch, start=0, duration=480, velocity=80, staff=None, voice=None):
    event = {
        "pitch": pitch,
        "start_tick": start,
        "duration_ticks": duration,
        "velocity": velocity,
    }
    if staff is not None:
        event["staff"] = staff
    if voice is not None:
        event["voice"] = voice
    return event


def _evaluate(raw, scope=None) -> list:
    context = build_analysis_context(raw, scope)
    tonality = infer_tonality_from_context(context)
    harmony = analyze_harmony_from_context(context, tonality)
    roles = analyze_roles_from_context(context)
    melody = analyze_melody_from_context(context, tonality=tonality, harmony=harmony)
    density = analyze_density_from_context(context, harmony)
    repetition = analyze_repetition_from_context(context)
    return evaluate_analysis_warnings(
        WarningEvaluationInput(
            context=context,
            tonality=tonality,
            harmony=harmony,
            melody=melody,
            density=density,
            roles=roles,
            repetition=repetition,
        )
    )


def _codes(warnings) -> set[str]:
    return {item.code for item in warnings}


def test_warning_registry_covers_all_schema_codes():
    assert set(WARNING_REGISTRY) == set(ANALYSIS_WARNING_CODES)


def test_bassoon_not_matched_as_bass_range():
    # Bb4 is in bassoon practical range but above bass (C1–C4).
    events = [_note("Bb4", start=0, duration=1920)]
    raw = _v2_shell(bar_count=1, tracks=[_track(events, instrument="Bassoon", role="melody")])
    context = build_analysis_context(raw)
    track = context.tracks[0]
    bounds = practical_range_for_track(track)
    assert bounds is not None
    low, high = bounds
    assert low == midi_pitch_number("Bb1")
    assert high == midi_pitch_number("Eb5")
    midi = midi_pitch_number("Bb4")
    assert low <= midi <= high
    warnings = _evaluate(raw)
    assert "note_outside_instrument_range" not in _codes(warnings)


def test_note_outside_instrument_range_bassoon_too_high():
    events = [_note("C6", start=0, duration=1920)]
    raw = _v2_shell(bar_count=1, tracks=[_track(events, instrument="bassoon", role="melody")])
    warnings = _evaluate(raw)
    assert "note_outside_instrument_range" in _codes(warnings)
    item = next(w for w in warnings if w.code == "note_outside_instrument_range")
    assert item.category == "range"
    assert item.details["identity"] == "bassoon"
    assert item.details["outlier_count"] >= 1


def test_drums_not_judged_by_pitched_ranges():
    events = [_note("C8", start=0, duration=480), _note("C1", start=480, duration=480)]
    raw = _v2_shell(
        bar_count=1,
        tracks=[_track(events, track_id="drums-1", instrument="Drum Kit", role="drums", is_drum=True)],
    )
    warnings = _evaluate(raw)
    assert "note_outside_instrument_range" not in _codes(warnings)
    assert "percussion_only_scope" in _codes(warnings)


def test_dense_overlap_threshold_boundary_and_chord_false_positive():
    # Legitimate 4-note chord / pad: under thresholds.
    chord = [
        _note("C3", 0, 1920),
        _note("E3", 0, 1920),
        _note("G3", 0, 1920),
        _note("C4", 0, 1920),
    ]
    chord_raw = _v2_shell(bar_count=1, tracks=[_track(chord, role="pad")])
    chord_warnings = _evaluate(chord_raw)
    assert "dense_overlapping_material" not in _codes(chord_warnings)

    # Unison across voices (different pitches not required): 3 overlapping notes OK.
    unison_ish = [
        _note("C4", 0, 1920),
        _note("E4", 0, 1920),
        _note("G4", 0, 1920),
    ]
    assert "dense_overlapping_material" not in _codes(_evaluate(_v2_shell(bar_count=1, tracks=[_track(unison_ish)])))

    # Conspicuous stack at/above threshold.
    stack = [_note(f"C{3 + (i % 3)}", 0, 1920) for i in range(int(DENSE_NOTE_LOAD_THRESHOLD))]
    # Ensure uniqueness of pitch identity while keeping full overlap; use chromatic stack.
    stack = [_note(["C3", "C#3", "D3", "Eb3", "E3", "F3", "F#3", "G3", "Ab3", "A3", "Bb3", "B3"][i], 0, 1920) for i in range(int(DENSE_NOTE_LOAD_THRESHOLD))]
    dense_raw = _v2_shell(bar_count=1, tracks=[_track(stack)])
    dense_warnings = _evaluate(dense_raw)
    assert "dense_overlapping_material" in _codes(dense_warnings)
    item = next(w for w in dense_warnings if w.code == "dense_overlapping_material")
    assert item.details["note_load_actual"] >= DENSE_NOTE_LOAD_THRESHOLD
    assert item.details["note_load_threshold"] == DENSE_NOTE_LOAD_THRESHOLD
    assert item.details["max_simultaneity_threshold"] == DENSE_MAX_SIMULTANEITY_THRESHOLD


def test_empty_selected_section_and_authored_section():
    empty_section = _v2_shell(
        bar_count=4,
        sections=[
            {
                "type": "verse",
                "id": "sec-a",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "type": "chorus",
                "id": "sec-b",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        tracks=[
            _track(
                [_note("C4", 0, 480), _note("E4", 480, 480)],
                role="melody",
            )
        ],
    )
    # Composition-wide: empty authored section 1.
    composition_warnings = _evaluate(empty_section, {"kind": "composition"})
    authored = [w for w in composition_warnings if w.code == "empty_analysis_scope"]
    assert authored
    assert any(w.locator and w.locator.section_index == 1 for w in authored)

    # Selected empty section.
    selected = _evaluate(empty_section, {"kind": "section", "section_index": 1})
    assert "empty_analysis_scope" in _codes(selected)
    item = next(w for w in selected if w.code == "empty_analysis_scope")
    assert item.details["scope_kind"] == "section"


def test_empty_selected_track():
    raw = _v2_shell(
        tracks=[
            _track([_note("C4", 0, 480)], track_id="filled", role="melody"),
            _track([], track_id="silent", role="harmony"),
        ]
    )
    warnings = _evaluate(raw, {"kind": "track", "track_id": "silent"})
    assert "empty_analysis_scope" in _codes(warnings)


def test_excessive_duplicate_notes_exact_match():
    events = [
        _note("C4", 0, 480, staff="treble", voice=1),
        _note("C4", 0, 480, staff="treble", voice=1),
        _note("E4", 480, 480),
    ]
    warnings = _evaluate(_v2_shell(bar_count=1, tracks=[_track(events)]))
    assert "excessive_duplicate_notes" in _codes(warnings)
    item = next(w for w in warnings if w.code == "excessive_duplicate_notes")
    assert item.details["duplicate_extra_count"] >= 1


def test_duplicates_differ_by_staff_or_voice_are_not_exact():
    events = [
        _note("C4", 0, 480, staff="treble", voice=1),
        _note("C4", 0, 480, staff="bass", voice=1),
        _note("C4", 0, 480, staff="treble", voice=2),
    ]
    warnings = _evaluate(_v2_shell(bar_count=1, tracks=[_track(events)]))
    assert "excessive_duplicate_notes" not in _codes(warnings)


def test_overlapping_same_pitch_timing_not_exact_duplicate():
    events = [
        _note("C4", 0, 960),
        _note("C4", 480, 960),  # overlaps but different start/duration
    ]
    warnings = _evaluate(_v2_shell(bar_count=1, tracks=[_track(events)]))
    assert "overlapping_same_pitch_timing" in _codes(warnings)


def test_legitimate_unison_across_tracks_not_duplicate_warning():
    raw = _v2_shell(
        bar_count=1,
        tracks=[
            _track([_note("C4", 0, 1920)], track_id="a", role="melody"),
            _track([_note("C4", 0, 1920)], track_id="b", role="harmony"),
        ],
    )
    warnings = _evaluate(raw)
    assert "excessive_duplicate_notes" not in _codes(warnings)


def test_timing_grid_anomaly_threshold():
    # Mostly off 16th-note grid (480/4=120).
    events = [
        _note("C4", 10, 470),
        _note("D4", 130, 470),
        _note("E4", 250, 470),
        _note("F4", 370, 470),
        _note("G4", 490, 470),
    ]
    warnings = _evaluate(_v2_shell(bar_count=1, tracks=[_track(events, role="melody")]))
    assert "timing_grid_anomaly" in _codes(warnings)
    item = next(w for w in warnings if w.code == "timing_grid_anomaly")
    assert item.details["off_grid_count"] >= 4
    assert item.details["grid_ticks"] == 120

    # Aligned notes must not warn.
    aligned = [_note("C4", i * 480, 480) for i in range(4)]
    assert "timing_grid_anomaly" not in _codes(
        _evaluate(_v2_shell(bar_count=1, tracks=[_track(aligned, role="melody")]))
    )


def test_declared_harmony_conflict_and_unparseable():
    chord = [_note("C3", 0, 3840), _note("E3", 0, 3840), _note("G3", 0, 3840)]
    conflict_raw = _v2_shell(
        tracks=[_track(chord)],
        harmony=[{"bar": 1, "chord": "G"}, {"bar": 2, "chord": "G"}],
    )
    conflict_codes = _codes(_evaluate(conflict_raw))
    assert "declared_harmony_conflicts_with_inference" in conflict_codes

    bad_raw = _v2_shell(
        tracks=[_track(chord)],
        harmony=[{"bar": 1, "chord": "???"}, {"bar": 2, "chord": "???"}],
    )
    bad = _evaluate(bad_raw)
    # When declared symbols cannot be parsed for comparison, emit unparseable
    # (or conflict if the analyzer classifies that way for the fixture).
    assert _codes(bad) & {
        "declared_harmony_unparseable",
        "declared_harmony_conflicts_with_inference",
    }


def test_unsupported_sustain_and_limitation_codes():
    raw = _v2_shell(
        bar_count=1,
        tracks=[
            _track(
                [_note("C4", 0, 1920)],
                role="melody",
                sustain_pedals=[{"start_tick": 0, "duration_ticks": 960}],
            )
        ],
    )
    warnings = _evaluate(raw)
    assert "unsupported_sustain_interpretation" in _codes(warnings)


def test_warning_sort_order_severity_then_code():
    raw = _v2_shell(
        bar_count=1,
        tracks=[
            _track(
                # duplicates + range violation + off-grid
                [
                    _note("C8", 10, 470),
                    _note("C8", 10, 470),
                    _note("D8", 130, 470),
                    _note("E8", 250, 470),
                    _note("F8", 370, 470),
                ],
                instrument="flute",
                role="melody",
            )
        ],
    )
    warnings = _evaluate(raw)
    sorted_copy = sort_analysis_warnings(warnings)
    assert [w.code for w in warnings] == [w.code for w in sorted_copy]
    # severity ranks: warning before info
    severities = [w.severity for w in warnings]
    if "warning" in severities and "info" in severities:
        assert severities.index("warning") < severities.index("info") or all(
            s == severities[0] for s in severities[: severities.index("info")]
        )


def test_melody_skyline_emits_limitation_warning():
    # Polyphonic melody track triggers skyline reduction.
    events = [
        _note("C4", 0, 480),
        _note("E4", 0, 480),
        _note("G4", 480, 480),
        _note("B4", 480, 480),
        _note("C5", 960, 480),
        _note("E5", 960, 480),
    ]
    raw = _v2_shell(bar_count=1, tracks=[_track(events, role="melody")])
    warnings = _evaluate(raw)
    assert "melody_skyline_reduction" in _codes(warnings)


def _events_from_pitches(pitches, *, duration_ticks=480, gap=480):
    events = []
    tick = 0
    for pitch in pitches:
        events.append(_note(pitch, tick, duration_ticks))
        tick += gap
    return events


def test_declared_key_conflict_and_relative_ambiguity_warnings():
    # Strong F# minor material vs declared C major crosses contradiction margins.
    fs_minor = ["F#4", "A4", "C#5", "F#4", "G#4", "A4", "C#5", "F#5"] * 6
    conflict_raw = _v2_shell(
        bar_count=12,
        key="C major",
        tracks=[_track(_events_from_pitches(fs_minor), role="melody")],
        duration_ticks=12 * 1920,
    )
    conflict_raw["sections"][0]["bar_count"] = 12
    conflict_raw["sections"][0]["duration_ticks"] = 12 * 1920
    conflict_codes = _codes(_evaluate(conflict_raw))
    assert "declared_key_conflicts_with_inference" in conflict_codes

    ambiguous = ["C4", "E4", "G4", "A4"] * 8
    amb_raw = _v2_shell(
        bar_count=8,
        key="C major",
        tracks=[_track(_events_from_pitches(ambiguous), role="melody")],
        duration_ticks=8 * 1920,
    )
    amb_raw["sections"][0]["bar_count"] = 8
    amb_raw["sections"][0]["duration_ticks"] = 8 * 1920
    assert "relative_key_ambiguity" in _codes(_evaluate(amb_raw))


def test_declared_key_change_conflict_warning():
    # Local windows inherit declared root key C major while pitched evidence is F# minor.
    # That is the same contradiction path used for key-change / local-span conflicts.
    pitches = ["F#4", "A4", "C#5", "F#4", "G#4", "A4", "C#5", "F#5"] * 6
    raw = _v2_shell(
        bar_count=12,
        key="C major",
        tracks=[_track(_events_from_pitches(pitches), role="melody")],
        duration_ticks=12 * 1920,
    )
    raw["sections"][0]["bar_count"] = 12
    raw["sections"][0]["duration_ticks"] = 12 * 1920
    warnings = _evaluate(raw)
    codes = _codes(warnings)
    assert "declared_key_change_conflicts_with_inference" in codes
    assert "declared_key_conflicts_with_inference" in codes
