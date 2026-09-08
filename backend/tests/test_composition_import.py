"""Unit tests for shared source → composition.v2 canonicalization."""

from __future__ import annotations

import pytest

from app.import_schemas import CompositionImportError
from app.import_settings import load_import_settings
from app.services.composition_import import (
    ParsedSourceScore,
    SourceNoteEvent,
    SourceTempoChange,
    SourceTrack,
    allocate_stable_id,
    canonicalize_source_score,
    midi_number_to_pitch,
)
from app.services.composition_timeline import compile_timeline


def _score(**overrides) -> ParsedSourceScore:
    score = ParsedSourceScore(
        format="midi",
        source_ppq=480,
        content_end_tick=480,
        root_tempo=100,
        root_time_signature="4/4",
        root_key="C major",
        tracks=[
            SourceTrack(
                source_track_index=0,
                channel=1,
                program=0,
                name="Piano",
                notes=[
                    SourceNoteEvent(
                        onset_tick=0,
                        duration_ticks=480,
                        pitch_midi=60,
                        velocity=90,
                        source_ordinal=0,
                    )
                ],
            )
        ],
    )
    for key, value in overrides.items():
        setattr(score, key, value)
    return score


def test_canonicalize_minimal_score_produces_valid_v2():
    result = canonicalize_source_score(
        _score(),
        display_filename="demo.mid",
        input_bytes=64,
    )
    composition = result.composition
    assert composition.schema_version == "composition.v2"
    assert composition.harmony == []
    assert composition.sections[0].type == "unsectioned"
    assert composition.tracks[0].role == "other"
    assert len(composition.tracks[0].events) == 1
    assert composition.tracks[0].events[0].pitch == "C4"
    compile_timeline(composition)
    assert any(issue.code == "section_defaulted" for issue in result.import_report.issues)
    assert any(issue.code == "role_defaulted" for issue in result.import_report.issues)
    assert any(issue.code == "pitch_spelling_inferred" for issue in result.import_report.issues)


def test_canonicalize_pads_partial_final_bar():
    result = canonicalize_source_score(
        _score(content_end_tick=100),
        display_filename="pad.mid",
        input_bytes=32,
    )
    assert result.composition.duration_ticks == 1920
    assert result.composition.bar_count == 1
    assert any(issue.code == "partial_measure_padded" for issue in result.import_report.issues)


def test_canonicalize_defaults_missing_metadata():
    result = canonicalize_source_score(
        _score(root_tempo=None, root_time_signature=None, root_key=None),
        display_filename="defaults.mid",
        input_bytes=32,
    )
    assert result.composition.tempo == 120
    assert result.composition.time_signature == "4/4"
    assert result.composition.key == "C major"
    codes = {issue.code for issue in result.import_report.issues}
    assert {"tempo_defaulted", "meter_defaulted", "key_defaulted"} <= codes


def test_canonicalize_preserves_midi_ppq_and_stable_ids():
    first = canonicalize_source_score(_score(), display_filename="a.mid", input_bytes=10)
    second = canonicalize_source_score(_score(), display_filename="b.mid", input_bytes=10)
    assert first.composition.ticks_per_quarter == 480
    assert first.composition.tracks[0].id == second.composition.tracks[0].id
    assert first.composition.tracks[0].events[0].id == second.composition.tracks[0].events[0].id


def test_canonicalize_bass_and_drums_roles():
    score = _score(
        tracks=[
            SourceTrack(
                source_track_index=0,
                channel=1,
                program=33,
                name="Bass",
                instrument_name="Electric Bass (finger)",
                notes=[
                    SourceNoteEvent(
                        onset_tick=0,
                        duration_ticks=480,
                        pitch_midi=40,
                        velocity=80,
                        source_ordinal=0,
                    )
                ],
            ),
            SourceTrack(
                source_track_index=1,
                channel=10,
                program=0,
                name="Drums",
                is_drum=True,
                notes=[
                    SourceNoteEvent(
                        onset_tick=0,
                        duration_ticks=240,
                        pitch_midi=36,
                        velocity=100,
                        source_ordinal=0,
                    )
                ],
            ),
        ]
    )
    result = canonicalize_source_score(score, display_filename="roles.mid", input_bytes=20)
    roles = {track.role for track in result.composition.tracks}
    assert roles == {"bass", "drums"}


def test_key_aware_pitch_spelling():
    assert midi_number_to_pitch(70, key=None) == "A#4"
    assert midi_number_to_pitch(70, key="F major") == "Bb4"
    assert midi_number_to_pitch(60, key="C major") == "C4"


def test_stable_id_collision_suffix():
    used: set[str] = set()
    first, collided_first = allocate_stable_id(prefix="track", parts=["x"], used=used)
    # Force collision by pre-seeding the exact deterministic id.
    used.clear()
    used.add(first)
    second, collided_second = allocate_stable_id(prefix="track", parts=["x"], used=used)
    assert collided_first is False
    assert collided_second is True
    assert second.startswith(first)
    assert second != first


def test_complexity_limit_rejects_too_many_tracks():
    settings = load_import_settings({"IMPORT_MAX_TRACKS": "1"})
    score = _score(
        tracks=[
            SourceTrack(source_track_index=0, channel=1, program=0, notes=[]),
            SourceTrack(source_track_index=1, channel=2, program=0, notes=[]),
        ]
    )
    with pytest.raises(CompositionImportError) as exc_info:
        canonicalize_source_score(
            score,
            display_filename="too-many.mid",
            input_bytes=8,
            settings=settings,
        )
    assert exc_info.value.code == "import_complexity_exceeded"
    assert exc_info.value.http_status == 413


def test_tempo_change_after_scale_is_retained():
    score = _score(
        content_end_tick=3840,
        tempo_changes=[SourceTempoChange(tick=1920, bpm=140.4)],
    )
    result = canonicalize_source_score(score, display_filename="tempo.mid", input_bytes=16)
    assert result.composition.tempo_changes[0].tick == 1920
    assert result.composition.tempo_changes[0].bpm == 140
    assert any(issue.code == "tempo_rounded" for issue in result.import_report.issues)
