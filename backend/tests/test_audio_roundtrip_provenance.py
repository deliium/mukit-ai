"""Tests for audio.roundtrip.provenance.v1 builder."""

from __future__ import annotations

from app.services.audio_roundtrip_provenance import (
    build_roundtrip_provenance,
    merge_render_into_provenance,
)


def test_build_roundtrip_provenance_chain() -> None:
    frag = build_roundtrip_provenance(
        source_audio_asset_id="src_01",
        source_sha256_prefix="abcdef0123456789",
        result_asset_id="res_01",
        alignment_asset_id="aln_01",
        recovery_job_id="job_01",
        composition_fingerprint="snap_fp_deadbeef",
    )
    assert frag.schema_version == "audio.roundtrip.provenance.v1"
    assert frag.alignment_asset_id == "aln_01"
    assert frag.alignment_schema_version == "audio.alignment.v1"


def test_merge_render_into_provenance() -> None:
    base = build_roundtrip_provenance(
        source_audio_asset_id="src_01",
        alignment_asset_id="aln_01",
    )
    merged = merge_render_into_provenance(
        base,
        neural_render_id="nar_01",
        composition_fingerprint="fp_after_edit",
        rendered_at="2026-09-24T13:00:00Z",
    )
    assert merged.neural_render_id == "nar_01"
    assert merged.source_audio_asset_id == "src_01"
    assert merged.composition_fingerprint == "fp_after_edit"
