"""Unit tests for tokenizer vocab, quantize, repair, and encode/decode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2, midi_pitch_number
from app.tokenizer.decode import decode_tokens
from app.tokenizer.encode import encode_composition
from app.tokenizer.errors import TokenizerDecodeError, TokenizerVerifyError
from app.tokenizer.manifest import build_manifest, verify_manifest, write_manifest
from app.tokenizer.quantize import (
    bin_to_velocity,
    midi_to_pitch_name,
    snap_duration_steps,
    snap_tick_to_grid,
    velocity_to_bin,
)
from app.tokenizer.schemas import (
    TOKENIZER_VERSION,
    TokenizerConfigV1,
    default_tokenizer_config,
)
from app.tokenizer.validate import repair_token_sequence, validate_token_sequence
from app.tokenizer.versioning import expected_tokenizer_record, verify_expectation
from app.tokenizer.vocab import build_vocab
from app.tokenizer import special_tokens as st


FIXTURES = Path(__file__).parent / "fixtures"
V2_SAMPLE = FIXTURES / "dataset" / "sources" / "v2_sample.json"
V2_EXPRESSIVE = FIXTURES / "composition_v2_expressive.json"


@pytest.fixture
def config() -> TokenizerConfigV1:
    return default_tokenizer_config()


@pytest.fixture
def vocab(config: TokenizerConfigV1):
    return build_vocab(config)


def test_vocab_pad_zero_and_hash_stable(config: TokenizerConfigV1) -> None:
    a = build_vocab(config)
    b = build_vocab(config)
    assert a.token_to_id[st.PAD] == 0
    assert a.vocab_hash == b.vocab_hash
    assert a.size == b.size
    assert a.size < config.max_vocab_size


def test_quantize_half_up_and_velocity_bins(config: TokenizerConfigV1) -> None:
    snapped, did = snap_tick_to_grid(60, config.grid_ticks)
    assert did
    assert snapped in {0, 120}
    steps, _, _ = snap_duration_steps(1, config)
    assert steps == 1
    for vel in (1, 64, 127):
        b = velocity_to_bin(vel, config.velocity_bins)
        restored = bin_to_velocity(b, config.velocity_bins)
        assert 1 <= restored <= 127
    assert midi_to_pitch_name(60) == "C4"
    assert midi_pitch_number(midi_to_pitch_name(61)) == 61


def test_encode_decode_sample_polyphony_multitrack(config: TokenizerConfigV1, vocab) -> None:
    doc = CompositionV2.model_validate(json.loads(V2_SAMPLE.read_text(encoding="utf-8")))
    seq = encode_composition(doc, config, vocab=vocab)
    assert seq.tokenizer_version == TOKENIZER_VERSION
    assert seq.encode_report is not None
    assert seq.encode_report.notes_emitted >= 1
    assert st.BAR in (seq.token_strs or [])
    decoded, report = decode_tokens(seq, config, vocab=vocab)
    assert report.result in {"ok", "repaired"}
    assert len(decoded.tracks) == len(doc.tracks)
    # Polyphony: multiple notes can share a start after snap
    starts = [e.start_tick for t in decoded.tracks for e in t.events]
    assert len(starts) == seq.encode_report.notes_emitted


def test_encode_expressive_omits_articulations(config: TokenizerConfigV1, vocab) -> None:
    if not V2_EXPRESSIVE.exists():
        pytest.skip("expressive fixture missing")
    doc = CompositionV2.model_validate(json.loads(V2_EXPRESSIVE.read_text(encoding="utf-8")))
    seq = encode_composition(doc, config, vocab=vocab)
    assert seq.encode_report is not None
    # Core profile may omit expressive metadata
    decoded, _ = decode_tokens(seq, config, vocab=vocab)
    for track in decoded.tracks:
        for event in track.events:
            assert event.articulations == []
            assert event.tie is None


def test_empty_and_single_note(config: TokenizerConfigV1, vocab) -> None:
    base = CompositionV2.model_validate(json.loads(V2_SAMPLE.read_text(encoding="utf-8")))
    # Single note
    track = base.tracks[0].model_copy(
        update={"events": [base.tracks[0].events[0]], "id": "track_0", "name": "Track 0"}
    )
    other = base.tracks[1].model_copy(update={"events": [], "id": "track_1", "name": "Track 1"})
    single = base.model_copy(update={"tracks": [track, other]})
    seq = encode_composition(single, config, vocab=vocab)
    decoded, _ = decode_tokens(seq, config, vocab=vocab)
    assert sum(len(t.events) for t in decoded.tracks) == 1

    # Empty events still valid composition after decode headers
    empty_tracks = [
        t.model_copy(update={"events": [], "id": f"track_{i}", "name": f"Track {i}"})
        for i, t in enumerate(base.tracks)
    ]
    empty = base.model_copy(update={"tracks": empty_tracks})
    seq2 = encode_composition(empty, config, vocab=vocab)
    decoded2, _ = decode_tokens(seq2, config, vocab=vocab)
    assert sum(len(t.events) for t in decoded2.tracks) == 0


def test_dense_chord_polyphony(config: TokenizerConfigV1, vocab) -> None:
    base = CompositionV2.model_validate(json.loads(V2_SAMPLE.read_text(encoding="utf-8")))
    pitches = ["C4", "E4", "G4", "B4"]
    events = [
        base.tracks[0].events[0].model_copy(
            update={"pitch": p, "start_tick": 0, "duration_ticks": 480, "id": f"c{i}"}
        )
        for i, p in enumerate(pitches)
    ]
    track = base.tracks[0].model_copy(update={"events": events, "id": "track_0", "name": "Track 0"})
    other = base.tracks[1].model_copy(update={"events": [], "id": "track_1", "name": "Track 1"})
    chord = base.model_copy(update={"tracks": [track, other]})
    seq = encode_composition(chord, config, vocab=vocab)
    decoded, _ = decode_tokens(seq, config, vocab=vocab)
    notes = [e for t in decoded.tracks for e in t.events]
    assert len(notes) == 4
    assert len({e.start_tick for e in notes}) == 1


def test_repair_orphan_velocity_and_pad(config: TokenizerConfigV1, vocab) -> None:
    ids = [
        vocab.id(st.PAD),
        vocab.id(st.BOS),
        vocab.id(f"{st.VEL_PREFIX}3"),
        vocab.id(st.BAR),
        vocab.id(f"{st.PITCH_PREFIX}60"),
        vocab.id(f"{st.VEL_PREFIX}3"),
        vocab.id(f"{st.DUR_PREFIX}2"),
        vocab.id(st.EOS),
        vocab.id(st.PAD),
    ]
    repaired = repair_token_sequence(ids, config, vocab)
    assert st.PAD not in repaired.token_strs
    assert "orphan_velocity" in repaired.issue_codes or repaired.result in {"ok", "repaired"}
    assert repaired.token_strs[0] == st.BOS
    assert repaired.token_strs[-1] == st.EOS


def test_reject_missing_eos(config: TokenizerConfigV1, vocab) -> None:
    cfg = config.model_copy(update={"on_invalid": "reject", "require_bos_eos": True})
    ids = [vocab.id(st.BOS), vocab.id(st.BAR)]
    validation = validate_token_sequence(ids, cfg, vocab)
    assert "missing_eos" in validation.issue_codes
    with pytest.raises(TokenizerDecodeError):
        decode_tokens(ids, cfg, vocab=vocab)


def test_manifest_verify_and_version_mismatch(config: TokenizerConfigV1, vocab, tmp_path: Path) -> None:
    manifest = build_manifest(config, vocab)
    path = tmp_path / "tokenizer.manifest.json"
    write_manifest(path, manifest)
    verify_manifest(path, config, vocab)

    expectation = expected_tokenizer_record(config, vocab)
    verify_expectation(expectation, config, vocab)

    bad = expectation.model_copy(update={"expected_tokenizer_version": "tokenizer.v999"})
    with pytest.raises(TokenizerVerifyError):
        verify_expectation(bad, config, vocab, require_version=True)

    bad_hash = expectation.model_copy(update={"vocab_hash": "0" * 64})
    with pytest.raises(TokenizerVerifyError):
        verify_expectation(bad_hash, config, vocab)

    # Soft verify warns but continues; --require-version hard-fails on version drift.
    drifted = manifest.model_copy(update={"tokenizer_version": "tokenizer.v999"})
    drifted_path = tmp_path / "tokenizer.manifest.drift.json"
    write_manifest(drifted_path, drifted)
    verify_manifest(drifted_path, config, vocab, require_version=False)
    with pytest.raises(TokenizerVerifyError):
        verify_manifest(drifted_path, config, vocab, require_version=True)


def test_tempo_and_meter_changes_at_bar_boundaries(config: TokenizerConfigV1, vocab) -> None:
    """Encode/decode preserves bar-boundary tempo and meter changes."""
    from app.composition_schemas import (
        CompositionV2Section,
        CompositionV2TempoChange,
        CompositionV2TimeSignatureChange,
    )

    base = CompositionV2.model_validate(json.loads(V2_SAMPLE.read_text(encoding="utf-8")))
    # bars 1–2 remain 4/4 (3840 ticks); bars 3–4 become 3/4 (2880 ticks) → duration 6720
    duration_ticks = 3840 + 2880
    track0 = base.tracks[0].model_copy(
        update={
            "id": "track_0",
            "name": "Track 0",
            "events": [
                e.model_copy(update={"start_tick": min(e.start_tick, duration_ticks - 120)})
                for e in base.tracks[0].events
                if e.start_tick < duration_ticks
            ],
        }
    )
    track1 = base.tracks[1].model_copy(
        update={
            "id": "track_1",
            "name": "Track 1",
            "events": [
                e
                for e in base.tracks[1].events
                if e.start_tick < duration_ticks
            ],
        }
    )
    sections = [
        CompositionV2Section(
            id="section-1",
            type="intro",
            start_bar=1,
            bar_count=2,
            start_tick=0,
            duration_ticks=3840,
        ),
        CompositionV2Section(
            id="section-2",
            type="outro",
            start_bar=3,
            bar_count=2,
            start_tick=3840,
            duration_ticks=2880,
        ),
    ]
    changed = base.model_copy(
        update={
            "duration_ticks": duration_ticks,
            "bar_count": 4,
            "sections": sections,
            "tracks": [track0, track1],
            "tempo_changes": [CompositionV2TempoChange(tick=3840, bpm=140)],
            "time_signature_changes": [
                CompositionV2TimeSignatureChange(tick=3840, time_signature="3/4")
            ],
        }
    )

    seq = encode_composition(changed, config, vocab=vocab)
    assert any(t == "TEMPO_140" for t in (seq.token_strs or []))
    assert any(t.startswith("METER_3_4") for t in (seq.token_strs or []))

    decoded, report = decode_tokens(seq, config, vocab=vocab)
    assert report.result in {"ok", "repaired"}
    assert decoded.tempo == 100
    assert [(c.tick, c.bpm) for c in decoded.tempo_changes] == [(3840, 140)]
    assert decoded.time_signature == "4/4"
    assert [(c.tick, c.time_signature) for c in decoded.time_signature_changes] == [
        (3840, "3/4")
    ]
    assert decoded.duration_ticks == duration_ticks
    assert decoded.bar_count == 4

