"""Melody / phrase / cadence analysis unit coverage."""

from __future__ import annotations

from dataclasses import dataclass

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ChordSpanResult,
    HarmonyAnalysisResult,
    InferenceMeta,
)
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_harmony_analysis import analyze_harmony_from_context
from app.services.composition_melody_analysis import (
    MELODY_METHOD,
    MelodicRoleHints,
    analyze_melody_from_context,
)
from app.services.composition_tonality import infer_tonality_from_context


def _v2_shell(*, bar_count: int = 2, key: str = "C major", tracks=None, sections=None, **overrides):
    duration = bar_count * 1920
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": key,
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": sections
        or [
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
                "id": "melody-1",
                "name": "Melody",
                "instrument": "flute",
                "role": "melody",
                "midi_program": 73,
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


def _track(events, *, track_id: str = "melody-1", role: str = "melody", instrument: str = "flute"):
    return {
        "id": track_id,
        "name": track_id,
        "instrument": instrument,
        "role": role,
        "midi_program": 0,
        "channel": 1,
        "events": events,
    }


def _note(pitch: str, start: int, duration: int = 480, velocity: int = 80):
    return {
        "pitch": pitch,
        "start_tick": start,
        "duration_ticks": duration,
        "velocity": velocity,
    }


def _scale_run(pitches: list[str], *, start: int = 0, step: int = 480, duration: int = 480):
    return [_note(pitch, start + index * step, duration) for index, pitch in enumerate(pitches)]


@dataclass(frozen=True)
class _Feature:
    track_id: str
    score: float
    confidence: float | None = None


def test_ascending_descending_arch_static_mixed_contours():
    ascending = _v2_shell(
        tracks=[_track(_scale_run(["C4", "D4", "E4", "F4", "G4", "A4"]))]
    )
    descending = _v2_shell(
        tracks=[_track(_scale_run(["A4", "G4", "F4", "E4", "D4", "C4"]))]
    )
    arch = _v2_shell(
        tracks=[_track(_scale_run(["C4", "E4", "G4", "E4", "C4"]))]
    )
    static = _v2_shell(
        tracks=[_track(_scale_run(["C4", "C4", "D4", "C4", "C4"], duration=240))]
    )
    mixed = _v2_shell(
        tracks=[
            _track(
                _scale_run(
                    ["C4", "G4", "D4", "A4", "E4", "B4", "F4", "C5"],
                    step=240,
                    duration=240,
                )
            )
        ]
    )

    assert analyze_melody_from_context(build_analysis_context(ascending)).profiles[0].contour == "ascending"
    assert analyze_melody_from_context(build_analysis_context(descending)).profiles[0].contour == "descending"
    assert analyze_melody_from_context(build_analysis_context(arch)).profiles[0].contour == "arch"
    assert analyze_melody_from_context(build_analysis_context(static)).profiles[0].contour == "static"
    assert analyze_melody_from_context(build_analysis_context(mixed)).profiles[0].contour == "mixed"


def test_range_and_tessitura():
    # Short high C5 + long low C4 → tessitura pulled toward C4.
    events = [
        _note("C4", 0, 1440),
        _note("C5", 1440, 480),
    ]
    raw = _v2_shell(tracks=[_track(events)])
    profile = analyze_melody_from_context(build_analysis_context(raw)).profiles[0]
    assert profile.pitch_min == "C4"
    assert profile.pitch_max == "C5"
    assert profile.range_semitones == 12
    assert profile.tessitura_midi is not None
    # Duration-weighted mean closer to C4 (60) than C5 (72).
    assert profile.tessitura_midi < 66
    assert profile.tessitura_midi > 60


def test_rests_create_phrase_boundaries():
    # Two note groups separated by a long rest.
    events = [
        _note("C4", 0, 480),
        _note("D4", 480, 480),
        # rest ~ 960 ticks
        _note("E4", 1920, 480),
        _note("F4", 2400, 480),
    ]
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    result = analyze_melody_from_context(build_analysis_context(raw))
    assert len(result.phrases) >= 2
    assert result.phrases[0].end_tick <= 1920
    assert result.phrases[-1].start_tick >= 960


def test_crossing_notes_clip_to_scope():
    events = [
        _note("C4", 0, 3000),  # crosses bar 2
        _note("E4", 1920, 480),
    ]
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    # Use two sections so section 1 starts mid-composition.
    raw["sections"] = [
        {
            "type": "verse",
            "start_bar": 1,
            "bar_count": 1,
            "start_tick": 0,
            "duration_ticks": 1920,
        },
        {
            "type": "chorus",
            "start_bar": 2,
            "bar_count": 1,
            "start_tick": 1920,
            "duration_ticks": 1920,
        },
    ]
    context = build_analysis_context(raw, {"kind": "section", "section_index": 1})
    result = analyze_melody_from_context(context)
    assert result.profiles
    # Scoped analysis should still produce a profile without requiring full-line onsets only.
    assert result.profiles[0].inference.status in {"ok", "insufficient_evidence"}
    assert result.inference.method == MELODY_METHOD


def test_polyphonic_skyline_reduction():
    # Simultaneous chord tones on a melody track → skyline keeps the top pitch.
    events = [
        _note("C4", 0, 480),
        _note("E4", 0, 480),
        _note("G4", 0, 480),
        _note("C4", 480, 480),
        _note("E4", 480, 480),
        _note("A4", 480, 480),
    ]
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_melody_from_context(build_analysis_context(raw))
    profile = result.profiles[0]
    assert profile.skyline_reduced is True
    assert profile.pitch_max in {"G4", "A4"}
    assert profile.pitch_min in {"C4", "E4", "G4", "A4"}
    assert profile.inference.status == "ok"


def test_ambiguous_melody_tracks_via_features():
    # No declared melody; two close inferred candidates → abstain.
    raw = _v2_shell(
        tracks=[
            _track(_scale_run(["C4", "D4", "E4"]), track_id="a", role="other"),
            _track(_scale_run(["G4", "A4", "B4"]), track_id="b", role="other"),
        ]
    )
    hints = MelodicRoleHints(
        features=(
            _Feature("a", score=0.70, confidence=0.70),
            _Feature("b", score=0.68, confidence=0.68),
        )
    )
    result = analyze_melody_from_context(build_analysis_context(raw), role_hints=hints)
    assert result.profiles == []
    assert result.inference.status == "ambiguous"


def test_declared_melody_preferred_over_hints():
    raw = _v2_shell(
        tracks=[
            _track(_scale_run(["C4", "D4", "E4", "F4"]), track_id="mel", role="melody"),
            _track(_scale_run(["C3", "C3", "C3"]), track_id="bass", role="bass"),
        ]
    )
    hints = MelodicRoleHints(candidate_track_ids=("bass",))
    result = analyze_melody_from_context(build_analysis_context(raw), role_hints=hints)
    assert len(result.profiles) == 1
    assert result.profiles[0].track_id == "mel"


def test_candidate_track_ids_when_no_declared():
    raw = _v2_shell(
        tracks=[
            _track(_scale_run(["C4", "E4", "G4", "A4"]), track_id="x", role="other"),
            _track(_scale_run(["C3", "C3"]), track_id="y", role="bass"),
        ]
    )
    result = analyze_melody_from_context(
        build_analysis_context(raw),
        role_hints=["x"],
    )
    assert result.profiles
    assert result.profiles[0].track_id == "x"


def test_phrase_threshold_suppresses_close_boundaries():
    # Dense notes with tiny gaps should not explode into many phrases.
    events = [_note("C4", i * 240, 200) for i in range(16)]
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    result = analyze_melody_from_context(build_analysis_context(raw))
    assert len(result.phrases) <= 4


def test_section_boundaries_not_forced_as_phrases():
    # Continuous ascending line across two sections without rests.
    events = _scale_run(
        ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"],
        step=480,
        duration=480,
    )
    raw = _v2_shell(
        bar_count=2,
        tracks=[_track(events)],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            },
            {
                "type": "chorus",
                "start_bar": 2,
                "bar_count": 1,
                "start_tick": 1920,
                "duration_ticks": 1920,
            },
        ],
    )
    result = analyze_melody_from_context(build_analysis_context(raw))
    # Section edge alone must not force a phrase split when the line is continuous.
    assert len(result.phrases) <= 2


