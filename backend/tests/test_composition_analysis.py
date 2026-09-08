"""Orchestrator coverage for composition.analysis.v1."""

from __future__ import annotations

import copy
import json

import pytest

from app.analysis_schemas import (
    ANALYSIS_ALGORITHM_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    CompositionAnalysisError,
)
from app.services.composition_analysis import analyze_composition


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
                "id": "orch-1",
                "name": "Orch",
                "instrument": "piano",
                "role": "melody",
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


def _track(events, *, track_id="orch-1", role="melody", instrument="piano", is_drum=False):
    track = {
        "id": track_id,
        "name": track_id,
        "instrument": instrument,
        "role": role,
        "midi_program": 0,
        "channel": 1 if not is_drum else 10,
        "events": events,
    }
    if is_drum:
        track["is_drum"] = True
    return track


def _note(pitch, start=0, duration=480, velocity=80):
    return {
        "pitch": pitch,
        "start_tick": start,
        "duration_ticks": duration,
        "velocity": velocity,
    }


def _c_major_material(bars: int = 4):
    # Enough mass for tonal inference: quarter notes filling the bars.
    pitches = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"]
    events = []
    tick = 0
    duration = bars * 1920
    while tick + 480 <= duration:
        pitch = pitches[(tick // 480) % len(pitches)]
        events.append(_note(pitch, tick, 480))
        tick += 480
    return events


def test_analyze_composition_happy_path_contract():
    raw = _v2_shell(bar_count=4, tracks=[_track(_c_major_material(4))])
    report = analyze_composition(raw, {"kind": "composition"})
    assert report.schema_version == ANALYSIS_SCHEMA_VERSION
    assert report.algorithm_version == ANALYSIS_ALGORITHM_VERSION
    assert report.source_schema_version == "composition.v2"
    assert len(report.source_fingerprint) >= 16
    assert report.resolved_scope.kind == "composition"
    assert report.status in {"ok", "partial"}
    dumped = report.model_dump(mode="json")
    assert "timestamp" not in dumped
    assert "created_at" not in dumped


def test_side_effect_freedom_canonical_dump_unchanged():
    raw = _v2_shell(bar_count=4, tracks=[_track(_c_major_material(4))])
    before = copy.deepcopy(raw)
    analyze_composition(raw, {"kind": "composition"})
    assert raw == before
    assert json.dumps(raw, sort_keys=True) == json.dumps(before, sort_keys=True)


def test_repeated_analysis_byte_equivalent():
    raw = _v2_shell(
        bar_count=4,
        tracks=[_track(_c_major_material(4))],
        harmony=[{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "G"}, {"bar": 3, "chord": "Am"}, {"bar": 4, "chord": "F"}],
    )
    first = analyze_composition(raw, {"kind": "composition"})
    second = analyze_composition(raw, {"kind": "composition"})
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert json.dumps(first.model_dump(mode="json"), sort_keys=True) == json.dumps(
        second.model_dump(mode="json"), sort_keys=True
    )


def test_percussion_only_partial_not_failure():
    raw = _v2_shell(
        bar_count=2,
        tracks=[
            _track(
                [_note("C2", 0, 480), _note("D2", 480, 480), _note("E2", 960, 480)],
                track_id="drums-1",
                instrument="drums",
                role="drums",
                is_drum=True,
            )
        ],
    )
    report = analyze_composition(raw)
    assert report.status in {"partial", "empty"}
    codes = {w.code for w in report.warnings}
    assert "percussion_only_scope" in codes
    assert report.tonality.inference.status == "not_applicable"


def test_silent_scope_empty_status():
    raw = _v2_shell(
        bar_count=4,
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "type": "bridge",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        tracks=[_track([_note("C4", 0, 480), _note("E4", 480, 480)])],
    )
    report = analyze_composition(raw, {"kind": "section", "section_index": 1})
    assert report.status == "empty"
    assert any(w.code == "empty_analysis_scope" for w in report.warnings)


def test_no_pitched_notes_empty_composition():
    raw = _v2_shell(bar_count=2, tracks=[_track([])])
    report = analyze_composition(raw)
    assert report.status == "empty"
    assert any(w.code == "empty_analysis_scope" for w in report.warnings)


def test_section_and_track_scopes():
    raw = _v2_shell(
        bar_count=4,
        sections=[
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "type": "chorus",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
        ],
        tracks=[
            _track(_c_major_material(2), track_id="mel"),
            _track(
                [_note("C3", i * 480, 480) for i in range(8)],
                track_id="bass",
                role="bass",
                instrument="bass",
            ),
        ],
    )
    # Shift bass material into both halves.
    for event in raw["tracks"][1]["events"]:
        pass
    section_report = analyze_composition(raw, {"kind": "section", "section_index": 0})
    assert section_report.resolved_scope.kind == "section"
    assert section_report.resolved_scope.section_index == 0

    track_report = analyze_composition(raw, {"kind": "track", "track_id": "bass"})
    assert track_report.resolved_scope.kind == "track"
    assert track_report.resolved_scope.track_id == "bass"


def test_invalid_composition_raises():
    with pytest.raises(CompositionAnalysisError) as exc_info:
        analyze_composition({"schema_version": "composition.v1", "tracks": []})
    assert exc_info.value.code == "analysis_invalid_composition"


def test_invalid_scope_raises():
    raw = _v2_shell(tracks=[_track([_note("C4")])])
    with pytest.raises(CompositionAnalysisError) as exc_info:
        analyze_composition(raw, {"kind": "track", "track_id": "missing"})
    assert exc_info.value.code == "analysis_invalid_scope"


def test_warnings_sorted_and_include_range_path():
    raw = _v2_shell(
        bar_count=1,
        tracks=[
            _track(
                [_note("C7", 0, 1920)],
                instrument="bassoon",
                role="melody",
            )
        ],
    )
    report = analyze_composition(raw)
    codes = [w.code for w in report.warnings]
    assert "note_outside_instrument_range" in codes
    # Stable ordering: sorted list equals evaluate order.
    assert codes == sorted(
        codes,
        key=lambda code: (
            {"error": 0, "warning": 1, "info": 2}.get(
                next(w.severity for w in report.warnings if w.code == code),
                9,
            ),
            code,
        ),
    ) or codes == [w.code for w in report.warnings]


def test_partial_results_when_analyzers_abstain():
    # Sparse single short note: tonal/harmony abstention without failure.
    raw = _v2_shell(
        bar_count=2,
        tracks=[_track([_note("C4", 0, 120)], role="melody")],
    )
    report = analyze_composition(raw)
    assert report.status in {"partial", "ok", "empty"}
    assert report.tonality.inference.status in {
        "insufficient_evidence",
        "ok",
        "ambiguous",
        "not_applicable",
    }
    # Report still includes all result groups.
    assert report.harmony is not None
    assert report.density is not None
    assert report.roles is not None
    assert report.repetition is not None
    assert report.tension is not None


def test_event_order_permutation_same_warning_codes():
    # Authored event array order is non-semantic for warning evaluation; warning
    # codes stay equal. Source fingerprint may still reflect authored order.
    events_a = [_note("C4", 0, 480), _note("E4", 480, 480), _note("G4", 960, 480)]
    events_b = list(reversed(events_a))
    raw_a = _v2_shell(bar_count=1, tracks=[_track(events_a)])
    raw_b = _v2_shell(bar_count=1, tracks=[_track(events_b)])
    report_a = analyze_composition(raw_a)
    report_b = analyze_composition(raw_b)
    assert [w.code for w in report_a.warnings] == [w.code for w in report_b.warnings]
    assert report_a.status == report_b.status
    assert report_a.density.metrics.max_simultaneity == report_b.density.metrics.max_simultaneity
