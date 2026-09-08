"""Unit tests for import instrument resolution and GM map."""

from __future__ import annotations

from app.services.import_instruments import (
    GM_PROGRAM_BY_NUMBER,
    gm_program_name,
    infer_import_role,
    resolve_import_instrument,
)


def test_gm_level1_map_covers_all_programs():
    assert len(GM_PROGRAM_BY_NUMBER) == 128
    assert gm_program_name(0) == "Acoustic Grand Piano"
    assert gm_program_name(32) == "Acoustic Bass"
    assert gm_program_name(127) == "Gunshot"


def test_channel_10_infers_drums_role():
    role, source = infer_import_role(
        explicit_role=None,
        channel=10,
        is_drum=False,
        instrument="piano",
    )
    assert role == "drums"
    assert source == "channel_percussion"


def test_bass_identity_infers_bass_role():
    resolved = resolve_import_instrument(
        source_program=33,
        instrument_name="Electric Bass (finger)",
        explicit_role=None,
        channel=1,
    )
    assert resolved.role == "bass"
    assert resolved.role_source == "instrument_identity"
    assert resolved.midi_program == 33
    assert resolved.instrument_defaulted is False


def test_insufficient_metadata_defaults_to_other():
    resolved = resolve_import_instrument(
        source_program=0,
        instrument_name="Acoustic Grand Piano",
        explicit_role=None,
        channel=1,
    )
    assert resolved.role == "other"
    assert resolved.role_source == "default"


def test_explicit_role_wins_over_channel():
    role, source = infer_import_role(
        explicit_role="melody",
        channel=10,
        is_drum=True,
        instrument="drums",
    )
    assert role == "melody"
    assert source == "explicit_role"


def test_missing_program_defaults_and_preserves_name_when_possible():
    resolved = resolve_import_instrument(
        source_program=None,
        instrument_name="violin",
        explicit_role=None,
        channel=2,
    )
    assert resolved.midi_program == 0
    assert resolved.instrument_defaulted is True
    assert resolved.instrument == "violin"
    assert resolved.role == "other"
