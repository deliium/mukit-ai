"""Acceptance gate: Composition V2 encode/decode preserves core quantized fields."""

from __future__ import annotations

import json
from pathlib import Path

from app.composition_schemas import CompositionV2, midi_pitch_number
from app.tokenizer.decode import decode_tokens
from app.tokenizer.encode import encode_composition
from app.tokenizer.quantize import (
    bin_to_velocity,
    snap_duration_steps,
    snap_tick_to_grid,
    velocity_to_bin,
)
from app.tokenizer.schemas import default_tokenizer_config
from app.tokenizer.vocab import build_vocab


FIXTURES = Path(__file__).parent / "fixtures"
V2_SAMPLE = FIXTURES / "dataset" / "sources" / "v2_sample.json"


def _quantized_note_tuples(doc: CompositionV2, config) -> list[tuple]:
    rows: list[tuple] = []
    for track_index, track in enumerate(doc.tracks):
        for event in track.events:
            start, _ = snap_tick_to_grid(int(event.start_tick), config.grid_ticks)
            dur_steps, _, _ = snap_duration_steps(int(event.duration_ticks), config)
            vel_bin = velocity_to_bin(int(event.velocity), config.velocity_bins)
            rows.append(
                (
                    track_index,
                    midi_pitch_number(event.pitch),
                    start,
                    dur_steps * config.grid_ticks,
                    bin_to_velocity(vel_bin, config.velocity_bins),
                    bool(track.is_drum),
                    int(track.midi_program),
                )
            )
    rows.sort()
    return rows


def test_acceptance_roundtrip_preserves_core_fields() -> None:
    """User acceptance: pitches, quantized timing/durations, tracks, polyphony."""
    config = default_tokenizer_config()
    vocab = build_vocab(config)
    original = CompositionV2.model_validate(json.loads(V2_SAMPLE.read_text(encoding="utf-8")))

    sequence = encode_composition(original, config, vocab=vocab)
    decoded, report = decode_tokens(sequence, config, vocab=vocab)

    assert report.result in {"ok", "repaired"}
    assert sequence.vocab_hash == vocab.vocab_hash
    assert len(decoded.tracks) == len(original.tracks)

    expected = _quantized_note_tuples(original, config)
    actual = _quantized_note_tuples(decoded, config)
    assert actual == expected

    # Polyphony / multi-track: at least one shared onset or ≥2 tracks with notes
    onsets = [row[2] for row in actual]
    track_slots_with_notes = {row[0] for row in actual}
    assert len(actual) >= 1
    assert len(track_slots_with_notes) >= 1
    assert len(onsets) == len(actual)
