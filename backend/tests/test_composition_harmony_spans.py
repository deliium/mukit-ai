"""Tests for legacy bar-point → explicit harmony span normalization."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.services.composition_harmony_spans import (
    HARMONY_SHAPE_CANONICAL,
    HARMONY_SHAPE_LEGACY,
    HarmonySpanNormalizationError,
    classify_harmony_list_shape,
    ensure_canonical_harmony_spans,
    legacy_harmony_points_to_spans,
    project_spans_to_legacy_change_points,
)
from app.services.composition_migration import migrate_v1_to_v2
from app.services.composition_normalizer import normalize_composition_json


def _minimal_v2(**overrides):
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 4,
        "duration_ticks": 7680,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 4,
                "start_tick": 0,
                "duration_ticks": 7680,
            }
        ],
        "tracks": [
            {
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [],
            }
        ],
        "harmony": [],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [],
    }
    data.update(overrides)
    return data


def test_classify_legacy_and_canonical_shapes():
    assert classify_harmony_list_shape([{"bar": 1, "chord": "C"}]) == HARMONY_SHAPE_LEGACY
    assert (
        classify_harmony_list_shape(
            [{"start_tick": 0, "duration_ticks": 1920, "chord": "C"}]
        )
        == HARMONY_SHAPE_CANONICAL
    )


def test_legacy_points_to_spans_leading_gap_and_final_extent():
    boundaries = [0, 1920, 3840, 5760, 7680]
    spans, stats = legacy_harmony_points_to_spans(
        [{"bar": 2, "chord": "G"}, {"bar": 4, "chord": "C"}],
        boundaries=boundaries,
        duration_ticks=7680,
        bar_count=4,
    )
    assert spans == [
        {"start_tick": 1920, "duration_ticks": 3840, "chord": "G"},
        {"start_tick": 5760, "duration_ticks": 1920, "chord": "C"},
    ]
    assert stats["leading_gap_ticks"] == 1920
    assert stats["final_span_end_tick"] == 7680


def test_legacy_duplicate_bar_last_wins(caplog):
    boundaries = [0, 1920, 3840]
    with caplog.at_level("WARNING"):
        spans, stats = legacy_harmony_points_to_spans(
            [
                {"bar": 1, "chord": "C"},
                {"bar": 1, "chord": "Am"},
                {"bar": 2, "chord": "G"},
            ],
            boundaries=boundaries,
            duration_ticks=3840,
            bar_count=2,
        )
    assert spans[0]["chord"] == "Am"
    assert stats["duplicate_collapsed_count"] == 1
    assert "Collapsed duplicate legacy harmony bars" in caplog.text


def test_legacy_out_of_range_bar_rejected():
    with pytest.raises(HarmonySpanNormalizationError) as exc:
        legacy_harmony_points_to_spans(
            [{"bar": 9, "chord": "C"}],
            boundaries=[0, 1920],
            duration_ticks=1920,
            bar_count=1,
        )
    assert exc.value.code == "harmony_legacy_bar_out_of_range"


def test_mixed_shape_rejected():
    with pytest.raises(HarmonySpanNormalizationError) as exc:
        ensure_canonical_harmony_spans(
            [
                {"bar": 1, "chord": "C"},
                {"start_tick": 1920, "duration_ticks": 1920, "chord": "G"},
            ],
            time_signature="4/4",
            ticks_per_quarter=480,
            bar_count=2,
            duration_ticks=3840,
        )
    assert exc.value.code == "harmony_mixed_shape_rejected"


def test_composition_v2_accepts_legacy_harmony_and_dumps_spans():
    composition = CompositionV2.model_validate(
        _minimal_v2(harmony=[{"bar": 1, "chord": "C"}, {"bar": 3, "chord": "G"}])
    )
    assert [item.model_dump() for item in composition.harmony] == [
        {"start_tick": 0, "duration_ticks": 3840, "chord": "C"},
        {"start_tick": 3840, "duration_ticks": 3840, "chord": "G"},
    ]


def test_composition_v2_rejects_overlapping_canonical_spans():
    with pytest.raises(ValidationError):
        CompositionV2.model_validate(
            _minimal_v2(
                harmony=[
                    {"start_tick": 0, "duration_ticks": 3000, "chord": "C"},
                    {"start_tick": 1920, "duration_ticks": 1920, "chord": "G"},
                ]
            )
        )


def test_composition_v2_idempotent_for_canonical_spans():
    first = CompositionV2.model_validate(
        _minimal_v2(harmony=[{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "G"}])
    )
    second = CompositionV2.model_validate(first.model_dump(mode="json"))
    assert first.model_dump(mode="json")["harmony"] == second.model_dump(mode="json")["harmony"]


def test_variable_meter_legacy_harmony_uses_boundaries():
    composition = CompositionV2.model_validate(
        _minimal_v2(
            bar_count=3,
            duration_ticks=4800,
            time_signature="4/4",
            time_signature_changes=[
                {"tick": 1920, "time_signature": "3/4"},
                {"tick": 3360, "time_signature": "6/8"},
            ],
            sections=[
                {
                    "type": "intro",
                    "start_bar": 1,
                    "bar_count": 1,
                    "start_tick": 0,
                    "duration_ticks": 1920,
                },
                {
                    "type": "verse",
                    "start_bar": 2,
                    "bar_count": 2,
                    "start_tick": 1920,
                    "duration_ticks": 2880,
                },
            ],
            harmony=[{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "G"}],
        )
    )
    assert composition.harmony[0].start_tick == 0
    assert composition.harmony[0].duration_ticks == 1920
    assert composition.harmony[1].start_tick == 1920
    assert composition.harmony[1].duration_ticks == 2880


def test_v1_migration_fidelity_with_legacy_harmony_projection():
    v1 = {
        "schema_version": "composition.v1",
        "tempo": 120,
        "key": "C major",
        "time_signature": "4/4",
        "ticks_per_quarter": 480,
        "bar_count": 2,
        "duration_ticks": 3840,
        "sections": [
            {
                "type": "intro",
                "start_bar": 1,
                "bar_count": 2,
                "start_tick": 0,
                "duration_ticks": 3840,
            }
        ],
        "tracks": [
            {
                "id": "melody-1",
                "name": "Melody",
                "instrument": "piano",
                "role": "melody",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "type": "note",
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                    }
                ],
            }
        ],
        "harmony": [{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "G"}],
    }
    migrated = migrate_v1_to_v2(v1)
    assert migrated.composition.harmony[0].start_tick == 0
    assert migrated.composition.harmony[0].duration_ticks == 1920
    projected = project_spans_to_legacy_change_points(
        migrated.composition.harmony,
        boundaries=[0, 1920, 3840],
        duration_ticks=3840,
        bar_count=2,
    )
    assert projected == [{"bar": 1, "chord": "C"}, {"bar": 2, "chord": "G"}]


def test_normalize_composition_json_converts_legacy_v2_harmony():
    composition = normalize_composition_json(
        _minimal_v2(harmony=[{"bar": 1, "chord": "Am"}, {"bar": 4, "chord": "E"}])
    )
    assert composition.harmony[0].chord == "Am"
    assert composition.harmony[0].duration_ticks == 5760
    assert composition.harmony[1].start_tick == 5760
