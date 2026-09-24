"""Build secret-safe ``audio.roundtrip.provenance.v1`` fragments.

Never embeds PCM, prompts, full overlays, or event arrays.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.audio_alignment_schemas import (
    AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION,
    AudioRoundtripProvenanceV1,
)
from app.services.persistence_secret_guard import assert_no_secret_fields


logger = logging.getLogger(__name__)


def build_roundtrip_provenance(
    *,
    source_audio_asset_id: str | None = None,
    source_sha256_prefix: str | None = None,
    result_asset_id: str | None = None,
    alignment_asset_id: str | None = None,
    recovery_job_id: str | None = None,
    composition_fingerprint: str | None = None,
    revision_id: str | None = None,
    neural_render_id: str | None = None,
    bound_at: str | None = None,
    rendered_at: str | None = None,
) -> AudioRoundtripProvenanceV1:
    """Assemble and secret-guard a round-trip provenance fragment."""
    frag = AudioRoundtripProvenanceV1(
        schema_version=AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION,
        source_audio_asset_id=source_audio_asset_id,
        source_sha256_prefix=source_sha256_prefix,
        result_asset_id=result_asset_id,
        alignment_asset_id=alignment_asset_id,
        recovery_job_id=recovery_job_id,
        composition_fingerprint=composition_fingerprint,
        revision_id=revision_id,
        neural_render_id=neural_render_id,
        bound_at=bound_at,
        rendered_at=rendered_at,
        alignment_schema_version="audio.alignment.v1" if alignment_asset_id else None,
        recovery_result_schema_version="audio.recovery.result.v1" if result_asset_id else None,
    )
    payload = frag.model_dump(mode="json", exclude_none=True)
    assert_no_secret_fields(payload, context="audio_roundtrip_provenance_v1")
    logger.info(
        "Round-trip provenance attached",
        extra={
            "has_source": bool(source_audio_asset_id),
            "has_alignment": bool(alignment_asset_id),
            "has_render": bool(neural_render_id),
            "fp_prefix": (composition_fingerprint or "")[:12] or None,
            "stage_keys": sorted(payload.keys()),
        },
    )
    return frag


def merge_render_into_provenance(
    existing: Mapping[str, Any] | AudioRoundtripProvenanceV1 | None,
    *,
    neural_render_id: str,
    composition_fingerprint: str | None = None,
    rendered_at: str | None = None,
) -> AudioRoundtripProvenanceV1:
    """Extend an existing fragment with a neural render id."""
    base: dict[str, Any] = {}
    if isinstance(existing, AudioRoundtripProvenanceV1):
        base = existing.model_dump(mode="json", exclude_none=True)
    elif isinstance(existing, Mapping):
        base = dict(existing)
    base["neural_render_id"] = neural_render_id
    if composition_fingerprint:
        base["composition_fingerprint"] = composition_fingerprint
    if rendered_at:
        base["rendered_at"] = rendered_at
    return build_roundtrip_provenance(**{  # type: ignore[arg-type]
        k: base.get(k)
        for k in (
            "source_audio_asset_id",
            "source_sha256_prefix",
            "result_asset_id",
            "alignment_asset_id",
            "recovery_job_id",
            "composition_fingerprint",
            "revision_id",
            "neural_render_id",
            "bound_at",
            "rendered_at",
        )
    })
