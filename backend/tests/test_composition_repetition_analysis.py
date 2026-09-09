"""Repetition / motif analysis unit coverage."""

from __future__ import annotations

from app.analysis_schemas import ANALYSIS_ALGORITHM_VERSION
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_repetition_analysis import (
    REPETITION_METHOD,
    analyze_repetition_from_context,
)


def _v2_shell(*, bar_count: int = 4, tracks=None, sections=None, **overrides):
    duration = bar_count * 1920
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": bar_count,
        "duration_ticks": duration,
        "sections": sections
        or [
            {
                "id": "a",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            },
            {
                "id": "b",
                "type": "chorus",
                "start_bar": 3,
                "bar_count": 2,
                "start_tick": 3840,
                "duration_ticks": 3840,
            },
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


def _track(events, *, track_id="rep-1"):
    return {
        "id": track_id,
        "name": track_id,
        "instrument": "flute",
        "role": "melody",
        "midi_program": 73,
        "channel": 1,
        "events": events,
    }


def _phrase(pitches: list[str], *, start: int, step: int = 480, dur: int = 480):
    return [
        {
            "pitch": pitch,
            "start_tick": start + index * step,
            "duration_ticks": dur,
            "velocity": 80,
        }
        for index, pitch in enumerate(pitches)
    ]


def test_exact_motif_detection():
    phrase = ["C4", "D4", "E4", "G4"]
    events = _phrase(phrase, start=0) + _phrase(phrase, start=1920)
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    exact = [motif for motif in result.motifs if motif.kind == "exact"]
    assert exact
    assert result.inference.method == REPETITION_METHOD
    assert result.inference.method_version == ANALYSIS_ALGORITHM_VERSION
    # Deterministic ordering: exact before transposed/rhythm.
    kinds = [motif.kind for motif in result.motifs]
    if len(kinds) >= 2:
        assert kinds == sorted(kinds, key=lambda k: {"exact": 0, "transposed": 1, "rhythm_only": 2}[k])


def test_transposed_motif_detection():
    events = _phrase(["C4", "D4", "E4", "G4"], start=0) + _phrase(
        ["D4", "E4", "F#4", "A4"], start=1920
    )
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    transposed = [motif for motif in result.motifs if motif.kind == "transposed"]
    assert transposed
    assert any(motif.transposition_semitones == 2 for motif in transposed)


def test_rhythm_only_motif_detection():
    # Same rhythm, different intervals.
    events = _phrase(["C4", "E4", "G4", "B4"], start=0, step=480) + _phrase(
        ["C4", "D4", "F4", "A4"], start=1920, step=480
    )
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    rhythm = [motif for motif in result.motifs if motif.kind == "rhythm_only"]
    assert rhythm


def test_repeated_sections_have_fingerprints():
    # Identical material in both sections → equal fingerprint digests embedded in IDs.
    phrase = _phrase(["C4", "D4", "E4", "F4"], start=0) + _phrase(
        ["C4", "D4", "E4", "F4"], start=3840
    )
    raw = _v2_shell(tracks=[_track(phrase)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    assert len(result.section_fingerprint_ids) == 2
    # Fingerprint IDs are deterministic and non-empty.
    assert all(item.startswith("section_fp:") for item in result.section_fingerprint_ids)
    # Content-equal sections share the same digest suffix.
    left = result.section_fingerprint_ids[0].rsplit(":", 1)[-1]
    right = result.section_fingerprint_ids[1].rsplit(":", 1)[-1]
    assert left == right


def test_insufficient_notes_abstains():
    events = _phrase(["C4", "D4"], start=0)
    raw = _v2_shell(bar_count=1, tracks=[_track(events)], sections=[
        {
            "type": "verse",
            "start_bar": 1,
            "bar_count": 1,
            "start_tick": 0,
            "duration_ticks": 1920,
        }
    ], duration_ticks=1920)
    result = analyze_repetition_from_context(build_analysis_context(raw))
    assert result.inference.status == "insufficient_evidence"
    assert result.motifs == []
    assert result.motif_families == []


def test_motif_families_group_reference_and_matches():
    phrase = ["C4", "D4", "E4", "G4"]
    events = _phrase(phrase, start=0) + _phrase(phrase, start=1920)
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    assert result.motif_families
    family = next(item for item in result.motif_families if "exact" in item.relationship_kinds)
    assert family.reference.kind == "exact"
    assert family.matched_occurrences
    assert family.reference.start_tick < family.matched_occurrences[0].start_tick
    assert family.reference.notes
    assert all(note.event_indexes is not None for note in family.reference.notes)


def test_motif_families_use_event_ids_when_present():
    phrase = ["C4", "D4", "E4", "G4"]
    events = []
    for index, pitch in enumerate(phrase):
        events.append(
            {
                "id": f"n{index}",
                "pitch": pitch,
                "start_tick": index * 480,
                "duration_ticks": 480,
                "velocity": 80,
            }
        )
    for index, pitch in enumerate(phrase):
        events.append(
            {
                "id": f"n{index + 4}",
                "pitch": pitch,
                "start_tick": 1920 + index * 480,
                "duration_ticks": 480,
                "velocity": 80,
            }
        )
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    family = next(item for item in result.motif_families if "exact" in item.relationship_kinds)
    assert all(note.event_ids for note in family.reference.notes)


def test_inversion_detection():
    up = ["C4", "D4", "E4", "F4"]
    down = ["C4", "Bb3", "Ab3", "Gb3"]
    events = _phrase(up, start=0) + _phrase(down, start=1920)
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    kinds = {kind for family in result.motif_families for kind in family.relationship_kinds}
    assert "inversion" in kinds


def test_augmentation_detection():
    base = _phrase(["C4", "D4", "E4"], start=0, step=480, dur=480)
    augmented = _phrase(["C4", "D4", "E4"], start=3840, step=960, dur=960)
    raw = _v2_shell(tracks=[_track(base + augmented)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    kinds = {kind for family in result.motif_families for kind in family.relationship_kinds}
    assert "augmentation" in kinds


def test_family_ids_are_sha256_prefixed():
    phrase = ["C4", "D4", "E4", "G4"]
    events = _phrase(phrase, start=0) + _phrase(phrase, start=1920)
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    assert all(family.id.startswith("motif_family:") for family in result.motif_families)
    assert all(len(family.id.split(":", 1)[1]) == 16 for family in result.motif_families)


def test_repetition_method_reports_skyline_projection():
    phrase = ["C4", "D4", "E4", "G4"]
    events = _phrase(phrase, start=0) + _phrase(phrase, start=1920)
    raw = _v2_shell(tracks=[_track(events)])
    result = analyze_repetition_from_context(build_analysis_context(raw))
    assert result.inference.method == REPETITION_METHOD
    assert "skyline" in result.inference.method
