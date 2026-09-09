"""Composition arrangement request/response schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.arrangement_schemas import (
    ARRANGEMENT_ALGORITHM_VERSION,
    ARRANGEMENT_CATALOG_VERSION,
    ARRANGEMENT_ERROR_CODES,
    ARRANGEMENT_MAX_CANDIDATE_COUNT,
    ARRANGEMENT_MAX_INSTRUCTION_CHARS,
    ARRANGEMENT_MAX_REJECTED_ATTEMPTS,
    ARRANGEMENT_OPERATIONS,
    ARRANGEMENT_RANGE_POLICY_VERSION,
    ARRANGEMENT_WARNING_CODES,
    ArrangementCandidate,
    ArrangementInstrumentationRequirements,
    ArrangementPartRequirement,
    ArrangementRejectedAttempt,
    CompositionArrangementDraft,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
    CompositionArrangementPreviewResponse,
    EDIT_FINGERPRINT_PROFILE,
    validate_arrangement_request_limits,
)
from app.composition_schemas import CompositionV2
from tests.test_composition_v2_schema import minimal_v2


def _part(
    part_id: str,
    instrument_id: str,
    *,
    role: str | None = None,
    source_track_ids: list[str] | None = None,
    doubling_policy: str = "none",
) -> dict:
    return {
        "part_id": part_id,
        "instrument_id": instrument_id,
        "role": role,
        "source_track_ids": source_track_ids or [],
        "doubling_policy": doubling_policy,
    }


def _instrumentation(
    before: list[dict] | None = None,
    after: list[dict] | None = None,
) -> dict:
    return {
        "before": before
        or [
            _part("b1", "acoustic_grand_piano", role="melody", source_track_ids=["piano-1"]),
        ],
        "after": after
        or [
            _part("a1", "violin", role="melody", source_track_ids=["piano-1"]),
        ],
    }


def _base_request(**overrides):
    composition = CompositionV2.model_validate(minimal_v2())
    payload = {
        "composition": composition,
        "operation": "change_instrumentation",
        "source_track_ids": ["piano-1"],
        "protected_track_ids": [],
        "instrumentation": _instrumentation(),
        "candidate_count": 1,
    }
    payload.update(overrides)
    return payload


def _density() -> dict:
    metrics = {
        "event_count": 4,
        "attack_count": 4,
        "active_track_count": 1,
        "union_occupancy_ticks": 1920,
        "max_simultaneity": 2,
    }
    return {"before": metrics, "after": {**metrics, "event_count": 6}}


def _candidate(**overrides):
    composition = CompositionV2.model_validate(minimal_v2())
    payload = {
        "candidate_id": "c" * 64,
        "candidate_fingerprint": "b" * 64,
        "edit_source_fingerprint": "a" * 64,
        "catalog_fingerprint": "d" * 64,
        "operation": "change_instrumentation",
        "composition": composition,
        "provider": "fake",
        "model": "fake-arr",
        "before_inventory": [
            {
                "track_id": "piano-1",
                "instrument": "piano",
                "role": "harmony",
                "midi_program": 0,
                "event_count": 0,
                "part_id": "b1",
            }
        ],
        "after_inventory": [
            {
                "track_id": "violin-1",
                "instrument": "violin",
                "role": "melody",
                "midi_program": 40,
                "event_count": 0,
                "part_id": "a1",
            }
        ],
        "manifest": {
            "retained_track_ids": [],
            "removed_track_ids": ["piano-1"],
            "added_track_ids": ["violin-1"],
            "source_to_target": [
                {
                    "source_track_id": "piano-1",
                    "target_track_id": "violin-1",
                    "relationship": "reinstrumented",
                }
            ],
        },
        "event_counts": {"copied": 0, "moved": 0, "generated": 0, "removed": 0},
        "density": _density(),
        "target_profile_fingerprints": [
            {"instrument_id": "violin", "profile_fingerprint": "e" * 64}
        ],
        "assertions": [
            {
                "kind": "melody_preservation",
                "satisfied": True,
                "required": True,
                "detail": "melody multiset preserved",
            }
        ],
    }
    payload.update(overrides)
    return payload


# --- Operation matrix ----------------------------------------------------------


@pytest.mark.parametrize(
    "operation,before,after",
    [
        (
            "change_instrumentation",
            [_part("b1", "acoustic_grand_piano", role="melody")],
            [_part("a1", "violin", role="melody")],
        ),
        (
            "add_accompaniment",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "string_ensemble_1", role="harmony"),
            ],
        ),
        (
            "remove_accompaniment",
            [_part("b1", "acoustic_grand_piano", role="harmony")],
            [_part("a1", "violin", role="melody")],
        ),
        (
            "orchestrate_selected_tracks",
            [_part("b1", "acoustic_grand_piano", role="harmony")],
            [
                _part("a1", "cello", role="bass"),
                _part("a2", "violin", role="melody"),
            ],
        ),
        (
            "piano_to_ensemble",
            [_part("b1", "acoustic_grand_piano", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "cello", role="bass"),
            ],
        ),
        (
            "simplify_arrangement",
            [
                _part("b1", "violin", role="melody"),
                _part("b2", "string_ensemble_1", role="harmony"),
            ],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "string_ensemble_1", role="harmony"),
            ],
        ),
        (
            "increase_texture_density",
            [_part("b1", "violin", role="melody")],
            [_part("a1", "violin", role="melody")],
        ),
        (
            "decrease_texture_density",
            [_part("b1", "violin", role="melody")],
            [_part("a1", "violin", role="melody")],
        ),
        (
            "create_countermelody",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "flute", role="countermelody"),
            ],
        ),
        (
            "double_melody",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "viola", role="melody", doubling_policy="octave"),
            ],
        ),
    ],
)
def test_operation_matrix_success(operation, before, after):
    request = CompositionArrangementPreviewRequest.model_validate(
        _base_request(
            operation=operation,
            instrumentation=_instrumentation(before=before, after=after),
        )
    )
    assert request.operation == operation
    assert operation in ARRANGEMENT_OPERATIONS


@pytest.mark.parametrize(
    "operation,before,after,match",
    [
        (
            "change_instrumentation",
            [_part("b1", "violin", role="melody")],
            [_part("a1", "violin", role="melody")],
            "must differ",
        ),
        (
            "remove_accompaniment",
            [_part("b1", "violin", role="melody")],
            [_part("a1", "cello", role="bass")],
            "accompaniment-role",
        ),
        (
            "piano_to_ensemble",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "cello", role="bass"),
            ],
            "piano",
        ),
        (
            "piano_to_ensemble",
            [_part("b1", "acoustic_grand_piano", role="melody")],
            [_part("a1", "violin", role="melody")],
            "two distinct",
        ),
        (
            "create_countermelody",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "flute", role="harmony"),
            ],
            "countermelody",
        ),
        (
            "double_melody",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody"),
                _part("a2", "viola", role="melody", doubling_policy="none"),
            ],
            "exactly one authorized doubling",
        ),
        (
            "double_melody",
            [_part("b1", "violin", role="melody")],
            [
                _part("a1", "violin", role="melody", doubling_policy="unison"),
                _part("a2", "viola", role="melody", doubling_policy="octave"),
            ],
            "exactly one authorized doubling",
        ),
    ],
)
def test_operation_matrix_failure(operation, before, after, match):
    with pytest.raises(ValidationError, match=match):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(
                operation=operation,
                instrumentation=_instrumentation(before=before, after=after),
            )
        )


def test_remove_accompaniment_allows_bass_role():
    request = CompositionArrangementPreviewRequest.model_validate(
        _base_request(
            operation="remove_accompaniment",
            instrumentation=_instrumentation(
                before=[_part("b1", "acoustic_bass", role="bass")],
                after=[_part("a1", "violin", role="melody")],
            ),
        )
    )
    assert request.operation == "remove_accompaniment"


# --- Strict extras / uniqueness / inventories ----------------------------------


def test_request_rejects_extra_fields():
    with pytest.raises(ValidationError):
        CompositionArrangementPreviewRequest.model_validate(_base_request(mystery=True))


def test_draft_rejects_extra_fields():
    with pytest.raises(ValidationError):
        CompositionArrangementDraft.model_validate(
            {
                "parts": [{"action": "add", "part_id": "p1"}],
                "channel": 1,
            }
        )


def test_draft_rejects_gm_program_metadata():
    with pytest.raises(ValidationError):
        CompositionArrangementDraft.model_validate(
            {
                "parts": [
                    {
                        "action": "add",
                        "part_id": "p1",
                        "midi_program": 40,
                    }
                ]
            }
        )


def test_part_ids_unique_with_derived_aggregate_counts():
    instrumentation = ArrangementInstrumentationRequirements.model_validate(
        {
            "before": [
                _part("b1", "acoustic_grand_piano", role="melody"),
                _part("b2", "acoustic_grand_piano", role="harmony"),
            ],
            "after": [
                _part("a1", "violin", role="melody"),
                _part("a2", "cello", role="bass"),
                _part("a3", "violin", role="countermelody"),
            ],
        }
    )
    assert instrumentation.before_part_count == 2
    assert instrumentation.after_part_count == 3
    assert instrumentation.before_distinct_instrument_count == 1
    assert instrumentation.after_distinct_instrument_count == 2
    assert instrumentation.instrument_count("before", "acoustic_grand_piano") == 2
    assert instrumentation.role_count("after", "melody") == 1
    assert instrumentation.role_count("after", "countermelody") == 1


def test_duplicate_part_ids_rejected():
    with pytest.raises(ValidationError, match="duplicate part_id"):
        ArrangementInstrumentationRequirements.model_validate(
            {
                "before": [
                    _part("same", "violin"),
                    _part("same", "cello"),
                ],
                "after": [_part("a1", "violin")],
            }
        )


def test_source_and_protected_must_be_unique_and_disjoint():
    with pytest.raises(ValidationError, match="unique"):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(source_track_ids=["piano-1", "piano-1"])
        )
    with pytest.raises(ValidationError, match="intersect"):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(
                source_track_ids=["piano-1"],
                protected_track_ids=["piano-1"],
            )
        )


def test_unsupported_role_rejected():
    with pytest.raises(ValidationError, match="Unsupported track role"):
        ArrangementPartRequirement.model_validate(
            _part("p1", "violin", role="soloist")
        )


# --- Candidate / rejection bounds ----------------------------------------------


def test_candidate_count_bounds():
    with pytest.raises(ValidationError):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(candidate_count=0)
        )
    with pytest.raises(ValidationError):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(candidate_count=ARRANGEMENT_MAX_CANDIDATE_COUNT + 1)
        )


def test_rejected_attempt_never_carries_composition():
    attempt = ArrangementRejectedAttempt.model_validate(
        {
            "ordinal": 1,
            "stage": "range",
            "codes": ["arrangement_range_failed"],
            "reasons": ["absolute out of range on cello"],
        }
    )
    assert "composition" not in ArrangementRejectedAttempt.model_fields
    with pytest.raises(ValidationError):
        ArrangementRejectedAttempt.model_validate(
            {
                "ordinal": 1,
                "stage": "range",
                "codes": ["arrangement_range_failed"],
                "composition": minimal_v2(),
            }
        )


def test_rejected_attempt_bounds():
    with pytest.raises(ValidationError):
        ArrangementRejectedAttempt.model_validate(
            {"ordinal": 1, "stage": "draft_validation", "codes": []}
        )
    too_many = [
        ArrangementRejectedAttempt.model_validate(
            {
                "ordinal": 1,
                "stage": "provider",
                "codes": [f"code_{index}"],
            }
        )
        for index in range(ARRANGEMENT_MAX_REJECTED_ATTEMPTS + 1)
    ]
    with pytest.raises(ValidationError):
        CompositionArrangementPreviewResponse.model_validate(
            {
                "edit_source_fingerprint": "a" * 64,
                "catalog_fingerprint": "d" * 64,
                "operation": "change_instrumentation",
                "requested_candidate_count": 1,
                "candidates": [],
                "rejected_attempts": too_many,
                "provider": "fake",
            }
        )


# --- Text / context limits ----------------------------------------------------


def test_instruction_normalization_and_max_chars():
    request = CompositionArrangementPreviewRequest.model_validate(
        _base_request(instruction="  keep  the cello  ")
    )
    assert request.instruction == "keep the cello"
    with pytest.raises(ValidationError):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(instruction="x" * (ARRANGEMENT_MAX_INSTRUCTION_CHARS + 1))
        )


def test_options_context_budget_bounds():
    request = CompositionArrangementPreviewRequest.model_validate(
        _base_request(options={"context_budget_chars": 4000, "max_repairs": 2})
    )
    assert request.options.context_budget_chars == 4000
    with pytest.raises(ValidationError):
        CompositionArrangementPreviewRequest.model_validate(
            _base_request(options={"context_budget_chars": 100})
        )


def test_validate_arrangement_request_limits_rejects_huge_track_counts():
    request = CompositionArrangementPreviewRequest.model_validate(_base_request())
    oversized_tracks = [
        request.composition.tracks[0].model_copy(update={"id": f"t-{index}"})
        for index in range(65)
    ]
    request = request.model_copy(
        update={
            "composition": request.composition.model_copy(
                update={"tracks": oversized_tracks}
            )
        }
    )
    with pytest.raises(CompositionArrangementError) as exc:
        validate_arrangement_request_limits(request)
    assert exc.value.code == "arrangement_request_too_large"
    assert exc.value.http_status == 422


# --- Draft validation ----------------------------------------------------------


def test_draft_validates_relative_notes_and_refs():
    draft = CompositionArrangementDraft.model_validate(
        {
            "parts": [
                {
                    "action": "redistribute",
                    "part_id": "a1",
                    "source_track_ids": ["piano-1"],
                    "source_note_refs": ["n1", "n2"],
                    "notes": [
                        {
                            "pitch": "C4",
                            "relative_start_tick": 0,
                            "duration_ticks": 480,
                            "articulations": ["staccato"],
                            "tie": {"group_id": "t1", "type": "start"},
                        }
                    ],
                }
            ]
        }
    )
    assert draft.parts[0].notes[0].pitch == "C4"
    assert draft.parts[0].source_note_refs == ["n1", "n2"]


def test_draft_rejects_duplicate_part_ids_and_invalid_pitch():
    with pytest.raises(ValidationError, match="duplicate draft part_ids"):
        CompositionArrangementDraft.model_validate(
            {
                "parts": [
                    {"action": "add", "part_id": "p1"},
                    {"action": "retain", "part_id": "p1"},
                ]
            }
        )
    with pytest.raises(ValidationError, match="Invalid pitch"):
        CompositionArrangementDraft.model_validate(
            {
                "parts": [
                    {
                        "action": "add",
                        "part_id": "p1",
                        "notes": [
                            {
                                "pitch": "H4",
                                "relative_start_tick": 0,
                                "duration_ticks": 480,
                            }
                        ],
                    }
                ]
            }
        )


def test_draft_rejects_unknown_action():
    with pytest.raises(ValidationError):
        CompositionArrangementDraft.model_validate(
            {"parts": [{"action": "teleport", "part_id": "p1"}]}
        )


# --- Response / fingerprints / OpenAPI smoke -----------------------------------


def test_response_requires_unique_candidates_and_shared_fingerprints():
    left = _candidate(candidate_id="1" * 64)
    right = _candidate(candidate_id="1" * 64, candidate_fingerprint="f" * 64)
    with pytest.raises(ValidationError, match="unique"):
        CompositionArrangementPreviewResponse.model_validate(
            {
                "edit_source_fingerprint": "a" * 64,
                "catalog_fingerprint": "d" * 64,
                "operation": "change_instrumentation",
                "requested_candidate_count": 2,
                "candidates": [left, right],
                "provider": "fake",
            }
        )


def test_response_rejects_mismatched_catalog_fingerprint():
    with pytest.raises(ValidationError, match="catalog_fingerprint"):
        CompositionArrangementPreviewResponse.model_validate(
            {
                "edit_source_fingerprint": "a" * 64,
                "catalog_fingerprint": "d" * 64,
                "operation": "change_instrumentation",
                "requested_candidate_count": 1,
                "candidates": [_candidate(catalog_fingerprint="z" * 64)],
                "provider": "fake",
            }
        )


def test_response_accepts_partial_success_with_rejected_attempts():
    response = CompositionArrangementPreviewResponse.model_validate(
        {
            "edit_source_fingerprint": "a" * 64,
            "catalog_fingerprint": "d" * 64,
            "catalog_version": ARRANGEMENT_CATALOG_VERSION,
            "range_policy_version": ARRANGEMENT_RANGE_POLICY_VERSION,
            "algorithm_version": ARRANGEMENT_ALGORITHM_VERSION,
            "operation": "change_instrumentation",
            "requested_candidate_count": 2,
            "candidates": [_candidate()],
            "rejected_attempts": [
                {
                    "ordinal": 2,
                    "stage": "preservation",
                    "codes": ["arrangement_preservation_failed"],
                    "reasons": ["melody multiset mismatch"],
                }
            ],
            "provider": "fake",
            "warning_codes": ["candidate_partial_success"],
        }
    )
    assert len(response.candidates) == 1
    assert len(response.rejected_attempts) == 1
    assert response.candidates[0].catalog_version == ARRANGEMENT_CATALOG_VERSION
    assert response.candidates[0].target_profile_fingerprints[0].instrument_id == "violin"
    assert EDIT_FINGERPRINT_PROFILE == "composition.edit.v1"


def test_candidate_rejects_unknown_warning_code():
    with pytest.raises(ValidationError, match="Unknown arrangement warning"):
        ArrangementCandidate.model_validate(
            _candidate(warning_codes=["not_a_real_code"])
        )


def test_candidate_exposes_catalog_and_profile_fingerprint_fields():
    candidate = ArrangementCandidate.model_validate(_candidate())
    dumped = candidate.model_dump(mode="json")
    assert dumped["algorithm_version"] == ARRANGEMENT_ALGORITHM_VERSION
    assert dumped["catalog_version"] == ARRANGEMENT_CATALOG_VERSION
    assert dumped["range_policy_version"] == ARRANGEMENT_RANGE_POLICY_VERSION
    assert len(dumped["catalog_fingerprint"]) >= 16
    assert dumped["target_profile_fingerprints"][0]["profile_fingerprint"]
    assert dumped["edit_source_fingerprint"]
    assert dumped["candidate_fingerprint"]
    assert "manifest" in dumped
    assert "assertions" in dumped


def test_error_and_warning_code_registries_stable():
    assert "arrangement_candidate_exhausted" in ARRANGEMENT_ERROR_CODES
    assert "questionable_range" in ARRANGEMENT_WARNING_CODES
    err = CompositionArrangementError(
        "arrangement_provider_unavailable",
        http_status=503,
        details={"provider": "openai"},
    )
    assert err.code == "arrangement_provider_unavailable"
    assert sorted(err.details.keys()) == ["provider"]


def test_openapi_compatible_serialization_roundtrip():
    request = CompositionArrangementPreviewRequest.model_validate(
        _base_request(
            selection={"provider": "fake", "model": "fake-arr"},
            options={"max_repairs": 2, "context_budget_chars": 4000},
            instruction="  soft strings  ",
            range_adjustment="octave_shift_unprotected",
        )
    )
    dumped = request.model_dump(mode="json")
    again = CompositionArrangementPreviewRequest.model_validate(dumped)
    assert again.operation == "change_instrumentation"
    assert again.instruction == "soft strings"
    assert again.range_adjustment == "octave_shift_unprotected"
    assert again.options.max_repairs == 2

    schema = CompositionArrangementPreviewRequest.model_json_schema()
    assert "properties" in schema
    assert "operation" in schema["properties"]

    response_schema = CompositionArrangementPreviewResponse.model_json_schema()
    assert "catalog_fingerprint" in response_schema["properties"]
    assert "rejected_attempts" in response_schema["properties"]
    assert "candidates" in response_schema["properties"]


def test_defaults_preserve_melody_harmony_and_reject_range():
    request = CompositionArrangementPreviewRequest.model_validate(_base_request())
    assert request.preserve_melody is True
    assert request.preserve_harmony is True
    assert request.range_adjustment == "reject"
    assert request.allow_unlisted_after is False