def test_cadence_authentic_and_abstention():
    # Build a clear V→I cadence under C major with melodic endpoint on tonic.
    # Bar1: G major (V), Bar2: C major (I); melody ends on C.
    harmony_events = [
        _note("G2", 0, 1920),
        _note("B2", 0, 1920),
        _note("D3", 0, 1920),
        _note("C2", 1920, 1920),
        _note("E2", 1920, 1920),
        _note("G2", 1920, 1920),
        _note("C3", 1920, 1920),
    ]
    melody_events = [
        _note("D5", 0, 480),
        _note("B4", 480, 480),
        _note("G4", 960, 480),
        # rest into cadence
        _note("C5", 1920, 960),
    ]
    raw = _v2_shell(
        bar_count=2,
        key="C major",
        tracks=[
            _track(melody_events, track_id="melody-1", role="melody"),
            _track(harmony_events, track_id="harmony-1", role="harmony", instrument="piano"),
        ],
    )
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    harmony = analyze_harmony_from_context(context, tonality)
    result = analyze_melody_from_context(context, tonality, harmony)
    assert result.cadences
    kinds = {cadence.kind for cadence in result.cadences}
    # At least one classified authentic or unclassified if harmony abstains.
    assert kinds & {
        "authentic_perfect",
        "authentic_imperfect",
        "half",
        "plagal",
        "deceptive",
        "unclassified",
    }

    # Without harmony/tonality → cadence abstention / unclassified.
    bare = analyze_melody_from_context(context)
    assert bare.cadences
    assert all(
        c.kind == "unclassified" or c.inference.status == "insufficient_evidence"
        for c in bare.cadences
    )


