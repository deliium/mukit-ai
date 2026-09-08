import logging
import re
from typing import Any

from pydantic import ValidationError

from app.composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V1,
    COMPOSITION_SCHEMA_VERSION_V2,
    CompositionV1,
    CompositionV2,
    UnsupportedSchemaVersionError,
)
from app.schemas import LLMMusicJson
from app.services.composition_migration import CompositionMigrationError, migrate_v1_to_v2
from app.services.composition_timing import (
    derive_section_boundaries,
    legacy_bar_beat_to_start_tick,
    quarter_units_to_ticks,
)


logger = logging.getLogger(__name__)

COMPOSITION_SCHEMA_VERSION = COMPOSITION_SCHEMA_VERSION_V2
DEFAULT_TICKS_PER_QUARTER = 480
DEFAULT_LEGACY_VELOCITY = 80

INSTRUMENT_PROGRAMS = {
    "piano": 0,
    "keyboard": 0,
    "organ": 19,
    "guitar": 24,
    "bass": 32,
    "violin": 40,
    "viola": 41,
    "cello": 42,
    "harp": 46,
    "strings": 48,
    "choir": 52,
    "trumpet": 56,
    "trombone": 57,
    "horn": 60,
    "sax": 64,
    "oboe": 68,
    "clarinet": 71,
    "flute": 73,
    "synth": 80,
    "pad": 88,
    "drums": 0,
    "percussion": 0,
}


class CompositionNormalizationError(ValueError):
    pass


def normalize_composition_json(raw: Any) -> CompositionV2:
    """Exact version dispatch that always returns operational CompositionV2."""
    logger.debug(
        "Starting composition normalization",
        extra={"raw_type": type(raw).__name__, "schema_version": _schema_version(raw)},
    )
    if isinstance(raw, CompositionV2):
        logger.info(
            "Composition normalization completed",
            extra={
                "normalization_path": "canonical",
                "source_schema_version": COMPOSITION_SCHEMA_VERSION_V2,
                "schema_version": raw.schema_version,
            },
        )
        return raw

    if isinstance(raw, CompositionV1):
        migrated = migrate_v1_to_v2(raw)
        logger.info(
            "Composition normalization completed",
            extra={
                "normalization_path": "v1_to_v2",
                "source_schema_version": COMPOSITION_SCHEMA_VERSION_V1,
                "schema_version": migrated.composition.schema_version,
            },
        )
        return migrated.composition

    raw_data = raw.model_dump() if hasattr(raw, "model_dump") else raw
    if not isinstance(raw_data, dict):
        logger.error(
            "Composition normalization failed",
            extra={"normalization_path": "rejected", "reason": "raw input is not an object"},
        )
        raise CompositionNormalizationError("Music JSON must be an object")

    schema_version = raw_data.get("schema_version")
    if schema_version == COMPOSITION_SCHEMA_VERSION_V2:
        try:
            composition = CompositionV2.model_validate(raw_data)
        except ValidationError as exc:
            logger.error(
                "Canonical composition.v2 validation failed during normalization",
                extra={"normalization_path": "rejected", "error_count": len(exc.errors())},
            )
            raise
        logger.info(
            "Composition normalization completed",
            extra={
                "normalization_path": "canonical",
                "source_schema_version": COMPOSITION_SCHEMA_VERSION_V2,
                "schema_version": composition.schema_version,
                "track_count": len(composition.tracks),
                "event_count": sum(len(track.events) for track in composition.tracks),
            },
        )
        return composition

    if schema_version == COMPOSITION_SCHEMA_VERSION_V1:
        try:
            migrated = migrate_v1_to_v2(raw_data)
        except (ValidationError, CompositionMigrationError) as exc:
            logger.error(
                "Composition.v1 migration failed during normalization",
                extra={
                    "normalization_path": "v1_to_v2",
                    "error_type": type(exc).__name__,
                    "code": getattr(exc, "code", None),
                },
            )
            raise
        logger.info(
            "Composition normalization completed",
            extra={
                "normalization_path": "v1_to_v2",
                "source_schema_version": COMPOSITION_SCHEMA_VERSION_V1,
                "schema_version": migrated.composition.schema_version,
                "ignored_field_count": len(migrated.ignored_field_paths),
                "track_count": len(migrated.composition.tracks),
                "event_count": sum(len(track.events) for track in migrated.composition.tracks),
            },
        )
        return migrated.composition

    if schema_version is not None:
        logger.error(
            "Unsupported composition schema_version",
            extra={
                "normalization_path": "rejected",
                "schema_version": str(schema_version),
                "code": UnsupportedSchemaVersionError.code,
            },
        )
        raise UnsupportedSchemaVersionError(str(schema_version))

    # Unversioned legacy → V1 → V2
    v1 = _migrate_legacy_music_json(raw_data)
    migrated = migrate_v1_to_v2(v1)
    logger.info(
        "Composition normalization completed",
        extra={
            "normalization_path": "legacy_to_v2",
            "source_schema_version": None,
            "schema_version": migrated.composition.schema_version,
            "track_count": len(migrated.composition.tracks),
            "event_count": sum(len(track.events) for track in migrated.composition.tracks),
        },
    )
    return migrated.composition


