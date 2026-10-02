"""Schema contracts for preference documents."""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from app.preference_schemas import PreferenceChoiceV1, PreferenceLearningError


def _vector(fill: float = 0.0) -> list[float]:
    return [fill] * 16


def _row(label: str, index: int, *, chosen: bool) -> dict:
    return {
        "candidate_id": f"{label}_candidate_id1",
        "candidate_fingerprint": "abcdef0123456789",
        "original_index": index,
        "feature_vector": _vector(),
        "chosen": chosen,
    }


def _choice(**overrides) -> dict:
    payload = {
        "id": "pref_0123456789abcdef",
        "context": {
            "surface": "development",
            "operation": "continue",
            "project_id": "p" * 80,
            "source_fingerprint": "b" * 16,
            "request_digest": "a" * 64,
        },
        "candidates": [
            _row("sparse", 0, chosen=False),
            _row("middle", 1, chosen=False),
            _row("dense", 2, chosen=True),
        ],
        "chosen_candidate_id": "dense_candidate_id1",
        "created_at": "2026-10-02T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_choice_accepts_dense_among_three_rhythms() -> None:
    choice = PreferenceChoiceV1.model_validate(_choice())
    assert choice.chosen_candidate_id == "dense_candidate_id1"
    assert choice.context.project_id is not None
    assert len(choice.context.project_id) == 80
    assert [item.candidate_id for item in choice.candidates] == [
        "sparse_candidate_id1",
        "middle_candidate_id1",
        "dense_candidate_id1",
    ]
    assert sum(1 for item in choice.candidates if item.chosen) == 1


def test_choice_rejects_a_second_chosen_flag() -> None:
    payload = _choice()
    payload["candidates"][0]["chosen"] = True
    with pytest.raises(ValidationError, match="preference_invalid"):
        PreferenceChoiceV1.model_validate(payload)


def test_choice_rejects_feature_vector_length() -> None:
    payload = _choice()
    payload["candidates"][2]["feature_vector"] = [0.0] * 15
    with pytest.raises(ValidationError, match="preference_invalid"):
        PreferenceChoiceV1.model_validate(payload)


@pytest.mark.parametrize("element", [1.01, -0.01])
def test_choice_rejects_feature_element_outside_unit_interval(element: float) -> None:
    payload = _choice()
    payload["candidates"][1]["feature_vector"] = [element] + [0.0] * 15
    with pytest.raises(ValidationError, match="preference_invalid"):
        PreferenceChoiceV1.model_validate(payload)


def test_choice_rejects_non_finite_feature_element() -> None:
    payload = _choice()
    payload["candidates"][1]["feature_vector"] = [math.nan] + [0.0] * 15
    with pytest.raises(ValidationError, match="preference_invalid"):
        PreferenceChoiceV1.model_validate(payload)


def test_choice_rejects_genre_key() -> None:
    payload = _choice()
    payload["genre"] = "waltz"
    with pytest.raises(ValidationError, match="preference_forbidden_payload"):
        PreferenceChoiceV1.model_validate(payload)


def test_choice_rejects_events_key() -> None:
    payload = _choice()
    payload["events"] = []
    with pytest.raises(ValidationError, match="preference_forbidden_payload"):
        PreferenceChoiceV1.model_validate(payload)


def test_choice_rejects_unknown_surface() -> None:
    payload = _choice()
    payload["context"]["surface"] = "generate"
    with pytest.raises(ValidationError):
        PreferenceChoiceV1.model_validate(payload)


def test_forbidden_payload_error_code() -> None:
    with pytest.raises(PreferenceLearningError) as exc:
        from app.preference_schemas import reject_preference_forbidden_payload

        reject_preference_forbidden_payload({"genre": "jazz"}, model_name="PreferenceChoiceV1")
    assert exc.value.code == "preference_forbidden_payload"
    assert exc.value.http_status == 422