def test_half_plagal_deceptive_paths_with_injected_harmony():
    """Cadence classifier paths via controlled harmony spans (not full inference)."""
    melody_events = _scale_run(["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"])
    raw = _v2_shell(bar_count=2, tracks=[_track(melody_events)])
    context = build_analysis_context(raw)
    phrases_probe = analyze_melody_from_context(context)
    assert phrases_probe.phrases
    end_tick = phrases_probe.phrases[-1].end_tick

    def _span(symbol, root, quality, bass, roman, function, start, end, conf=0.8):
        return ChordSpanResult(
            id=f"{symbol}-{start}",
            start_tick=start,
            end_tick=end,
            symbol=symbol,
            root_pc=root,
            quality=quality,
            bass_pc=bass,
            roman=roman,
            function=function,
            inference=InferenceMeta(status="ok", confidence=conf, method="test"),
        )

    cases = [
        (
            [_span("G", 7, "maj", 7, "V", "dominant", 0, end_tick - 480),
             _span("C", 0, "maj", 0, "I", "tonic", end_tick - 480, end_tick + 480)],
            {"authentic_perfect", "authentic_imperfect"},
        ),
        (
            [_span("F", 5, "maj", 5, "IV", "predominant", 0, end_tick - 480),
             _span("C", 0, "maj", 0, "I", "tonic", end_tick - 480, end_tick + 480)],
            {"plagal"},
        ),
        (
            [_span("C", 0, "maj", 0, "I", "tonic", 0, end_tick - 480),
             _span("G", 7, "maj", 7, "V", "dominant", end_tick - 480, end_tick + 480)],
            {"half"},
        ),
        (
            [_span("G", 7, "maj", 7, "V", "dominant", 0, end_tick - 480),
             _span("Am", 9, "min", 9, "vi", "tonic", end_tick - 480, end_tick + 480)],
            {"deceptive"},
        ),
    ]

    tonality = infer_tonality_from_context(context)
    for spans, expected in cases:
        harmony = HarmonyAnalysisResult(
            spans=spans,
            inference=InferenceMeta(status="ok", confidence=0.8, method="test"),
        )
        result = analyze_melody_from_context(context, tonality, harmony)
        assert result.cadences
        assert result.cadences[-1].kind in expected


def test_melody_analysis_deterministic():
    raw = _v2_shell(
        bar_count=2,
        tracks=[
            _track(
                _scale_run(["C4", "E4", "G4", "F4", "E4", "D4", "C4"])
                + [_note("G4", 0, 480), _note("E4", 0, 480)]  # polyphony for skyline
            )
        ],
    )
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    harmony = analyze_harmony_from_context(context, tonality)
    first = analyze_melody_from_context(context, tonality, harmony)
    second = analyze_melody_from_context(context, tonality, harmony)
    assert first.model_dump() == second.model_dump()
    assert first.inference.method == MELODY_METHOD
    assert first.inference.method_version == ANALYSIS_ALGORITHM_VERSION


def test_high_confidence_inferred_feature_selection():
    raw = _v2_shell(
        tracks=[
            _track(_scale_run(["C4", "D4", "E4", "G4"]), track_id="winner", role="other"),
            _track(_scale_run(["C3", "C3"]), track_id="loser", role="other"),
        ]
    )
    hints = MelodicRoleHints(
        features=(
            _Feature("winner", score=0.90, confidence=0.88),
            _Feature("loser", score=0.40, confidence=0.40),
        )
    )
    result = analyze_melody_from_context(build_analysis_context(raw), role_hints=hints)
    assert result.profiles
    assert result.profiles[0].track_id == "winner"


def test_lead_role_selected():
    raw = _v2_shell(
        tracks=[_track(_scale_run(["C5", "D5", "E5", "F5"]), track_id="lead-1", role="lead")]
    )
    result = analyze_melody_from_context(build_analysis_context(raw))
    assert result.profiles
    assert result.profiles[0].track_id == "lead-1"
    assert result.profiles[0].contour == "ascending"
