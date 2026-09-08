"""Deterministic source fingerprints for composition.analysis.v1.

Hashes a versioned, sorted-key compact JSON projection of analysis-relevant
canonical fields only. Does not import the private migration hash helper.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..analysis_schemas import (
    ANALYSIS_FINGERPRINT_PROFILE,
    ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN,
    fingerprint_log_prefix,
)
from ..composition_schemas import CompositionV2


logger = logging.getLogger(__name__)


def canonical_analysis_json_dumps(value: Any) -> str:
    """Compact JSON with recursively sorted object keys (UTF-8, no spaces)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def analysis_relevant_projection(composition: CompositionV2) -> dict[str, Any]:
    """Project only fields that affect musical analysis identity.

    Excludes markers, volume/pan/expression automation, dynamics, sustain, and
    UI-only state. Includes timeline, sections, track identity/role/drum metadata,
    note pitch/timing/velocity/staff/voice/tie/articulations, declared key timeline,
    and harmony.
    """
    return {
        "fingerprint_profile": ANALYSIS_FINGERPRINT_PROFILE,
        "schema_version": composition.schema_version,
        "tempo": composition.tempo,
        "key": composition.key,
        "time_signature": composition.time_signature,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "duration_ticks": composition.duration_ticks,
        "bar_count": composition.bar_count,
        "tempo_changes": [
            {"tick": change.tick, "bpm": change.bpm} for change in composition.tempo_changes
        ],
        "time_signature_changes": [
            {"tick": change.tick, "time_signature": change.time_signature}
            for change in composition.time_signature_changes
        ],
        "key_changes": [
            {"tick": change.tick, "key": change.key} for change in composition.key_changes
        ],
        "sections": [
            {
                "id": section.id,
                "type": section.type,
                "label": section.label,
                "start_bar": section.start_bar,
                "bar_count": section.bar_count,
                "start_tick": section.start_tick,
                "duration_ticks": section.duration_ticks,
            }
            for section in composition.sections
        ],
        "harmony": [
            {"bar": item.bar, "chord": item.chord} for item in composition.harmony
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
                "staff": track.staff,
                "events": [
                    {
                        "type": event.type,
                        "id": event.id,
                        "pitch": event.pitch,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "velocity": event.velocity,
                        "staff": event.staff,
                        "voice": event.voice,
                        "articulations": list(event.articulations),
                        "tie": (
                            None
                            if event.tie is None
                            else {"group_id": event.tie.group_id, "type": event.tie.type}
                        ),
                    }
                    for event in track.events
                ],
            }
            for track in composition.tracks
        ],
    }


def composition_source_fingerprint(composition: CompositionV2) -> str:
    """Return a stable SHA-256 hex digest of the analysis-relevant projection."""
    projection = analysis_relevant_projection(composition)
    encoded = canonical_analysis_json_dumps(projection)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    note_count = sum(len(track.events) for track in composition.tracks)
    logger.debug(
        "Composition analysis fingerprint computed",
        extra={
            "fingerprint_profile": ANALYSIS_FINGERPRINT_PROFILE,
            "fingerprint_prefix": fingerprint_log_prefix(digest),
            "track_count": len(composition.tracks),
            "section_count": len(composition.sections),
            "note_count": note_count,
            "encoded_bytes": len(encoded),
            "prefix_len": ANALYSIS_FINGERPRINT_LOG_PREFIX_LEN,
        },
    )
    return digest