def _migrate_legacy_music_json(raw_data: dict[str, Any]) -> CompositionV1:
    logger.debug(
        "Migrating legacy music JSON to composition.v1",
        extra={"raw_keys": sorted(raw_data.keys()), "note_count": len(raw_data.get("notes") or [])},
    )
    if not raw_data.get("notes"):
        logger.info(
            "Composition normalization rejected legacy harmony-only JSON",
            extra={"normalization_path": "legacy_rejected", "reason": "missing explicit notes"},
        )
        raise CompositionNormalizationError(
            "Legacy music JSON has harmony/tracks but no note events; regenerate or add notes before canonical rendering/playback."
        )

    try:
        legacy = LLMMusicJson.model_validate(raw_data)
    except ValidationError as exc:
        logger.error(
            "Legacy music JSON validation failed during normalization",
            extra={"normalization_path": "legacy_rejected", "error_count": len(exc.errors())},
        )
        raise

    ticks_per_quarter = int(raw_data.get("ticks_per_quarter") or DEFAULT_TICKS_PER_QUARTER)
    sections = derive_section_boundaries(legacy.sections, legacy.time_signature, ticks_per_quarter)
    bar_count = sum(int(section["bar_count"]) for section in sections)
    duration_ticks = sum(int(section["duration_ticks"]) for section in sections)

    events_by_track: dict[int, list[dict[str, Any]]] = {index: [] for index in range(1, len(legacy.tracks) + 1)}
    raw_notes = raw_data.get("notes") or []
    for index, note in enumerate(legacy.notes):
        raw_note = raw_notes[index] if index < len(raw_notes) and isinstance(raw_notes[index], dict) else {}
        velocity = raw_note.get("velocity", DEFAULT_LEGACY_VELOCITY)
        if "velocity" not in raw_note:
            logger.warning(
                "Defaulting missing legacy note velocity during composition migration",
                extra={"normalization_path": "legacy_migrated", "note_index": index, "velocity": velocity},
            )

        start_tick = legacy_bar_beat_to_start_tick(
            note.bar,
            note.beat,
            legacy.time_signature,
            ticks_per_quarter,
        )
        duration_note_ticks = quarter_units_to_ticks(note.duration, ticks_per_quarter)
        events_by_track[note.track].append(
            {
                "type": "note",
                "pitch": note.pitch,
                "start_tick": start_tick,
                "duration_ticks": duration_note_ticks,
                "velocity": velocity,
                "staff": note.staff,
            }
        )

    canonical_tracks = []
    for index, track in enumerate(legacy.tracks, start=1):
        instrument = track.instrument
        canonical_tracks.append(
            {
                "id": _track_id(instrument, index),
                "name": instrument,
                "instrument": instrument,
                "role": track.role,
                "midi_program": _midi_program_for_instrument(instrument),
                "channel": 10 if track.role in {"drums", "percussion"} else _melodic_channel(index),
                "is_drum": track.role in {"drums", "percussion"},
                "volume": 100,
                "pan": 0,
                "events": events_by_track[index],
            }
        )

    composition = CompositionV1.model_validate(
        {
            "schema_version": COMPOSITION_SCHEMA_VERSION_V1,
            "tempo": legacy.tempo,
            "key": legacy.key,
            "time_signature": legacy.time_signature,
            "ticks_per_quarter": ticks_per_quarter,
            "duration_ticks": duration_ticks,
            "bar_count": bar_count,
            "sections": sections,
            "tracks": canonical_tracks,
            "harmony": [item.model_dump() for item in legacy.harmony],
        }
    )
    logger.info(
        "Legacy music JSON migrated to composition.v1",
        extra={
            "normalization_path": "legacy_migrated",
            "schema_version": composition.schema_version,
            "bar_count": composition.bar_count,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
            "duration_ticks": composition.duration_ticks,
        },
    )
    return composition


def _schema_version(raw: Any) -> str | None:
    if isinstance(raw, (CompositionV1, CompositionV2)):
        return raw.schema_version
    if isinstance(raw, dict):
        value = raw.get("schema_version")
        return str(value) if value is not None else None
    return None


def _track_id(instrument: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", instrument.lower()).strip("-") or "track"
    return f"{slug}-{index}"


def _midi_program_for_instrument(instrument: str) -> int:
    normalized = instrument.strip().lower()
    for token, program in INSTRUMENT_PROGRAMS.items():
        if token in normalized:
            return program
    return 0


def _melodic_channel(track_index: int) -> int:
    channel = ((track_index - 1) % 15) + 1
    return channel + 1 if channel >= 10 else channel
