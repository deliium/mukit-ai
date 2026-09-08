"""Harmony / scale-degree analysis unit coverage."""

from __future__ import annotations

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_harmony_analysis import (
    HARMONY_METHOD,
    analyze_harmony_from_context,
    analyze_scale_degrees_from_context,
)
from app.services.composition_tonality import infer_tonality_from_context


def _v2_shell(*, bar_count: int = 2, key: str = "C major", tracks=None, harmony=None, **overrides):
    duration = bar_count * 1920
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": key,
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
                "id": "harmony-1",
                "name": "Harmony",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": harmony or [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def _block_chord(pitches: list[str], *, start: int = 0, duration: int = 1920, velocity: int = 80):
    return [
        {
            "pitch": pitch,
            "start_tick": start,
            "duration_ticks": duration,
            "velocity": velocity - index * 5,
        }
        for index, pitch in enumerate(pitches)
    ]


def _track(events, *, track_id: str = "harmony-1", role: str = "harmony"):
    return {
        "id": track_id,
        "name": track_id,
        "instrument": "piano",
        "role": role,
        "midi_program": 0,
        "channel": 1,
        "events": events,
    }


def _accepted_symbols(result):
    return [
        span.symbol
        for span in result.spans
        if span.inference.status == "ok" and span.symbol
    ]


def test_c_major_triad_root_position():
    raw = _v2_shell(
        tracks=[_track(_block_chord(["C3", "E3", "G3", "C4"]))],
    )
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    result = analyze_harmony_from_context(context, tonality)
    symbols = _accepted_symbols(result)
    assert symbols
    assert symbols[0] == "C"
    assert result.spans[0].root_pc == 0
    assert result.spans[0].quality == "maj"
    assert result.spans[0].bass_pc == 0
    assert result.inference.method == HARMONY_METHOD
    assert result.inference.method_version == ANALYSIS_ALGORITHM_VERSION


def test_first_inversion_preserves_bass():
    raw = _v2_shell(
        tracks=[_track(_block_chord(["E3", "G3", "C4"]))],
    )
    result = analyze_harmony_from_context(build_analysis_context(raw))
    accepted = [span for span in result.spans if span.inference.status == "ok"]
    assert accepted
    assert accepted[0].symbol == "C"
    assert accepted[0].bass_pc == 4  # E


def test_seventh_sus_and_diminished():
    cases = [
        (["C3", "E3", "G3", "Bb3"], "C7", "dom7"),
        (["C3", "F3", "G3"], "Csus4", "sus4"),
        (["B3", "D4", "F4"], "Bdim", "dim"),
        (["A3", "C4", "E4", "G4"], "Am7", "min7"),
    ]
    for pitches, symbol, quality in cases:
        raw = _v2_shell(tracks=[_track(_block_chord(pitches))])
        result = analyze_harmony_from_context(build_analysis_context(raw))
        accepted = [span for span in result.spans if span.inference.status == "ok"]
        assert accepted, f"expected acceptance for {symbol}"
        assert accepted[0].symbol == symbol
        assert accepted[0].quality == quality


def test_missing_fifth_still_accepts_seventh():
    # C7 without the fifth (C-E-Bb) still has three chord tones → accept dom7.
    raw = _v2_shell(
        tracks=[_track(_block_chord(["C3", "E3", "Bb3"]))],
    )
    result = analyze_harmony_from_context(build_analysis_context(raw))
    accepted = [span for span in result.spans if span.inference.status == "ok"]
    assert accepted
    assert accepted[0].quality == "dom7"
    assert accepted[0].symbol == "C7"


def test_passing_tone_absorbed_into_neighbor_chord():
    # Sustained C major with a brief D passing tone mid-bar.
    events = _block_chord(["C3", "E3", "G3"], duration=1920)
    events.append({"pitch": "D4", "start_tick": 480, "duration_ticks": 120, "velocity": 70})
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_harmony_from_context(build_analysis_context(raw))
    symbols = _accepted_symbols(result)
    assert symbols
    assert symbols.count("C") >= 1
    # Passing tone should not create a durable non-C accepted span dominating the bar.
    non_c = [symbol for symbol in symbols if symbol != "C"]
    assert len(non_c) <= 1


def test_ambiguous_dyad_abstains():
    raw = _v2_shell(
        tracks=[_track(_block_chord(["C3", "G3"]))],
    )
    result = analyze_harmony_from_context(build_analysis_context(raw))
    accepted = _accepted_symbols(result)
    assert accepted == []
    assert result.inference.status in {"insufficient_evidence", "ambiguous"}


