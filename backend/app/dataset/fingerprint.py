"""Dataset-specific fingerprint profile (notes / timing / instruments).

Distinct from analysis (``composition.analysis``) and edit (``composition.edit.v1``)
profiles — do not overload those identities.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.composition_schemas import CompositionV2
from app.dataset.settings import DATASET_FINGERPRINT_PROFILE


logger = logging.getLogger(__name__)

FINGERPRINT_LOG_PREFIX_LEN = 12


def fingerprint_log_prefix(digest: str) -> str:
    return digest[:FINGERPRINT_LOG_PREFIX_LEN]


def canonical_dataset_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def dataset_fingerprint_projection(composition: CompositionV2) -> dict[str, Any]:
    """Project notes, timing, and instrument identity for corpus dedup."""
    return {
        "fingerprint_profile": DATASET_FINGERPRINT_PROFILE,
        "schema_version": composition.schema_version,
        "tempo": composition.tempo,
        "key": composition.key,
        "time_signature": composition.time_signature,
        "ticks_per_quarter": composition.ticks_per_quarter,
        "duration_ticks": composition.duration_ticks,
        "bar_count": composition.bar_count,
        "tempo_changes": [
            {"tick": c.tick, "bpm": c.bpm} for c in composition.tempo_changes
        ],
        "time_signature_changes": [
            {"tick": c.tick, "time_signature": c.time_signature}
            for c in composition.time_signature_changes
        ],
        "tracks": [
            {
                "id": track.id,
                "name": track.name,
                "role": track.role,
                "program": track.midi_program,
                "channel": track.channel,
                "is_drum": track.is_drum,
                "events": [
                    {
                        "pitch": event.pitch,
                        "start_tick": event.start_tick,
                        "duration_ticks": event.duration_ticks,
                        "velocity": event.velocity,
                    }
                    for event in track.events
                ],
            }
            for track in composition.tracks
        ],
    }


def dataset_content_hash(composition: CompositionV2) -> str:
    projection = dataset_fingerprint_projection(composition)
    encoded = canonical_dataset_json_dumps(projection)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    logger.debug(
        "Dataset fingerprint computed",
        extra={
            "fingerprint_profile": DATASET_FINGERPRINT_PROFILE,
            "fingerprint_prefix": fingerprint_log_prefix(digest),
            "track_count": len(composition.tracks),
            "note_count": sum(len(t.events) for t in composition.tracks),
        },
    )
    return digest
