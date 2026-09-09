"""Tests for full-document composition edit fingerprints and domain helpers."""

from __future__ import annotations

import copy

from app.arrangement_schemas import CompositionArrangementPreviewRequest
from app.composition_development_schemas import (
    EDIT_FINGERPRINT_PROFILE as DEVELOPMENT_EDIT_PROFILE,
    CompositionDevelopmentPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_development_fingerprint import derive_development_candidate_id
from app.services.composition_edit_fingerprint import (
    EDIT_FINGERPRINT_PROFILE,
    canonical_edit_json_dumps,
    composition_edit_fingerprint,
    edit_fingerprint_log_prefix,
    full_document_edit_projection,
)
from app.services.composition_fingerprint import composition_source_fingerprint
from app.services.llm_composition_arrangement import derive_arrangement_candidate_id
from tests.test_composition_arrangement_context import _acceptance_instrumentation, _piano_sketch_v2
from tests.test_composition_v2_schema import minimal_v2


def _dev_request(composition: CompositionV2, **overrides) -> CompositionDevelopmentPreviewRequest:
    payload = {
        "composition": composition,
        "operation": "continue",
        "output_bars": 8,
        "variation_strength": "balanced",
        "candidate_count": 2,
    }
    payload.update(overrides)
    return CompositionDevelopmentPreviewRequest.model_validate(payload)


def _arrangement_request(composition: CompositionV2, **overrides) -> CompositionArrangementPreviewRequest:
    payload = {
        "composition": composition,
        "operation": "piano_to_ensemble",
        "source_track_ids": ["piano-melody", "piano-accomp", "bass-1"],
        "instrumentation": _acceptance_instrumentation(),
        "candidate_count": 2,
    }
    payload.update(overrides)
    return CompositionArrangementPreviewRequest.model_validate(payload)


def test_edit_fingerprint_profile_constant():
    assert EDIT_FINGERPRINT_PROFILE == "composition.edit.v1"
    assert DEVELOPMENT_EDIT_PROFILE == EDIT_FINGERPRINT_PROFILE


def test_canonical_edit_json_sorts_object_keys_preserves_arrays():
    left = canonical_edit_json_dumps({"b": 1, "a": [3, 1, 2], "nested": {"z": 1, "y": 2}})
    right = canonical_edit_json_dumps({"nested": {"y": 2, "z": 1}, "a": [3, 1, 2], "b": 1})
    assert left == right
    assert left == '{"a":[3,1,2],"b":1,"nested":{"y":2,"z":1}}'


def test_edit_fingerprint_stable_and_logged(caplog):
    composition = CompositionV2.model_validate(minimal_v2())
    with caplog.at_level("DEBUG"):
        first = composition_edit_fingerprint(composition)
        second = composition_edit_fingerprint(composition)
    assert first == second
    assert len(first) == 64
    assert edit_fingerprint_log_prefix(first) == first[:12]
    assert "Composition edit fingerprint computed" in caplog.text


def test_edit_fingerprint_includes_markers_and_expression_unlike_analysis():
    base = CompositionV2.model_validate(minimal_v2())
    with_markers = CompositionV2.model_validate(
        minimal_v2(markers=[{"tick": 0, "kind": "rehearsal", "label": "A"}])
    )
    with_expression = CompositionV2.model_validate(
        minimal_v2(
            tracks=[
                {
                    "id": "piano-1",
                    "name": "Piano",
                    "instrument": "piano",
                    "role": "harmony",
                    "midi_program": 0,
                    "channel": 1,
                    "events": [],
                    "volume": 40,
                    "expression": 60,
                    "dynamic_marks": [{"tick": 0, "level": "pp"}],
                }
            ]
        )
    )

    base_edit = composition_edit_fingerprint(base)
    markers_edit = composition_edit_fingerprint(with_markers)
    expression_edit = composition_edit_fingerprint(with_expression)

    assert base_edit != markers_edit
    assert base_edit != expression_edit

    assert composition_source_fingerprint(base) == composition_source_fingerprint(with_markers)
    assert composition_source_fingerprint(base) == composition_source_fingerprint(with_expression)


def test_edit_fingerprint_sensitive_to_event_id_and_motif_changes():
    base = CompositionV2.model_validate(
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
                            "id": "n1",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ]
        )
    )
    changed_id = CompositionV2.model_validate(
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
                            "id": "n2",
                            "pitch": "C4",
                            "start_tick": 0,
                            "duration_ticks": 480,
                            "velocity": 80,
                        }
                    ],
                }
            ]
        )
    )
    assert composition_edit_fingerprint(base) != composition_edit_fingerprint(changed_id)


