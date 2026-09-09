"""Motif extraction, deterministic transforms, and identity verification."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.composition_schemas import CompositionV2, midi_pitch_number
from app.services.composition_motif_similarity import (
    WARN_IDENTITY_BELOW_THRESHOLD,
    identity_threshold,
    verify_motif_identity,
)
from app.services.composition_motif_transform import (
    AnswerCounterphraseProposal,
    MelodicVariationProposal,
    MotifTransformError,
    RhythmicVariationProposal,
    build_motif_destination,
    extract_relative_motif,
    transform_augmentation,
    transform_diminution,
    transform_inversion,
    transform_repeat,
    transform_sequence,
    transform_transpose,
    realize_melodic_variation,
    realize_rhythmic_variation,
    validate_answer_or_counterphrase,
)


def _v2_shell(*, bar_count: int = 4, tracks=None, **overrides):
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
                "id": "a",
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
    return CompositionV2.model_validate(data)


def _note(
    pitch: str,
    *,
    start: int,
    duration: int = 480,
    velocity: int = 80,
    event_id: str,
    staff: str | None = None,
    voice: int | None = None,
    articulations: list[str] | None = None,
    tie: dict | None = None,
):
    payload = {
        "pitch": pitch,
        "start_tick": start,
        "duration_ticks": duration,
        "velocity": velocity,
        "id": event_id,
    }
    if staff is not None:
        payload["staff"] = staff
    if voice is not None:
        payload["voice"] = voice
    if articulations:
        payload["articulations"] = articulations
    if tie is not None:
        payload["tie"] = tie
    return payload


def _track(events, *, track_id="melody-1", role="melody", instrument="flute"):
    return {
        "id": track_id,
        "name": track_id,
        "instrument": instrument,
        "role": role,
        "midi_program": 73,
        "channel": 1,
        "events": events,
    }


def _phrase_ids(prefix: str, pitches: list[str], *, start: int, step: int = 480):
    return [
        _note(pitch, start=start + index * step, event_id=f"{prefix}-{index}")
        for index, pitch in enumerate(pitches)
    ]


def _extract(composition: CompositionV2, track_id: str, event_ids: list[str]):
    extracted = extract_relative_motif(composition, track_id=track_id, event_ids=event_ids)
    return extracted.notes, extracted.anchor_midi


def _dest(composition: CompositionV2, *, track_id: str, start: int, anchor_midi: int, allow_overlap=False):
    return build_motif_destination(
        composition,
        track_id=track_id,
        start_tick=start,
        anchor_midi=anchor_midi,
        allow_overlap=allow_overlap,
    )


def _event_vector(events):
    return [
        (
            event.pitch,
            event.start_tick,
            event.duration_ticks,
            event.velocity,
            tuple(event.articulations),
            event.tie.type if event.tie else None,
        )
        for event in events
    ]


def test_extract_relative_motif_three_note_phrase():
    events = _phrase_ids("src", ["C4", "E4", "G4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    notes, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    assert anchor == midi_pitch_number("C4")
    assert len(notes) == 3
    assert notes[0].pitch_semitone_offset == 0
    assert notes[1].pitch_semitone_offset == 4
    assert notes[2].pitch_semitone_offset == 7
    assert notes[0].relative_start_tick == 0
    assert notes[1].relative_start_tick == 480


def test_transform_repeat_exact_event_vector():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    destination = _dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor)
    result = transform_repeat(
        source,
        composition=composition,
        destination=destination,
        id_seed="seed-repeat",
    )
    assert _event_vector(result.events) == [
        ("C4", 1920, 480, 80, (), None),
        ("D4", 2400, 480, 80, (), None),
        ("E4", 2880, 480, 80, (), None),
    ]
    assert result.verification.components.exact_transform_verified is True


def test_transform_transpose_positive_and_negative():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])

    up = transform_transpose(
        source,
        semitones=2,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-up",
    )
    assert [event.pitch for event in up.events] == ["D4", "E4", "F#4"]

    high_events = _phrase_ids("hi", ["E4", "F#4", "G#4"], start=0)
    high_composition = _v2_shell(tracks=[_track(high_events)])
    high_source, high_anchor = _extract(high_composition, "melody-1", ["hi-0", "hi-1", "hi-2"])
    down = transform_transpose(
        high_source,
        semitones=-2,
        composition=high_composition,
        destination=_dest(high_composition, track_id="melody-1", start=1920, anchor_midi=high_anchor),
        id_seed="seed-down",
    )
    assert [event.pitch for event in down.events] == ["D4", "E4", "F#4"]


def test_transform_inversion_first_note_axis():
    events = _phrase_ids("src", ["C5", "E5", "G5"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    result = transform_inversion(
        source,
        axis_pitch=None,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-inv-axis-first",
    )
    assert [event.pitch for event in result.events] == ["C5", "G#4", "F4"]


def test_transform_inversion_explicit_axis():
    events = _phrase_ids("src", ["C4", "E4", "G4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    result = transform_inversion(
        source,
        axis_pitch="G4",
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-inv-axis-g4",
    )
    assert [event.pitch for event in result.events] == ["D5", "A#4", "G4"]


def test_augmentation_and_diminution_rational_rounding():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0, step=480)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])

    aug = transform_augmentation(
        source,
        numerator=2,
        denominator=1,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=3840, anchor_midi=anchor),
        id_seed="seed-aug",
    )
    assert [(event.start_tick, event.duration_ticks) for event in aug.events] == [
        (3840, 960),
        (4800, 960),
        (5760, 960),
    ]

    dim = transform_diminution(
        source,
        numerator=1,
        denominator=2,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=6720, anchor_midi=anchor),
        id_seed="seed-dim",
    )
    assert [(event.start_tick, event.duration_ticks) for event in dim.events] == [
        (6720, 240),
        (6960, 240),
        (7200, 240),
    ]


def test_sequence_steps_and_intervals():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    result = transform_sequence(
        source,
        steps=2,
        interval_semitones=2,
        step_ticks=240,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=3840, anchor_midi=anchor),
        id_seed="seed-seq",
    )
    pitches = [event.pitch for event in result.events]
    assert pitches == ["C4", "D4", "E4", "D4", "E4", "F#4"]
    starts = [event.start_tick for event in result.events]
    assert starts[3] - starts[0] == 1680  # motif span (1440) + step gap (240)


def test_variable_meter_destination_bounds():
    composition = _v2_shell(
        bar_count=3,
        duration_ticks=4800,
        time_signature="4/4",
        time_signature_changes=[
            {"tick": 1920, "time_signature": "3/4"},
            {"tick": 3360, "time_signature": "6/8"},
        ],
        sections=[
            {
                "id": "a",
                "type": "verse",
                "start_bar": 1,
                "bar_count": 3,
                "start_tick": 0,
                "duration_ticks": 4800,
            }
        ],
        tracks=[_track(_phrase_ids("src", ["C4", "D4", "E4"], start=0))],
    )
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    destination = _dest(composition, track_id="melody-1", start=4320, anchor_midi=anchor)
    with pytest.raises(MotifTransformError) as exc:
        transform_repeat(
            source,
            composition=composition,
            destination=destination,
            id_seed="seed-overflow",
        )
    assert exc.value.code == "motif_destination_out_of_bounds"


def test_strength_boundary_rhythmic_variation():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])

    close = RhythmicVariationProposal(
        note_count=3,
        onset_delta_ticks=[0, 480, 960],
        duration_ticks=[480, 480, 480],
    )
    ok = realize_rhythmic_variation(
        source,
        close,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-rhythm-close",
        variation_strength=0.0,
    )
    assert ok.verification.passed

    far = RhythmicVariationProposal(
        note_count=3,
        onset_delta_ticks=[0, 960, 1920],
        duration_ticks=[240, 240, 240],
    )
    with pytest.raises(MotifTransformError) as exc:
        realize_rhythmic_variation(
            source,
            far,
            composition=composition,
            destination=_dest(composition, track_id="melody-1", start=3840, anchor_midi=anchor),
            id_seed="seed-rhythm-far",
            variation_strength=0.0,
        )
    assert exc.value.code == "motif_identity_failed"


def test_melodic_variation_strength_boundary():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])

    subtle = MelodicVariationProposal(note_count=3, pitch_semitone_offsets=[0, 0, 1])
    ok = realize_melodic_variation(
        source,
        subtle,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-mel-subtle",
        variation_strength=1.0,
    )
    assert ok.verification.passed

    wild = MelodicVariationProposal(note_count=3, pitch_semitone_offsets=[0, 12, -12])
    with pytest.raises(MotifTransformError):
        realize_melodic_variation(
            source,
            wild,
            composition=composition,
            destination=_dest(composition, track_id="melody-1", start=3840, anchor_midi=anchor),
            id_seed="seed-mel-wild",
            variation_strength=0.0,
        )


def test_source_immutability():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    before = copy.deepcopy(composition)
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    transform_transpose(
        source,
        semitones=5,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-immutable",
    )
    assert composition.model_dump() == before.model_dump()


def test_tie_and_articulation_preserved():
    events = [
        _note(
            "C4",
            start=0,
            duration=480,
            velocity=90,
            event_id="tie-0",
            staff="treble",
            voice=1,
            articulations=["accent"],
            tie={"group_id": "tg-1", "type": "start"},
        ),
        _note(
            "C4",
            start=480,
            duration=480,
            velocity=90,
            event_id="tie-1",
            staff="treble",
            voice=1,
            tie={"group_id": "tg-1", "type": "stop"},
        ),
        _note("E4", start=960, duration=480, velocity=90, event_id="n-2", staff="treble", voice=1),
    ]
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["tie-0", "tie-1", "n-2"])
    assert source[0].tie_segment_durations == (480, 480)
    assert source[0].articulations == ("accent",)

    result = transform_repeat(
        source,
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-tie",
    )
    assert len(result.events) == 3
    assert result.events[0].tie and result.events[0].tie.type == "start"
    assert result.events[1].tie and result.events[1].tie.type == "stop"
    assert result.events[0].articulations == ["accent"]
    assert result.events[1].articulations == []
    assert result.events[0].staff == "treble"
    assert result.events[0].voice == 1


def test_pitch_range_error_on_bass_track():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(
        tracks=[
            _track(events, track_id="melody-1", role="melody"),
            {
                "id": "bass-1",
                "name": "Bass",
                "instrument": "bass",
                "role": "bass",
                "midi_program": 32,
                "channel": 2,
                "events": [],
            },
        ]
    )
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    with pytest.raises(MotifTransformError) as exc:
        transform_transpose(
            source,
            semitones=0,
            composition=composition,
            destination=_dest(composition, track_id="bass-1", start=1920, anchor_midi=anchor),
            id_seed="seed-range",
        )
    assert exc.value.code == "motif_pitch_out_of_range"


def test_overlap_rejected_without_allow_flag():
    existing = _phrase_ids("block", ["G4", "A4", "B4"], start=960)
    source_events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(source_events + existing)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    with pytest.raises(MotifTransformError) as exc:
        transform_repeat(
            source,
            composition=composition,
            destination=_dest(composition, track_id="melody-1", start=960, anchor_midi=anchor),
            id_seed="seed-overlap",
        )
    assert exc.value.code == "motif_overlap_rejected"


def test_deterministic_event_ids():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    destination = _dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor)
    first = transform_repeat(
        source,
        composition=composition,
        destination=destination,
        id_seed="stable-seed",
    )
    second = transform_repeat(
        source,
        composition=composition,
        destination=destination,
        id_seed="stable-seed",
    )
    assert [event.id for event in first.events] == [event.id for event in second.events]
    assert len({event.id for event in first.events}) == 3


def test_canonical_round_trip_extraction():
    events = [
        _note("C4", start=120, duration=240, event_id="src-0"),
        _note("E4", start=360, duration=240, event_id="src-1"),
        _note("G4", start=600, duration=240, event_id="src-2"),
    ]
    composition = _v2_shell(tracks=[_track(events)])
    extracted = extract_relative_motif(composition, track_id="melody-1", event_ids=["src-0", "src-1", "src-2"])
    destination = build_motif_destination(
        composition,
        track_id="melody-1",
        start_tick=1920,
        anchor_midi=extracted.anchor_midi,
    )
    result = transform_repeat(
        extracted.notes,
        composition=composition,
        destination=destination,
        id_seed="seed-roundtrip",
    )
    assert [(event.pitch, event.start_tick, event.duration_ticks) for event in result.events] == [
        ("C4", 1920, 240),
        ("E4", 2160, 240),
        ("G4", 2400, 240),
    ]


def test_answer_counterphrase_validation():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    proposal = AnswerCounterphraseProposal(
        note_count=3,
        pitch_semitone_offsets=[0, -1, -2],
    )
    result = validate_answer_or_counterphrase(
        source,
        proposal,
        operation="answer",
        composition=composition,
        destination=_dest(composition, track_id="melody-1", start=1920, anchor_midi=anchor),
        id_seed="seed-answer",
        variation_strength=0.8,
    )
    assert [event.pitch for event in result.events] == ["C4", "C#4", "D4"]


def test_identity_threshold_scales_with_strength():
    assert identity_threshold("melodic_variation", 0.0) > identity_threshold("melodic_variation", 1.0)


def test_similarity_warn_code_for_failed_creative():
    events = _phrase_ids("src", ["C4", "D4", "E4"], start=0)
    composition = _v2_shell(tracks=[_track(events)])
    source, _anchor = _extract(composition, "melody-1", ["src-0", "src-1", "src-2"])
    mutated = tuple(
        [
            source[0],
            type(source[1])(
                relative_start_tick=960,
                duration_ticks=240,
                pitch_semitone_offset=source[1].pitch_semitone_offset + 12,
                velocity=source[1].velocity,
                staff=source[1].staff,
                voice=source[1].voice,
                articulations=source[1].articulations,
                tie_segment_durations=source[1].tie_segment_durations,
            ),
            source[2],
        ]
    )
    verification = verify_motif_identity(
        source,
        mutated,
        operation="melodic_variation",
        variation_strength=0.0,
    )
    assert not verification.passed
    assert WARN_IDENTITY_BELOW_THRESHOLD in verification.warning_codes


def test_proposal_validation_rejects_mismatched_lengths():
    with pytest.raises(ValidationError):
        RhythmicVariationProposal(
            note_count=3,
            onset_delta_ticks=[0, 480],
            duration_ticks=[480, 480, 480],
        )
