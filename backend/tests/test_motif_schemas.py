"""Motif apply API schema validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.motif_schemas import (
    MotifApplyError,
    MotifApplyRequest,
    MotifDestinationSelector,
    MotifOperationParameters,
    MotifSourceSelector,
    validate_motif_apply_request_limits,
)
from tests.test_composition_v2_schema import _motif_definition, _motif_source_events, _motif_track, minimal_v2


def _apply_payload(**overrides):
    composition = CompositionV2.model_validate(
        minimal_v2(
            bar_count=4,
            duration_ticks=7680,
            sections=[
                {
                    "id": "verse",
                    "type": "verse",
                    "start_bar": 1,
                    "bar_count": 2,
                    "start_tick": 0,
                    "duration_ticks": 3840,
                },
                {
                    "id": "chorus",
                    "type": "chorus",
                    "start_bar": 3,
                    "bar_count": 2,
                    "start_tick": 3840,
                    "duration_ticks": 3840,
                },
            ],
            tracks=[_motif_track()],
            motifs=[_motif_definition()],
        )
    )
    payload = {
        "composition": composition,
        "source": {"motif_id": "motif-a", "occurrence_id": "occ-orig"},
        "destination": {
            "section_id": "chorus",
            "track_id": "melody-1",
            "start_bar": 3,
        },
        "operation": "repeat",
        "parameters": {},
    }
    payload.update(overrides)
    return payload


def test_motif_apply_request_accepts_mechanical_repeat():
    request = MotifApplyRequest.model_validate(_apply_payload())
    assert request.operation == "repeat"
    assert request.variation_strength is None
    assert request.selection.provider is None


def test_motif_apply_request_requires_strength_for_creative():
    with pytest.raises(ValidationError, match="variation_strength"):
        MotifApplyRequest.model_validate(
            _apply_payload(
                operation="melodic_variation",
                selection={"provider": "openai", "model": "test-model"},
            )
        )


def test_motif_apply_request_requires_llm_selection_for_creative():
    with pytest.raises(ValidationError, match="LLM provider or model"):
        MotifApplyRequest.model_validate(
            _apply_payload(
                operation="melodic_variation",
                variation_strength=0.5,
            )
        )


def test_motif_apply_request_rejects_llm_selection_for_mechanical():
    with pytest.raises(ValidationError, match="LLM selection must be omitted"):
        MotifApplyRequest.model_validate(
            _apply_payload(selection={"provider": "openai", "model": "test-model"})
        )


def test_motif_apply_request_requires_transpose_semitones():
    with pytest.raises(ValidationError, match="transpose_semitones"):
        MotifApplyRequest.model_validate(_apply_payload(operation="transpose"))


def test_motif_apply_request_rejects_unknown_destination_track():
    with pytest.raises(ValidationError, match="destination.track_id"):
        MotifApplyRequest.model_validate(
            _apply_payload(destination={"section_id": "chorus", "track_id": "missing", "start_bar": 3})
        )


def test_motif_apply_request_rejects_extra_parameters_for_repeat():
    with pytest.raises(ValidationError, match="Unexpected parameters"):
        MotifApplyRequest.model_validate(
            _apply_payload(parameters={"transpose_semitones": 2})
        )


def test_validate_motif_apply_request_limits_rejects_oversized_event_count(monkeypatch):
    monkeypatch.setattr("app.motif_schemas.MOTIF_APPLY_MAX_EVENT_COUNT", 3)
    request = MotifApplyRequest.model_validate(_apply_payload())
    with pytest.raises(MotifApplyError, match="event count") as exc_info:
        validate_motif_apply_request_limits(request)
    assert exc_info.value.code == "motif_request_too_large"
    assert exc_info.value.details["limit"] == 3


def test_motif_operation_parameters_rejects_partial_time_scale():
    with pytest.raises(ValidationError, match="time_scale"):
        MotifOperationParameters.model_validate({"time_scale_numerator": 2})


def test_motif_destination_selector_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        MotifDestinationSelector.model_validate(
            {"track_id": "melody-1", "start_bar": 1, "extra": True}
        )


def test_motif_source_selector_requires_ids():
    with pytest.raises(ValidationError):
        MotifSourceSelector.model_validate({"motif_id": "", "occurrence_id": "occ-orig"})
