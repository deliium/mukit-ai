"""Bounded MIDI/MusicXML import limits and conversion policy constants.

Limits are independent from LLM generation budgets (32 bars / 6 instruments).
Runtime verbosity remains controlled by ``LOG_LEVEL``.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping


logger = logging.getLogger(__name__)

# --- Compatibility defaults (reported when source metadata is absent) ---
IMPORT_DEFAULT_TEMPO_BPM = 120
IMPORT_DEFAULT_TIME_SIGNATURE = "4/4"
IMPORT_DEFAULT_KEY = "C major"
IMPORT_NEUTRAL_TRACK_ROLE = "other"
IMPORT_NEUTRAL_SECTION_TYPE = "unsectioned"

# --- Format / signature policy (content detection; filenames are not authoritative) ---
MIDI_EXTENSIONS = frozenset({".mid", ".midi"})
MUSICXML_EXTENSIONS = frozenset({".musicxml", ".xml"})
MXL_EXTENSIONS = frozenset({".mxl"})
IMPORT_EXTENSIONS = MIDI_EXTENSIONS | MUSICXML_EXTENSIONS | MXL_EXTENSIONS

MIDI_HEADER_SIGNATURE = b"MThd"
ZIP_LOCAL_FILE_SIGNATURE = b"PK\x03\x04"
ZIP_EMPTY_ARCHIVE_SIGNATURE = b"PK\x05\x06"
XML_PROLOG_PREFIXES = (b"<?xml", b"<score-partwise", b"<score-timewise", b"<museScore")

# --- Timing / PPQ policy ---
IMPORT_DEFAULT_TARGET_PPQ = 480
IMPORT_PPQ_ROUND_HALF_UP = True  # shared half-value rounding with projection golden vectors

# Documented env defaults (also wired through .env.example / docker-compose).
_DEFAULT_MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_DEFAULT_MAX_EXPANDED_BYTES = 25 * 1024 * 1024
_DEFAULT_MAX_COMPRESSION_RATIO = 50.0
_DEFAULT_MAX_ARCHIVE_ENTRIES = 64
_DEFAULT_MAX_TRACKS = 64
_DEFAULT_MAX_NOTES = 100_000
_DEFAULT_MAX_BARS = 512
_DEFAULT_MAX_PPQ = 1920
_DEFAULT_MAX_METADATA_CHANGES = 512
_DEFAULT_MAX_ACTIVE_NOTES = 512


@dataclass(frozen=True)
class ImportSettings:
    max_upload_bytes: int
    max_expanded_bytes: int
    max_compression_ratio: float
    max_archive_entries: int
    max_tracks: int
    max_notes: int
    max_bars: int
    max_ppq: int
    max_metadata_changes: int
    max_active_notes: int
    default_target_ppq: int = IMPORT_DEFAULT_TARGET_PPQ


def load_import_settings(env: Mapping[str, str] | None = None) -> ImportSettings:
    source = env if env is not None else os.environ
    settings = ImportSettings(
        max_upload_bytes=_int_env(
            source,
            "IMPORT_MAX_UPLOAD_BYTES",
            _DEFAULT_MAX_UPLOAD_BYTES,
            minimum=1024,
            maximum=100 * 1024 * 1024,
        ),
        max_expanded_bytes=_int_env(
            source,
            "IMPORT_MAX_EXPANDED_BYTES",
            _DEFAULT_MAX_EXPANDED_BYTES,
            minimum=1024,
            maximum=200 * 1024 * 1024,
        ),
        max_compression_ratio=_float_env(
            source,
            "IMPORT_MAX_COMPRESSION_RATIO",
            _DEFAULT_MAX_COMPRESSION_RATIO,
            minimum=1.0,
            maximum=1000.0,
        ),
        max_archive_entries=_int_env(
            source,
            "IMPORT_MAX_ARCHIVE_ENTRIES",
            _DEFAULT_MAX_ARCHIVE_ENTRIES,
            minimum=1,
            maximum=10_000,
        ),
        max_tracks=_int_env(
            source,
            "IMPORT_MAX_TRACKS",
            _DEFAULT_MAX_TRACKS,
            minimum=1,
            maximum=512,
        ),
        max_notes=_int_env(
            source,
            "IMPORT_MAX_NOTES",
            _DEFAULT_MAX_NOTES,
            minimum=1,
            maximum=5_000_000,
        ),
        max_bars=_int_env(
            source,
            "IMPORT_MAX_BARS",
            _DEFAULT_MAX_BARS,
            minimum=1,
            maximum=10_000,
        ),
        max_ppq=_int_env(
            source,
            "IMPORT_MAX_PPQ",
            _DEFAULT_MAX_PPQ,
            minimum=24,
            maximum=9600,
        ),
        max_metadata_changes=_int_env(
            source,
            "IMPORT_MAX_METADATA_CHANGES",
            _DEFAULT_MAX_METADATA_CHANGES,
            minimum=0,
            maximum=100_000,
        ),
        max_active_notes=_int_env(
            source,
            "IMPORT_MAX_ACTIVE_NOTES",
            _DEFAULT_MAX_ACTIVE_NOTES,
            minimum=1,
            maximum=10_000,
        ),
        default_target_ppq=_int_env(
            source,
            "IMPORT_DEFAULT_TARGET_PPQ",
            IMPORT_DEFAULT_TARGET_PPQ,
            minimum=24,
            maximum=9600,
        ),
    )
    logger.debug(
        "Import settings loaded",
        extra={
            "max_upload_bytes": settings.max_upload_bytes,
            "max_expanded_bytes": settings.max_expanded_bytes,
            "max_compression_ratio": settings.max_compression_ratio,
            "max_archive_entries": settings.max_archive_entries,
            "max_tracks": settings.max_tracks,
            "max_notes": settings.max_notes,
            "max_bars": settings.max_bars,
            "max_ppq": settings.max_ppq,
            "max_metadata_changes": settings.max_metadata_changes,
            "max_active_notes": settings.max_active_notes,
            "default_target_ppq": settings.default_target_ppq,
        },
    )
    logger.info(
        "Import settings ready",
        extra={
            "max_upload_bytes": settings.max_upload_bytes,
            "max_tracks": settings.max_tracks,
            "max_notes": settings.max_notes,
            "max_bars": settings.max_bars,
            "max_ppq": settings.max_ppq,
        },
    )
    return settings


def _int_env(
    env: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "Invalid integer import setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Import setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value


def _float_env(
    env: Mapping[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw_value = env.get(name)
    if raw_value is None or raw_value.strip() == "":
        return default
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning(
            "Invalid float import setting; using default",
            extra={"setting_name": name, "fallback": default},
        )
        return default
    if value < minimum or value > maximum:
        clamped = min(max(value, minimum), maximum)
        logger.warning(
            "Import setting out of range; clamped",
            extra={
                "setting_name": name,
                "fallback": clamped,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        return clamped
    return value