def test_silent_frames_and_empty_scope():
    raw = _v2_shell(
        bar_count=2,
        tracks=[
            _track(
                [
                    {"pitch": "C3", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "E3", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                    {"pitch": "G3", "start_tick": 0, "duration_ticks": 480, "velocity": 80},
                ]
            )
        ],
    )
    result = analyze_harmony_from_context(build_analysis_context(raw))
    assert any(span.inference.status == "not_applicable" for span in result.spans) or any(
        span.symbol is None for span in result.spans
    )

    empty = analyze_harmony_from_context(build_analysis_context(_v2_shell()))
    assert empty.inference.status == "insufficient_evidence"
    assert empty.spans == []


def test_modulation_aware_scale_degrees():
    # Bars 1-2: C major triad attacks; bars 3-4: A minor triad attacks.
    events = []
    for bar, pitches in (
        (0, ["C4", "E4", "G4"]),
        (1, ["C4", "E4", "G4"]),
        (2, ["A4", "C5", "E5"]),
        (3, ["A4", "C5", "E5"]),
    ):
        start = bar * 1920
        for pitch in pitches:
            events.append(
                {
                    "pitch": pitch,
                    "start_tick": start,
                    "duration_ticks": 480,
                    "velocity": 80,
                }
            )
    raw = _v2_shell(
        bar_count=4,
        key="C major",
        tracks=[_track(events, role="melody")],
        key_changes=[{"tick": 2 * 1920, "key": "A minor"}],
    )
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    degrees = analyze_scale_degrees_from_context(context, tonality)
    assert degrees.inference.status == "ok"
    assert degrees.buckets
    # Degree 1 should appear (tonic in each local key).
    assert any(bucket.degree == 1 and bucket.alteration == 0 for bucket in degrees.buckets)


def test_harmonic_rhythm_merging_same_chord():
    # Two bars of identical C major should merge / report low change rate.
    events = _block_chord(["C3", "E3", "G3"], start=0, duration=3840)
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    result = analyze_harmony_from_context(build_analysis_context(raw))
    accepted = [span for span in result.spans if span.inference.status == "ok"]
    assert accepted
    assert result.harmonic_rhythm.unique_chord_count == 1
    assert result.harmonic_rhythm.changes_per_bar == 0.0


def test_declared_harmony_agreement_and_conflict():
    agree_raw = _v2_shell(
        tracks=[_track(_block_chord(["C3", "E3", "G3"]))],
        harmony=[{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "C"}],
    )
    agree = analyze_harmony_from_context(build_analysis_context(agree_raw))
    assert agree.declared_agreement in {"agreement", "partial_agreement"}

    conflict_raw = _v2_shell(
        tracks=[_track(_block_chord(["C3", "E3", "G3"]))],
        harmony=[{"bar": 1, "chord": "G"}, {"bar": 2, "chord": "G"}],
    )
    conflict = analyze_harmony_from_context(build_analysis_context(conflict_raw))
    assert conflict.declared_agreement == "conflict"

    bad_raw = _v2_shell(
        tracks=[_track(_block_chord(["C3", "E3", "G3"]))],
        harmony=[{"bar": 1, "chord": "???"}],
    )
    # Unparseable symbols: parser may still fail; when all declared fail -> unparseable.
    bad = analyze_harmony_from_context(build_analysis_context(bad_raw))
    assert bad.declared_agreement in {"unparseable", "insufficient_evidence", "conflict", "partial_agreement"}


def test_harmony_analysis_deterministic():
    raw = _v2_shell(
        bar_count=2,
        tracks=[
            _track(
                _block_chord(["C3", "E3", "G3"], start=0, duration=1920)
                + _block_chord(["G3", "B3", "D4"], start=1920, duration=1920)
            )
        ],
        harmony=[{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "G"}],
    )
    first = analyze_harmony_from_context(build_analysis_context(raw))
    second = analyze_harmony_from_context(build_analysis_context(raw))
    assert first.model_dump() == second.model_dump()


def test_roman_labels_when_tonality_accepted():
    raw = _v2_shell(
        key="C major",
        tracks=[_track(_block_chord(["C3", "E3", "G3", "C4"], duration=3840))],
        bar_count=2,
    )
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    result = analyze_harmony_from_context(context, tonality)
    accepted = [span for span in result.spans if span.inference.status == "ok"]
    assert accepted
    # With accepted C major, tonic chord should expose Roman I when confidence allows.
    if tonality.inference.status == "ok":
        assert any(span.roman in {"I", "I7"} or span.function == "tonic" for span in accepted)