def test_source_composition_not_mutated_by_fingerprint():
    composition = CompositionV2.model_validate(minimal_v2())
    before = copy.deepcopy(composition.model_dump(mode="json"))
    _ = composition_edit_fingerprint(composition)
    after = composition.model_dump(mode="json")
    assert before == after


def test_projection_includes_fingerprint_profile_and_full_document():
    composition = CompositionV2.model_validate(minimal_v2())
    projection = full_document_edit_projection(composition)
    assert projection["fingerprint_profile"] == EDIT_FINGERPRINT_PROFILE
    assert projection["document"]["schema_version"] == "composition.v2"
    assert "markers" in projection["document"]
    assert "motifs" in projection["document"]


def test_development_candidate_id_stable_and_distinct_across_operations_and_ordinals():
    composition = CompositionV2.model_validate(minimal_v2())
    source_fp = composition_edit_fingerprint(composition)
    continue_request = _dev_request(composition, operation="continue", output_bars=8)
    vary_request = _dev_request(
        composition,
        operation="vary_section",
        output_bars=None,
        source={"start_bar": 1, "end_bar": 2},
    )
    candidate_fp = composition_edit_fingerprint(CompositionV2.model_validate(minimal_v2()))

    id_a = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=continue_request,
        candidate_fingerprint=candidate_fp,
        candidate_ordinal=1,
    )
    id_a_again = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=continue_request,
        candidate_fingerprint=candidate_fp,
        candidate_ordinal=1,
    )
    id_b = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=continue_request,
        candidate_fingerprint=candidate_fp,
        candidate_ordinal=2,
    )
    id_vary = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=vary_request,
        candidate_fingerprint=candidate_fp,
        candidate_ordinal=1,
    )

    assert id_a == id_a_again
    assert id_a != id_b
    assert id_a != id_vary
    assert len(id_a) == 64


def test_development_candidate_id_distinct_for_different_candidate_fingerprints():
    composition = CompositionV2.model_validate(minimal_v2())
    source_fp = composition_edit_fingerprint(composition)
    request = _dev_request(composition)
    left = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=request,
        candidate_fingerprint="1" * 64,
        candidate_ordinal=1,
    )
    right = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=request,
        candidate_fingerprint="2" * 64,
        candidate_ordinal=1,
    )
    assert left != right


def test_composition_fingerprint_decoupled_from_development_and_arrangement_ids():
    composition = _piano_sketch_v2()
    source_fp = composition_edit_fingerprint(composition)
    assert len(source_fp) == 64

    # Composition hashing must not depend on development/arrangement request shapes.
    assert "derive_development_candidate_id" not in dir(
        __import__("app.services.composition_edit_fingerprint", fromlist=["*"])
    )

    arrangement = _arrangement_request(composition)
    arrangement_id = derive_arrangement_candidate_id(
        edit_source_fingerprint=source_fp,
        request=arrangement,
        candidate_fingerprint="a" * 64,
        candidate_ordinal=1,
        catalog_fingerprint="b" * 64,
    )
    development_id = derive_development_candidate_id(
        edit_source_fingerprint=source_fp,
        request=_dev_request(composition),
        candidate_fingerprint="a" * 64,
        candidate_ordinal=1,
    )
    assert arrangement_id != development_id
    assert len(arrangement_id) == 64


def test_arrangement_candidate_id_distinct_by_ordinal_and_operation():
    composition = _piano_sketch_v2()
    source_fp = composition_edit_fingerprint(composition)
    request = _arrangement_request(composition)
    change = _arrangement_request(
        composition,
        operation="change_instrumentation",
        source_track_ids=["piano-melody"],
        instrumentation={
            "before": [
                {
                    "part_id": "b-melody",
                    "instrument_id": "acoustic_grand_piano",
                    "role": "melody",
                    "source_track_ids": ["piano-melody"],
                    "doubling_policy": "none",
                }
            ],
            "after": [
                {
                    "part_id": "a-melody",
                    "instrument_id": "violin",
                    "role": "melody",
                    "source_track_ids": [],
                    "doubling_policy": "none",
                }
            ],
        },
    )
    id1 = derive_arrangement_candidate_id(
        edit_source_fingerprint=source_fp,
        request=request,
        candidate_fingerprint="c" * 64,
        candidate_ordinal=1,
        catalog_fingerprint="d" * 64,
    )
    id2 = derive_arrangement_candidate_id(
        edit_source_fingerprint=source_fp,
        request=request,
        candidate_fingerprint="c" * 64,
        candidate_ordinal=2,
        catalog_fingerprint="d" * 64,
    )
    id_change = derive_arrangement_candidate_id(
        edit_source_fingerprint=source_fp,
        request=change,
        candidate_fingerprint="c" * 64,
        candidate_ordinal=1,
        catalog_fingerprint="d" * 64,
    )
    assert id1 != id2
    assert id1 != id_change
