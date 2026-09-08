"""Pure composition.v1 → composition.v2 migration helpers."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from app.composition_schemas import (
    COMPOSITION_SCHEMA_VERSION_V1,
    COMPOSITION_SCHEMA_VERSION_V2,
    CompositionV1,
    CompositionV2,
    collect_ignored_v1_paths,
)


logger = logging.getLogger(__name__)


class CompositionMigrationError(ValueError):
    """Raised when V1→V2 migration fails the fidelity equality gate."""

    code = "v1_v2_migration_fidelity_failed"

    def __init__(self, message: str = "V1 to V2 migration fidelity check failed"):
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class MigrationResult:
    composition: CompositionV2
    source_schema_version: str
    target_schema_version: str = COMPOSITION_SCHEMA_VERSION_V2
    ignored_field_paths: tuple[str, ...] = ()
    section_ids_added: int = 0
    default_field_counts: dict[str, int] | None = None


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _project_v2_onto_v1_fields(composition: CompositionV2) -> dict[str, Any]:
    """Project migrated V2 back onto the V1 field set for ordered equality checks."""
    return {
        "schema_version": COMPOSITION_SCHEMA_VERSION_V1,
        "tempo": composition.tempo,
        "key": composition.key,
        "time_signature": composition.time_signature,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "duration_ticks": composition.duration_ticks,
        "bar_count": composition.bar_count,
        "sections": [
            {
                "type": section.type,
                "start_bar": section.start_bar,
                "bar_count": section.bar_count,
                "start_tick": section.start_tick,
                "duration_ticks": section.duration_ticks,
            }
            for section in composition.sections
        ],
        "tracks": [
            {
                "id": track.id,
                "name": track.name,
                "instrument": track.instrument,
                "role": track.role,
                "midi_program": track.midi_program,
                "channel": track.channel,
                "is_drum": track.is_drum,
                "volume": track.volume,
                "pan": track.pan,
                "staff": track.staff,
                "events": [
                    {
                        "type": event.type,
                        "pitch": event.pitch,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "velocity": event.velocity,
                        "id": event.id,
                        "staff": event.staff,
                        "voice": event.voice,
                    }
                    for event in track.events
                ],
            }
            for track in composition.tracks
        ],
        "harmony": [item.model_dump(mode="json") for item in composition.harmony],
    }


def _v1_equality_payload(composition: CompositionV1) -> dict[str, Any]:
    return {
        "schema_version": COMPOSITION_SCHEMA_VERSION_V1,
        "tempo": composition.tempo,
        "key": composition.key,
        "time_signature": composition.time_signature,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "duration_ticks": composition.duration_ticks,
        "bar_count": composition.bar_count,
        "sections": [
            {
                "type": section.type,
                "start_bar": section.start_bar,
                "bar_count": section.bar_count,
                "start_tick": section.start_tick,
                "duration_ticks": section.duration_ticks,
            }
            for section in composition.sections
        ],
        "tracks": [
            {
                "id": track.id,
                "name": track.name,
                "instrument": track.instrument,
                "role": track.role,
                "midi_program": track.midi_program,
                "channel": track.channel,
                "is_drum": track.is_drum,
                "volume": track.volume,
                "pan": track.pan,
                "staff": track.staff,
                "events": [
                    {
                        "type": event.type,
                        "pitch": event.pitch,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "velocity": event.velocity,
                        "id": event.id,
                        "staff": event.staff,
                        "voice": event.voice,
                    }
                    for event in track.events
                ],
            }
            for track in composition.tracks
        ],
        "harmony": [item.model_dump(mode="json") for item in composition.harmony],
    }


def migrate_v1_to_v2(
    source: CompositionV1 | dict[str, Any],
    *,
    enforce_fidelity: bool = True,
) -> MigrationResult:
    """Non-mutating V1→V2 migration. Source JSON/models are never modified in place."""
    if isinstance(source, CompositionV1):
        v1 = source
        raw_for_ignored: dict[str, Any] = source.model_dump(mode="json")
    else:
        raw_for_ignored = dict(source)
        v1 = CompositionV1.model_validate(source)

    ignored = tuple(collect_ignored_v1_paths(raw_for_ignored))
    logger.debug(
        "Starting V1 to V2 migration",
        extra={
            "source_schema_version": COMPOSITION_SCHEMA_VERSION_V1,
            "ignored_field_count": len(ignored),
            "track_count": len(v1.tracks),
            "section_count": len(v1.sections),
        },
    )

    section_ids_added = 0
    sections: list[dict[str, Any]] = []
    for index, section in enumerate(v1.sections, start=1):
        payload = section.model_dump(mode="json")
        payload["id"] = f"section-{index}"
        payload["label"] = None
        section_ids_added += 1
        sections.append(payload)

    tracks: list[dict[str, Any]] = []
    for track in v1.tracks:
        track_payload = track.model_dump(mode="json")
        track_payload["expression"] = 127
        track_payload["dynamic_marks"] = []
        track_payload["sustain_pedals"] = []
        track_payload["automation"] = []
        events = []
        for event in track.events:
            event_payload = event.model_dump(mode="json")
            event_payload["articulations"] = []
            event_payload["tie"] = None
            events.append(event_payload)
        track_payload["events"] = events
        tracks.append(track_payload)

    v2_payload = {
        "schema_version": COMPOSITION_SCHEMA_VERSION_V2,
        "tempo": v1.tempo,
        "key": v1.key,
        "time_signature": v1.time_signature,
        "ticks_per_quarter": v1.ticks_per_quarter,
        "duration_ticks": v1.duration_ticks,
        "bar_count": v1.bar_count,
        "sections": sections,
        "tracks": tracks,
        "harmony": [item.model_dump(mode="json") for item in v1.harmony],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }

    try:
        v2 = CompositionV2.model_validate(v2_payload)
    except ValidationError as exc:
        logger.error(
            "V1 to V2 migration produced invalid CompositionV2",
            extra={"error_count": len(exc.errors()), "code": CompositionMigrationError.code},
        )
        raise CompositionMigrationError("Migrated CompositionV2 failed validation") from exc

    if enforce_fidelity:
        expected = _v1_equality_payload(v1)
        actual = _project_v2_onto_v1_fields(v2)
        if expected != actual:
            logger.error(
                "V1 to V2 migration fidelity gate failed",
                extra={
                    "code": CompositionMigrationError.code,
                    "source_hash": _stable_hash(expected),
                    "projected_hash": _stable_hash(actual),
                },
            )
            raise CompositionMigrationError()

    defaults = {
        "tempo_changes": 0,
        "time_signature_changes": 0,
        "key_changes": 0,
        "markers": 0,
        "section_ids_added": section_ids_added,
        "track_expression_defaults": len(tracks),
        "empty_articulation_notes": sum(len(track["events"]) for track in tracks),
    }
    logger.info(
        "V1 to V2 migration completed",
        extra={
            "source_schema_version": COMPOSITION_SCHEMA_VERSION_V1,
            "target_schema_version": COMPOSITION_SCHEMA_VERSION_V2,
            "ignored_field_count": len(ignored),
            "section_ids_added": section_ids_added,
            "track_count": len(v2.tracks),
            "event_count": sum(len(track.events) for track in v2.tracks),
            "source_hash": _stable_hash(_v1_equality_payload(v1)),
            "target_hash": _stable_hash(v2.model_dump(mode="json")),
        },
    )
    if ignored:
        logger.warning(
            "Ignored unsupported V1 extra fields during migration",
            extra={"ignored_field_count": len(ignored), "ignored_field_paths": list(ignored)[:20]},
        )

    return MigrationResult(
        composition=v2,
        source_schema_version=COMPOSITION_SCHEMA_VERSION_V1,
        ignored_field_paths=ignored,
        section_ids_added=section_ids_added,
        default_field_counts=defaults,
    )
