"""Tests for deterministic analysis source fingerprints."""

from __future__ import annotations

import copy
import json

from app.analysis_schemas import ANALYSIS_FINGERPRINT_PROFILE, prepare_analysis_request
from app.composition_schemas import CompositionV2
from app.services.composition_fingerprint import (
    analysis_relevant_projection,
    canonical_analysis_json_dumps,
    composition_source_fingerprint,
)


def minimal_v2(**overrides):
    data = {
        "schema_version": "composition.v2",
        "tempo": 100,
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
                "id": "piano-1",
                "name": "Piano",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "channel": 1,
                "events": [
                    {
                        "pitch": "C4",
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "velocity": 80,
                        "articulations": ["accent"],
                    }
                ],
                "volume": 100,
                "expression": 127,
                "dynamic_marks": [{"tick": 0, "level": "mf"}],
            }
        ],
        "harmony": [{"bar": 1, "chord": "C"}],
        "tempo_changes": [],
        "time_signature_changes": [],
        "key_changes": [],
        "markers": [{"tick": 0, "kind": "rehearsal", "label": "A"}],
    }
    data.update(overrides)
    return data


def test_fingerprint_profile_constant_exported():
    assert ANALYSIS_FINGERPRINT_PROFILE == "analysis.source.v1"


def test_canonical_json_object_key_order_stable():
    left = canonical_analysis_json_dumps({"b": 1, "a": {"z": 2, "y": 3}})
    right = canonical_analysis_json_dumps({"a": {"y": 3, "z": 2}, "b": 1})
    assert left == right
    assert left == '{"a":{"y":3,"z":2},"b":1}'


def test_fingerprint_stable_across_repeated_runs(caplog):
    composition = CompositionV2.model_validate(minimal_v2())
    with caplog.at_level("DEBUG"):
        first = composition_source_fingerprint(composition)
        second = composition_source_fingerprint(composition)
    assert first == second
    assert len(first) == 64
    assert "Composition analysis fingerprint computed" in caplog.text


def test_fingerprint_ignores_markers_and_expression_lanes():
    base = CompositionV2.model_validate(minimal_v2())
    altered = CompositionV2.model_validate(
        minimal_v2(
            markers=[{"tick": 480, "kind": "text", "label": "other"}],
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "articulations": ["accent"],
                        }
                    ],
                    "volume": 40,
                    "expression": 60,
                    "dynamic_marks": [{"tick": 480, "level": "pp"}],
                    "sustain_pedals": [{"start_tick": 0, "duration_ticks": 480}],
                    "automation": [
                        {
                            "parameter": "volume",
                            "interpolation": "step",
                            "points": [{"tick": 480, "value": 50}],
                        }
                    ],
                }
            ],
        )
    )
    assert composition_source_fingerprint(base) == composition_source_fingerprint(altered)


def test_fingerprint_changes_when_analysis_relevant_fields_change():
    base = CompositionV2.model_validate(minimal_v2())
    pitch_changed = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "D4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "articulations": ["accent"],
                        }
                    ],
                }
            ]
        )
    )
    key_changed = CompositionV2.model_validate(minimal_v2(key="G major"))
    harmony_changed = CompositionV2.model_validate(minimal_v2(harmony=[{"bar": 1, "chord": "G"}]))
    role_changed = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "melody",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [
                        {
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                            "articulations": ["accent"],
                        }
                    ],
                }
            ]
        )
    )

    base_fp = composition_source_fingerprint(base)
    assert composition_source_fingerprint(pitch_changed) != base_fp
    assert composition_source_fingerprint(key_changed) != base_fp
    assert composition_source_fingerprint(harmony_changed) != base_fp
    assert composition_source_fingerprint(role_changed) != base_fp


def test_projection_object_key_permutation_does_not_change_fingerprint():
    composition = CompositionV2.model_validate(minimal_v2())
    projection = analysis_relevant_projection(composition)
    permuted = json.loads(json.dumps(projection))
    # Rebuild with shuffled top-level insertion order.
    rebuilt = {key: permuted[key] for key in reversed(list(permuted.keys()))}
    assert canonical_analysis_json_dumps(projection) == canonical_analysis_json_dumps(rebuilt)
    assert composition_source_fingerprint(composition) == composition_source_fingerprint(
        CompositionV2.model_validate(composition.model_dump(mode="json"))
    )


def test_prepare_request_then_fingerprint_does_not_mutate_source():
    raw = minimal_v2()
    before = copy.deepcopy(raw)
    validated, _, _ = prepare_analysis_request(raw, {"kind": "composition"})
    fingerprint = composition_source_fingerprint(validated)
    assert isinstance(fingerprint, str)
    assert raw == before
