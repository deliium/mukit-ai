"""Tension / dissonance analysis unit coverage."""

from __future__ import annotations

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ChordSpanResult,
    HarmonyAnalysisResult,
    InferenceMeta,
    AnalysisEvidence,
)
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_harmony_analysis import analyze_harmony_from_context
from app.services.composition_tension_analysis import (
    DISSONANCE_TABLE_VERSION,
    TENSION_METHOD,
    analyze_tension_from_context,
)
from app.services.composition_tonality import infer_tonality_from_context


def _v2_shell(*, bar_count: int = 2, tracks=None, key="C major", **overrides):
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
        "tracks": tracks or [],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def _track(events, *, track_id="ten-1", role="harmony"):
    return {
        "id": track_id,
        "name": track_id,
        "instrument": "piano",
        "role": role,
        "midi_program": 0,
        "channel": 1,
        "events": events,
    }


def _block(pitches: list[str], *, start: int = 0, duration: int = 1920):
    return [
        {
            "pitch": pitch,
            "start_tick": start,
            "duration_ticks": duration,
            "velocity": 80 - index,
        }
        for index, pitch in enumerate(pitches)
    ]


def test_consonant_vs_clustered_sonorities():
    consonant = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[_track(_block(["C4", "E4", "G4"]))],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    cluster = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[_track(_block(["C4", "C#4", "D4"]))],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    cons = analyze_tension_from_context(build_analysis_context(consonant))
    clus = analyze_tension_from_context(build_analysis_context(cluster))
    assert cons.components.vertical_dissonance is not None
    assert clus.components.vertical_dissonance is not None
    assert clus.components.vertical_dissonance > cons.components.vertical_dissonance
    assert cons.inference.method == TENSION_METHOD
    assert cons.inference.method_version == ANALYSIS_ALGORITHM_VERSION
    assert DISSONANCE_TABLE_VERSION.startswith("interval_class")


def test_unavailable_components_without_harmony():
    raw = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[_track(_block(["C4", "E4", "G4"]))],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    result = analyze_tension_from_context(context, tonality=tonality, harmony=None)
    assert result.components.vertical_dissonance is not None
    assert result.components.chromatic_mass is not None
    assert result.components.non_chord_tone_mass is None
    assert result.components.functional_distance is None
    assert result.components.unresolved_tendency is None
    assert "non_chord_tone_mass_unavailable" in result.limitations


def test_chromatic_and_nct_with_harmony():
    # Strong C-major mass plus a short Db chromatic/NCT so key stays C major.
    events = (
        _block(["C3", "E3", "G3", "C4"], start=0, duration=1920)
        + [
            {"pitch": "Db4", "start_tick": 1440, "duration_ticks": 480, "velocity": 70},
        ]
    )
    raw = _v2_shell(bar_count=1, duration_ticks=1920, tracks=[_track(events)], sections=[
        {
            "type": "verse",
            "start_bar": 1,
            "bar_count": 1,
            "start_tick": 0,
            "duration_ticks": 1920,
        }
    ])
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)
    harmony = analyze_harmony_from_context(context, tonality)
    result = analyze_tension_from_context(context, tonality=tonality, harmony=harmony)
    assert result.components.chromatic_mass is not None
    assert result.components.chromatic_mass > 0.0
    assert result.components.non_chord_tone_mass is not None
    assert result.components.non_chord_tone_mass > 0.0


def test_dominant_resolution_lowers_unresolved_tendency():
    # V then I with functions provided via synthetic harmony spans.
    events = (
        _block(["G3", "B3", "D4"], start=0, duration=1920)
        + _block(["C3", "E3", "G3"], start=1920, duration=1920)
    )
    raw = _v2_shell(bar_count=2, tracks=[_track(events)])
    context = build_analysis_context(raw)
    tonality = infer_tonality_from_context(context)

    resolved = HarmonyAnalysisResult(
        spans=[
            ChordSpanResult(
                id="v",
                start_tick=0,
                end_tick=1920,
                symbol="G",
                root_pc=7,
                quality="maj",
                function="dominant",
                inference=InferenceMeta(status="ok", confidence=0.9, evidence=AnalysisEvidence(count=1)),
            ),
            ChordSpanResult(
                id="i",
                start_tick=1920,
                end_tick=3840,
                symbol="C",
                root_pc=0,
                quality="maj",
                function="tonic",
                inference=InferenceMeta(status="ok", confidence=0.9, evidence=AnalysisEvidence(count=1)),
            ),
        ],
        inference=InferenceMeta(status="ok"),
    )
    unresolved_harmony = HarmonyAnalysisResult(
        spans=[
            ChordSpanResult(
                id="v-only",
                start_tick=0,
                end_tick=3840,
                symbol="G",
                root_pc=7,
                quality="maj",
                function="dominant",
                inference=InferenceMeta(status="ok", confidence=0.9, evidence=AnalysisEvidence(count=1)),
            ),
        ],
        inference=InferenceMeta(status="ok"),
    )

    resolved_result = analyze_tension_from_context(
        context, tonality=tonality, harmony=resolved
    )
    unresolved_result = analyze_tension_from_context(
        context, tonality=tonality, harmony=unresolved_harmony
    )
    assert resolved_result.components.unresolved_tendency is not None
    assert unresolved_result.components.unresolved_tendency is not None
    assert (
        unresolved_result.components.unresolved_tendency
        >= resolved_result.components.unresolved_tendency
    )


def test_percussion_only_not_applicable():
    events = [
        {"pitch": "C2", "start_tick": 0, "duration_ticks": 480, "velocity": 100},
    ]
    raw = _v2_shell(
        bar_count=1,
        duration_ticks=1920,
        tracks=[
            {
                "id": "drums",
                "name": "drums",
                "instrument": "drums",
                "role": "drums",
                "midi_program": 0,
                "channel": 10,
                "is_drum": True,
                "events": events,
            }
        ],
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
    )
    result = analyze_tension_from_context(build_analysis_context(raw))
    assert result.inference.status == "not_applicable"
    assert "percussion_only_scope" in result.limitations
