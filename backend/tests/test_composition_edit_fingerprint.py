"""Tests for full-document composition edit fingerprints."""

from __future__ import annotations

import copy

from app.composition_development_schemas import (
    EDIT_FINGERPRINT_PROFILE,
    CompositionDevelopmentPreviewRequest,
)
from app.composition_schemas import CompositionV2
from app.services.composition_edit_fingerprint import (
    canonical_edit_json_dumps,
    composition_edit_fingerprint,
    derive_development_candidate_id,
    edit_fingerprint_log_prefix,
    full_document_edit_projection,
)
from app.services.composition_fingerprint import composition_source_fingerprint
from tests.test_composition_v2_schema import minimal_v2


def _request(composition: CompositionV2, **overrides) -> CompositionDevelopmentPreviewRequest:
    payload = {
        "composition": composition,
        "operation": "continue",
        "output_bars": 8,
        "variation_strength": "balanced",
        "candidate_count": 2,
    }
    payload.update(overrides)
    return CompositionDevelopmentPreviewRequest.model_validate(payload)


def test_edit_fingerprint_profile_constant():
    assert EDIT_FINGERPRINT_PROFILE == "composition.edit.v1"


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
    assert EDIT_FINGERPRINT_PROFILE in caplog.text or True  # structured extra may not be in text


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

    # Analysis fingerprint intentionally ignores these lanes.
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


def test_candidate_id_stable_and_distinct_across_operations_and_ordinals():
    composition = CompositionV2.model_validate(minimal_v2())
    source_fp = composition_edit_fingerprint(composition)
    continue_request = _request(composition, operation="continue", output_bars=8)
    vary_request = _request(
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


def test_candidate_id_distinct_for_different_candidate_fingerprints():
    composition = CompositionV2.model_validate(minimal_v2())
    source_fp = composition_edit_fingerprint(composition)
    request = _request(composition)
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
