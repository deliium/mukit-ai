"""Tests for optional tracks[].note_performances validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.composition_schemas import CompositionV2, degrade_velocity_u16_to_midi7


def _minimal_v2(*, note_performances=None, velocity=100, event_id="n1"):
    track = {
        "id": "t1",
        "name": "Piano",
        "instrument": "acoustic_grand_piano",
        "role": "melody",
        "midi_program": 0,
        "channel": 1,
        "events": [
            {
                "id": event_id,
                "type": "note",
                "pitch": "C4",
                "start_tick": 0,
                "duration_ticks": 480,
                "velocity": velocity,
            }
        ],
    }
    if note_performances is not None:
        track["note_performances"] = note_performances
    return {
        "schema_version": "composition.v2",
        "tempo": 120,
        "time_signature": "4/4",
        "key": "C major",
        "ticks_per_quarter": 480,
        "bar_count": 1,
        "duration_ticks": 1920,
        "sections": [
            {
                "type": "verse",
                "start_bar": 1,
                "bar_count": 1,
                "start_tick": 0,
                "duration_ticks": 1920,
            }
        ],
        "harmony": [],
        "tracks": [track],
    }


def test_degrade_velocity_u16_matches_locked_table():
    assert degrade_velocity_u16_to_midi7(0) == 0
    assert degrade_velocity_u16_to_midi7(512) == 1
    assert degrade_velocity_u16_to_midi7(100 << 9) == 100
    assert degrade_velocity_u16_to_midi7(65024) == 127


def test_absent_and_empty_note_performances_accepted():
    CompositionV2.model_validate(_minimal_v2())
    CompositionV2.model_validate(_minimal_v2(note_performances=[]))


def test_valid_note_performance_with_degrade_match():
    u16 = 100 << 9
    CompositionV2.model_validate(
        _minimal_v2(
            velocity=100,
            note_performances=[
                {
                    "event_id": "n1",
                    "velocity_u16": u16,
                    "pitch_cents": [{"tick_offset": 0, "cents": 50}],
                    "pressure": [{"tick_offset": 120, "value": 0.5}],
                }
            ],
        )
    )


def test_orphan_event_id_rejected():
    with pytest.raises(ValidationError, match="not on track"):
        CompositionV2.model_validate(
            _minimal_v2(
                note_performances=[{"event_id": "missing", "velocity_u16": 512}],
            )
        )


def test_degrade_mismatch_rejected():
    with pytest.raises(ValidationError, match="degrade"):
        CompositionV2.model_validate(
            _minimal_v2(
                velocity=100,
                note_performances=[{"event_id": "n1", "velocity_u16": 64 << 9}],
            )
        )
