"""Schema accept/reject tests for audio.alignment.v1 + roundtrip provenance."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.audio_alignment_schemas import (
    AUDIO_ALIGNMENT_SCHEMA_VERSION,
    AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION,
    AudioAlignmentBoundDiscoveryV1,
    AudioAlignmentBoundJobV1,
    AudioAlignmentError,
    AudioAlignmentMapParams,
    AudioAlignmentQuality,
    AudioAlignmentStemBinding,
    AudioAlignmentV1,
    AudioRoundtripProvenanceV1,
)
from app.audio_recovery_schemas import (
    AudioRecoveryAssetMeta,
    AudioRecoveryBindResponseV1,
)


def _quality(**overrides) -> dict:
    base = {
        "overall_confidence": 0.82,
        "tempo_confidence": 0.8,
        "beat_grid_confidence": 0.75,
        "offset_uncertainty_ms": 40.0,
        "method": "timeline_parametric",
        "issues": [],
        "stem_qualities": [],
    }
    base.update(overrides)
    return base


def _map_params(**overrides) -> dict:
    base = {
        "tempo_bpm": 120.0,
        "ticks_per_quarter": 480,
        "downbeat_offset_seconds": 0.5,
        "origin_tick": 0,
        "meter": "4/4",
        "scaffolding_tempo_bpm": 120.0,
    }
    base.update(overrides)
    return base


def _minimal_alignment(**overrides) -> AudioAlignmentV1:
    base = {
        "source_audio_asset_id": "src_asset_01",
        "result_asset_id": "res_asset_01",
        "job_id": "job_01",
        "project_id": "proj_01",
        "composition_fingerprint": "snap_deadbeef01",
        "map": _map_params(),
        "stem_bindings": [
            {"stem": "melody", "track_id": "track_melody"},
        ],
        "quality": _quality(),
        "created_at": "2026-09-24T12:00:00Z",
    }
    base.update(overrides)
    return AudioAlignmentV1.model_validate(base)


def test_alignment_accepts_minimal_valid_document() -> None:
    doc = _minimal_alignment()
    assert doc.schema_version == AUDIO_ALIGNMENT_SCHEMA_VERSION
    assert doc.playable is False
    assert doc.map.downbeat_offset_seconds == 0.5
    assert doc.quality.method == "timeline_parametric"
    assert doc.stem_bindings[0].stem == "melody"
    assert doc.stem_bindings[0].track_id == "track_melody"


def test_alignment_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        AudioAlignmentV1.model_validate(
            {
                **_minimal_alignment().model_dump(),
                "unexpected": True,
            }
        )


def test_alignment_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        AudioAlignmentQuality.model_validate(_quality(overall_confidence=1.5))


def test_alignment_rejects_duplicate_stem_bindings() -> None:
    with pytest.raises(ValidationError, match="duplicate stem binding"):
        _minimal_alignment(
            stem_bindings=[
                {"stem": "melody", "track_id": "t1"},
                {"stem": "melody", "track_id": "t2"},
            ]
        )


def test_alignment_dedupes_issue_codes() -> None:
    q = AudioAlignmentQuality.model_validate(
        _quality(
            issues=["alignment_low_confidence", "alignment_low_confidence", "alignment_tempo_diverged"]
        )
    )
    assert q.issues == ["alignment_low_confidence", "alignment_tempo_diverged"]


def test_alignment_rejects_stem_wav_asset_id_field() -> None:
    """v1 stem bindings must not accept durable stem WAV asset ids."""
    with pytest.raises(ValidationError):
        AudioAlignmentStemBinding.model_validate(
            {
                "stem": "vocals",
                "track_id": "track_v",
                "stem_audio_asset_id": "stem_wav_01",
            }
        )


def test_map_params_rejects_negative_offset() -> None:
    with pytest.raises(ValidationError):
        AudioAlignmentMapParams.model_validate(
            _map_params(downbeat_offset_seconds=-0.1)
        )


def test_roundtrip_provenance_accepts_chain_ids() -> None:
    frag = AudioRoundtripProvenanceV1.model_validate(
        {
            "source_audio_asset_id": "src_01",
            "source_sha256_prefix": "abcdef0123456789",
            "result_asset_id": "res_01",
            "alignment_asset_id": "aln_01",
            "recovery_job_id": "job_01",
            "composition_fingerprint": "snap_fp_deadbeef",
            "neural_render_id": "nar_01",
            "bound_at": "2026-09-24T12:00:00Z",
            "alignment_schema_version": "audio.alignment.v1",
            "recovery_result_schema_version": "audio.recovery.result.v1",
        }
    )
    assert frag.schema_version == AUDIO_ROUNDTRIP_PROVENANCE_SCHEMA_VERSION
    assert frag.alignment_asset_id == "aln_01"


def test_roundtrip_provenance_rejects_extra() -> None:
    with pytest.raises(ValidationError):
        AudioRoundtripProvenanceV1.model_validate(
            {
                "schema_version": "audio.roundtrip.provenance.v1",
                "prompt": "secret prompt text",
            }
        )


def test_bound_discovery_idle_when_unbound() -> None:
    body = AudioAlignmentBoundDiscoveryV1.model_validate(
        {"project_id": "proj_01", "bound": False, "latest": None, "jobs": []}
    )
    assert body.bound is False
    assert body.latest is None


def test_bound_discovery_with_latest_job() -> None:
    job = AudioAlignmentBoundJobV1.model_validate(
        {
            "job_id": "job_01",
            "source_audio_asset_id": "src_01",
            "result_asset_id": "res_01",
            "alignment_asset_id": "aln_01",
            "source_sha256_prefix": "aabbccdd11223344",
        }
    )
    body = AudioAlignmentBoundDiscoveryV1.model_validate(
        {
            "project_id": "proj_01",
            "bound": True,
            "latest": job.model_dump(),
            "jobs": [job.model_dump()],
        }
    )
    assert body.bound is True
    assert body.latest is not None
    assert body.latest.alignment_asset_id == "aln_01"


def test_bind_response_includes_alignment_asset_id() -> None:
    resp = AudioRecoveryBindResponseV1.model_validate(
        {
            "job_id": "job_01",
            "project_id": "proj_01",
            "source_audio_asset_id": "src_01",
            "result_asset_id": "res_01",
            "alignment_asset_id": "aln_01",
            "overlay_entry_count": 3,
            "roundtrip_provenance": {
                "source_audio_asset_id": "src_01",
                "result_asset_id": "res_01",
                "alignment_asset_id": "aln_01",
                "composition_fingerprint": "snap_fp_01xx",
            },
        }
    )
    assert resp.alignment_asset_id == "aln_01"
    assert resp.roundtrip_provenance is not None
    assert resp.roundtrip_provenance.alignment_asset_id == "aln_01"


def test_asset_meta_accepts_alignment_json_kind() -> None:
    meta = AudioRecoveryAssetMeta.model_validate(
        {
            "id": "aln_01",
            "project_id": "proj_01",
            "job_id": "job_01",
            "kind": "alignment_json",
            "content_type": "application/json",
            "byte_size": 512,
            "sha256_prefix": "deadbeefcafe0001",
            "created_at": "2026-09-24T12:00:00Z",
        }
    )
    assert meta.kind == "alignment_json"


def test_alignment_error_carries_http_status() -> None:
    err = AudioAlignmentError(
        "audio_alignment_missing_source",
        "Source required",
        http_status=422,
        details={"job_id_prefix": "job_01"},
    )
    assert err.code == "audio_alignment_missing_source"
    assert err.http_status == 422
    assert err.details["job_id_prefix"] == "job_01"
