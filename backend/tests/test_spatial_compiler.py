"""Golden vector tests for the deterministic spatial compiler."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.services.spatial_compiler import compile_spatial_preview
from app.services.spatial_identity import identity_digest
from app.spatial_constants import ENGINE_VERSION, FOA_W_SN3D, distance_gain
from app.spatial_schemas import parse_spatial_scene

_V2 = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _scene_with_source(
    *,
    azimuth_deg: float,
    elevation_deg: float = 0.0,
    distance: float = 1.0,
    spread: float = 0.0,
    motion: list | None = None,
):
    body = {
        "schema_version": "spatial.scene.v1",
        "id": "sscene_0123456789abcdef",
        "name": "golden",
        "source_composition_fingerprint": "a" * 32,
        "engine_version": ENGINE_VERSION,
        "sources": [
            {
                "id": "ssrc_src01",
                "source_kind": "track",
                "track_id": "melody-1",
                "azimuth_deg": azimuth_deg,
                "elevation_deg": elevation_deg,
                "distance": distance,
                "spread": spread,
                "motion": motion or [],
            }
        ],
    }
    return parse_spatial_scene(body)


def test_front_stereo_equal_and_foa_w() -> None:
    preview = compile_spatial_preview(_scene_with_source(azimuth_deg=0.0), scene_revision=1)
    src = preview.sources[0]
    assert src.stereo.left_gain == pytest.approx(src.stereo.right_gain, rel=1e-6)
    assert src.foa.w == pytest.approx(FOA_W_SN3D, rel=1e-6)
    assert src.foa.y == pytest.approx(0.0, abs=1e-9)
    assert src.foa.x == pytest.approx(1.0, rel=1e-6)
    assert preview.engine_version == ENGINE_VERSION


def test_left_azimuth_more_left_gain_and_positive_y() -> None:
    preview = compile_spatial_preview(
        _scene_with_source(azimuth_deg=90.0), scene_revision=1
    )
    src = preview.sources[0]
    assert src.stereo.left_gain > src.stereo.right_gain
    assert src.foa.y == pytest.approx(1.0, rel=1e-6)
    assert src.foa.x == pytest.approx(0.0, abs=1e-9)


def test_right_azimuth_more_right_gain_and_negative_y() -> None:
    preview = compile_spatial_preview(
        _scene_with_source(azimuth_deg=-90.0), scene_revision=1
    )
    src = preview.sources[0]
    assert src.stereo.right_gain > src.stereo.left_gain
    assert src.foa.y == pytest.approx(-1.0, rel=1e-6)


def test_rear_stereo_collapses_toward_center() -> None:
    preview = compile_spatial_preview(
        _scene_with_source(azimuth_deg=180.0), scene_revision=1
    )
    src = preview.sources[0]
    assert src.stereo.left_gain == pytest.approx(src.stereo.right_gain, rel=1e-6)
    assert src.foa.x == pytest.approx(-1.0, rel=1e-6)


def test_distance_attenuates_gains() -> None:
    near = compile_spatial_preview(
        _scene_with_source(azimuth_deg=0.0, distance=1.0), scene_revision=1
    )
    far = compile_spatial_preview(
        _scene_with_source(azimuth_deg=0.0, distance=10.0), scene_revision=1
    )
    assert far.sources[0].distance_gain == pytest.approx(distance_gain(10.0))
    assert far.sources[0].stereo.left_gain < near.sources[0].stereo.left_gain


def test_motion_midpoint_interpolation() -> None:
    scene = _scene_with_source(
        azimuth_deg=0.0,
        motion=[
            {
                "tick": 0,
                "azimuth_deg": 0.0,
                "elevation_deg": 0.0,
                "distance": 1.0,
                "spread": 0.0,
            },
            {
                "tick": 100,
                "azimuth_deg": 90.0,
                "elevation_deg": 0.0,
                "distance": 1.0,
                "spread": 0.0,
            },
        ],
    )
    mid = compile_spatial_preview(scene, scene_revision=1, at_tick=50)
    assert mid.metrics.motion_sampled is True
    # Midpoint az ≈ 45° → Y = sin(45) ≈ 0.707, X = cos(45) ≈ 0.707
    assert mid.sources[0].foa.y == pytest.approx(math.sin(math.radians(45)), rel=1e-5)
    assert mid.sources[0].foa.x == pytest.approx(math.cos(math.radians(45)), rel=1e-5)


def test_compile_preserves_composition_identity() -> None:
    composition = CompositionV2.model_validate(
        json.loads(_V2.read_text(encoding="utf-8"))
    )
    before = identity_digest(composition)
    compile_spatial_preview(_scene_with_source(azimuth_deg=45.0), scene_revision=1)
    assert identity_digest(composition) == before


def test_unresolved_track_skipped_when_ids_provided() -> None:
    preview = compile_spatial_preview(
        _scene_with_source(azimuth_deg=0.0),
        scene_revision=1,
        track_ids={"other-track"},
    )
    assert preview.sources[0].skipped is True
    assert preview.sources[0].skip_reason == "source_unresolved"
    assert preview.metrics.skipped_count == 1
