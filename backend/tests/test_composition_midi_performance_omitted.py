"""SMF export ignores note_performances and reports performance_expression_omitted."""

from __future__ import annotations

from app.composition_schemas import CompositionV2
from app.services.composition_midi import render_midi_with_report


def test_smf_omits_note_performances_with_issue_code():
    composition = CompositionV2.model_validate(
        {
            "schema_version": "composition.v2",
            "tempo": 120,
            "key": "C major",
            "time_signature": "4/4",
            "ticks_per_quarter": 480,
            "duration_ticks": 1920,
            "bar_count": 1,
            "sections": [
                {
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                }
            ],
            "tracks": [
                {
                    "id": "melody-1",
                    "name": "Melody",
                    "instrument": "acoustic_grand_piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "id": "n1",
                            "type": "note",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 100,
                        }
                    ],
                    "note_performances": [
                        {
                            "event_id": "n1",
                            "velocity_u16": 100 << 9,
                            "pitch_cents": [{"tick_offset": 0, "cents": 25}],
                        }
                    ],
                }
            ],
            "harmony": [],
        }
    )
    result = render_midi_with_report(composition)
    assert result.midi_bytes
    codes = result.report.compact_codes()
    assert "performance_expression_omitted" in codes
