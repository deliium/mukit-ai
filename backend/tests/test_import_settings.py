"""Focused tests for bounded import environment settings and policy constants."""

from __future__ import annotations

from app.import_settings import (
    IMPORT_DEFAULT_KEY,
    IMPORT_DEFAULT_TEMPO_BPM,
    IMPORT_DEFAULT_TIME_SIGNATURE,
    IMPORT_EXTENSIONS,
    IMPORT_NEUTRAL_SECTION_TYPE,
    IMPORT_NEUTRAL_TRACK_ROLE,
    MIDI_EXTENSIONS,
    MIDI_HEADER_SIGNATURE,
    MUSICXML_EXTENSIONS,
    MXL_EXTENSIONS,
    load_import_settings,
)


def test_load_import_settings_defaults():
    settings = load_import_settings({})
    assert settings.max_upload_bytes == 5 * 1024 * 1024
    assert settings.max_expanded_bytes == 25 * 1024 * 1024
    assert settings.max_compression_ratio == 50.0
    assert settings.max_archive_entries == 64
    assert settings.max_tracks == 64
    assert settings.max_notes == 100_000
    assert settings.max_bars == 512
    assert settings.max_ppq == 1920
    assert settings.max_metadata_changes == 512
    assert settings.max_active_notes == 512
    assert settings.default_target_ppq == 480


def test_load_import_settings_reads_env_overrides():
    settings = load_import_settings(
        {
            "IMPORT_MAX_UPLOAD_BYTES": "2048",
            "IMPORT_MAX_TRACKS": "8",
            "IMPORT_MAX_NOTES": "1000",
            "IMPORT_MAX_BARS": "64",
            "IMPORT_MAX_COMPRESSION_RATIO": "12.5",
            "IMPORT_DEFAULT_TARGET_PPQ": "960",
        }
    )
    assert settings.max_upload_bytes == 2048
    assert settings.max_tracks == 8
    assert settings.max_notes == 1000
    assert settings.max_bars == 64
    assert settings.max_compression_ratio == 12.5
    assert settings.default_target_ppq == 960


def test_invalid_import_settings_fall_back_with_warning(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        settings = load_import_settings(
            {
                "IMPORT_MAX_UPLOAD_BYTES": "not-a-number",
                "IMPORT_MAX_COMPRESSION_RATIO": "bad",
                "IMPORT_MAX_TRACKS": "999999",
            }
        )
    assert settings.max_upload_bytes == 5 * 1024 * 1024
    assert settings.max_compression_ratio == 50.0
    assert settings.max_tracks == 512  # clamped to configured maximum
    assert any("import setting" in record.message.lower() for record in caplog.records)


def test_policy_constants_and_extensions():
    assert IMPORT_DEFAULT_TEMPO_BPM == 120
    assert IMPORT_DEFAULT_TIME_SIGNATURE == "4/4"
    assert IMPORT_DEFAULT_KEY == "C major"
    assert IMPORT_NEUTRAL_TRACK_ROLE == "other"
    assert IMPORT_NEUTRAL_SECTION_TYPE == "unsectioned"
    assert MIDI_HEADER_SIGNATURE == b"MThd"
    assert MIDI_EXTENSIONS == frozenset({".mid", ".midi"})
    assert MUSICXML_EXTENSIONS == frozenset({".musicxml", ".xml"})
    assert MXL_EXTENSIONS == frozenset({".mxl"})
    assert IMPORT_EXTENSIONS == MIDI_EXTENSIONS | MUSICXML_EXTENSIONS | MXL_EXTENSIONS


def test_import_limits_exceed_generation_budget_defaults():
    """Import complexity caps must remain independent of LLM 32-bar / 6-track budgets."""
    settings = load_import_settings({})
    assert settings.max_bars > 32
    assert settings.max_tracks > 6
