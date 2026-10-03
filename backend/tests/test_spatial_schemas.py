"""Accept/reject fixtures for spatial.scene.v1 and spatial.preview.v1."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.composition_schemas import CompositionV2
from app.services.spatial_identity import identity_digest
from app.spatial_constants import (
    ENGINE_VERSION,
    MAX_MOTION_KEYFRAMES,
    MAX_SOURCES,
    SCENE_SCHEMA_VERSION,
)
from app.spatial_schemas import (
    SpatialSceneError,
    parse_spatial_preview,
    parse_spatial_scene,
    reject_scene_embedded_material,
    stem_set_fingerprint,
)

_V2_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "fixtures"
    / "composition_v2_expressive.json"
)


def _minimal_scene(**overrides):
    body = {
        "schema_version": SCENE_SCHEMA_VERSION,
        "name": "Front stereo",
        "source_composition_fingerprint": "a" * 32,
        "engine_version": ENGINE_VERSION,
        "sources": [
            {
                "id": "ssrc_melody01",
                "source_kind": "track",
                "track_id": "melody-1",
                "azimuth_deg": 30.0,
                "elevation_deg": 0.0,
                "distance": 1.5,
                "spread": 0.1,
            },
            {
                "id": "ssrc_accomp01",
                "source_kind": "track",
                "track_id": "harmony-1",
                "azimuth_deg": -30.0,
                "elevation_deg": 0.0,
                "distance": 2.0,
                "spread": 0.2,
            },
        ],
    }
    body.update(overrides)
    return body


def test_parse_scene_accepts_track_sources() -> None:
    scene = parse_spatial_scene(_minimal_scene())
    assert scene.name == "Front stereo"
    assert len(scene.sources) == 2
    assert scene.sources[0].azimuth_deg == pytest.approx(30.0)


def test_reject_scene_embeds_events() -> None:
    with pytest.raises(SpatialSceneError) as captured:
        reject_scene_embedded_material({"events": [{"id": "n1"}]})
    assert captured.value.code == "scene_embeds_events"


def test_reject_scene_embeds_harmony_nested() -> None:
    body = _minimal_scene()
    body["meta"] = {"harmony": [{"bar": 1, "chord": "C"}]}
    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_scene(body)
    assert captured.value.code == "scene_embeds_harmony"


def test_reject_scene_embeds_pcm() -> None:
    with pytest.raises(SpatialSceneError) as captured:
        reject_scene_embedded_material({"audio_base64": "AAAA"})
    assert captured.value.code == "scene_embeds_pcm"


def test_stem_source_requires_stem_id() -> None:
    body = _minimal_scene(
        sources=[
            {
                "id": "ssrc_stem01",
                "source_kind": "stem",
                "stem_role": "melody",
                "azimuth_deg": 0.0,
                "elevation_deg": 0.0,
                "distance": 1.0,
                "spread": 0.0,
            }
        ],
        source_stem_set_id="stemset_1",
        source_stem_set_fingerprint="b" * 64,
    )
    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_scene(body)
    assert captured.value.code == "stem_id_required"


def test_stem_sources_require_scene_stem_set_pins() -> None:
    body = _minimal_scene(
        sources=[
            {
                "id": "ssrc_stem01",
                "source_kind": "stem",
                "stem_id": "stem_abc",
                "azimuth_deg": 0.0,
                "elevation_deg": 0.0,
                "distance": 1.0,
                "spread": 0.0,
            }
        ],
    )
    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_scene(body)
    assert captured.value.code == "spatial_scene_invalid"


def test_stem_source_accepts_with_stem_id_and_pins() -> None:
    scene = parse_spatial_scene(
        _minimal_scene(
            sources=[
                {
                    "id": "ssrc_stem01",
                    "source_kind": "stem",
                    "stem_id": "stem_abc",
                    "stem_role": "melody",
                    "azimuth_deg": 45.0,
                    "elevation_deg": 10.0,
                    "distance": 3.0,
                    "spread": 0.25,
                }
            ],
            source_stem_set_id="stemset_1",
            source_stem_set_fingerprint="c" * 64,
        )
    )
    assert scene.sources[0].stem_id == "stem_abc"
    assert scene.source_stem_set_id == "stemset_1"


def test_too_many_sources_refused() -> None:
    sources = [
        {
            "id": f"ssrc_{i:04d}",
            "source_kind": "track",
            "track_id": f"t{i}",
            "azimuth_deg": 0.0,
            "elevation_deg": 0.0,
            "distance": 1.0,
            "spread": 0.0,
        }
        for i in range(MAX_SOURCES + 1)
    ]
    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_scene(_minimal_scene(sources=sources))
    assert captured.value.code == "too_many_sources"


def test_too_many_motion_keyframes_refused() -> None:
    motion = [
        {
            "tick": i * 10,
            "azimuth_deg": float(i),
            "elevation_deg": 0.0,
            "distance": 1.0,
            "spread": 0.0,
        }
        for i in range(MAX_MOTION_KEYFRAMES + 1)
    ]
    body = _minimal_scene()
    body["sources"][0]["motion"] = motion
    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_scene(body)
    assert captured.value.code == "too_many_motion_keyframes"


def test_preview_refuses_pcm_fields() -> None:
    with pytest.raises(SpatialSceneError) as captured:
        parse_spatial_preview(
            {
                "schema_version": "spatial.preview.v1",
                "scene_id": "sscene_0123456789abcdef",
                "scene_revision": 1,
                "engine_version": ENGINE_VERSION,
                "source_composition_fingerprint": "d" * 32,
                "pcm": [0.1, 0.2],
            }
        )
    assert captured.value.code == "preview_invalid"


def test_stem_set_fingerprint_sorted_join() -> None:
    a = stem_set_fingerprint(["bb", "aa"])
    b = stem_set_fingerprint(["aa", "bb"])
    assert a == b
    assert len(a) == 64


def test_identity_digest_reuse_stable() -> None:
    composition = CompositionV2.model_validate(
        json.loads(_V2_FIXTURE.read_text(encoding="utf-8"))
    )
    first = identity_digest(composition)
    second = identity_digest(composition)
    assert first == second
    assert len(first) == 64
