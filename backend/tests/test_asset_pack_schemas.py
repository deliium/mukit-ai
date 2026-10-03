"""Unit tests for asset.pack.*.v1 schemas and locked preset constants."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.asset_pack_schemas import (
    FORBIDDEN_NOTE_KEYS,
    GAME_SOUNDTRACK_V1_PROPAGATE,
    GAME_SOUNDTRACK_V1_SLOTS,
    AssetPackBriefV1,
    AssetPackError,
    AssetPackPlanV1,
    AssetPackProductionV1,
    AssetPackPropagateOp,
    AssetPackV1,
    game_soundtrack_propagate_complete,
    reject_embedded_note_keys,
)


def _production(**overrides: object) -> dict:
    body: dict = {
        "schema_version": "asset.pack.production.v1",
        "master_target": "cinematic",
        "guarantee": False,
        "catalog_instrument_ids": ["violin", "cello", "french_horn"],
    }
    body.update(overrides)
    return body


def _minimal_plan(**overrides: object) -> dict:
    digest = "ab" * 32
    body: dict = {
        "schema_version": "asset.pack.plan.v1",
        "title": "Quest Pack",
        "preset": "game_soundtrack_v1",
        "slots": [
            {
                "slot_id": "main_theme",
                "label": "Main Theme",
                "adaptive_label": "main_theme",
                "density": "dense",
                "duration_seconds": 90,
                "narrative_intent": "establish franchise theme",
            },
            {
                "slot_id": "menu",
                "label": "Menu",
                "adaptive_label": "menu",
                "density": "sparse",
                "duration_seconds": 60,
                "narrative_intent": "calm menu loop",
            },
        ],
        "constraints": {
            "opening_key": "C minor",
            "time_signature": "4/4",
            "tempo_min": 72,
            "tempo_max": 96,
            "instrumentation": ["violin", "cello"],
            "motif_label": "Theme A",
        },
        "production": _production(),
        "theme_policy": {
            "seed_slot_id": "main_theme",
            "propagate": {"menu": {"operation": "repeat"}},
            "require_motif_pin": True,
        },
        "plan_digest": digest,
    }
    body.update(overrides)
    return body


def test_game_soundtrack_propagate_table_complete() -> None:
    assert game_soundtrack_propagate_complete()
    assert "main_theme" not in GAME_SOUNDTRACK_V1_PROPAGATE
    assert len(GAME_SOUNDTRACK_V1_SLOTS) == 10
    assert set(GAME_SOUNDTRACK_V1_PROPAGATE) == {
        row["slot_id"] for row in GAME_SOUNDTRACK_V1_SLOTS if row["slot_id"] != "main_theme"
    }


def test_brief_accepts_game_preset() -> None:
    brief = AssetPackBriefV1.model_validate(
        {
            "schema_version": "asset.pack.brief.v1",
            "title": "Forest Quest",
            "preset": "game_soundtrack_v1",
        }
    )
    assert brief.composer_profile_strength == "normal"
    assert brief.include_adaptive_scaffolds is True
    assert brief.include_rendering is False


def test_brief_rejects_extra_fields() -> None:
    with pytest.raises((ValidationError, AssetPackError)):
        AssetPackBriefV1.model_validate(
            {
                "schema_version": "asset.pack.brief.v1",
                "title": "X",
                "preset": "game_soundtrack_v1",
                "unexpected": True,
            }
        )


def test_brief_rejects_bad_slot_id() -> None:
    with pytest.raises((ValidationError, AssetPackError)):
        AssetPackBriefV1.model_validate(
            {
                "schema_version": "asset.pack.brief.v1",
                "title": "X",
                "preset": None,
                "slots": [{"slot_id": "Bad-Id!", "label": "Bad"}],
            }
        )


def test_production_guarantee_must_be_false() -> None:
    with pytest.raises((ValidationError, AssetPackError)):
        AssetPackProductionV1.model_validate(_production(guarantee=True))
    prod = AssetPackProductionV1.model_validate(_production())
    assert prod.guarantee is False


def test_plan_rejects_embedded_notes(caplog: pytest.LogCaptureFixture) -> None:
    dirty = _minimal_plan()
    dirty["slots"][0]["events"] = [{"pitch": "C4"}]
    with caplog.at_level(logging.DEBUG, logger="app.asset_pack_schemas"):
        with pytest.raises(AssetPackError) as exc:
            AssetPackPlanV1.model_validate(dirty)
    assert exc.value.code == "asset_pack_embedded_notes"
    assert any(
        getattr(record, "code", None) == "asset_pack_embedded_notes"
        for record in caplog.records
    )


def test_plan_rejects_playable_top_level() -> None:
    dirty = _minimal_plan()
    dirty["tracks"] = []
    with pytest.raises(AssetPackError) as exc:
        AssetPackPlanV1.model_validate(dirty)
    assert exc.value.code == "asset_pack_invalid"


def test_reject_embedded_note_keys_helper() -> None:
    with pytest.raises(AssetPackError) as exc:
        reject_embedded_note_keys({"meta": {"notes": []}}, model="test")
    assert exc.value.code == "asset_pack_embedded_notes"
    for key in FORBIDDEN_NOTE_KEYS:
        payload = {key: []}
        with pytest.raises(AssetPackError):
            reject_embedded_note_keys(payload, model="test")


def test_propagate_transpose_requires_semitones() -> None:
    with pytest.raises((ValidationError, AssetPackError)):
        AssetPackPropagateOp.model_validate({"operation": "transpose"})
    op = AssetPackPropagateOp.model_validate(
        {"operation": "transpose", "transpose_semitones": 7}
    )
    assert op.transpose_semitones == 7


def test_pack_document_id_pattern() -> None:
    pack = AssetPackV1.model_validate(
        {
            "schema_version": "asset.pack.v1",
            "id": "apack_" + "ab" * 8,
            "name": "Quest Pack",
            "status": "planned",
            "plan_digest": "cd" * 32,
            "document_revision": 1,
            "slots": [{"slot_id": "main_theme", "status": "pending"}],
        }
    )
    assert pack.id.startswith("apack_")
    with pytest.raises((ValidationError, AssetPackError)):
        AssetPackV1.model_validate(
            {
                "schema_version": "asset.pack.v1",
                "id": "not_a_pack",
                "name": "X",
                "plan_digest": "cd" * 32,
                "document_revision": 1,
            }
        )


def test_minimal_plan_validates() -> None:
    plan = AssetPackPlanV1.model_validate(_minimal_plan())
    assert plan.slots[0].duration_seconds == 90
    assert plan.theme_policy.seed_slot_id == "main_theme"
