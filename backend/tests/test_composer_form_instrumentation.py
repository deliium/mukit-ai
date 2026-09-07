"""Regression tests for plan_form instrumentation coercion."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.services.composition_planner import (
    ComposerFormPlan,
    coerce_instrumentation_labels,
)
from app.services.llm_music_generator import _parse_form_plan


def _valid_form_payload(**overrides):
    payload = {
        "tempo": 90,
        "key": "C major",
        "time_signature": "4/4",
        "bar_count": 8,
        "sections": [
            {"type": "intro", "start_bar": 1, "bar_count": 4},
            {"type": "outro", "start_bar": 5, "bar_count": 4},
        ],
        "instrumentation": ["piano", "bass", "violin"],
    }
    payload.update(overrides)
    return payload


def test_coerce_instrumentation_extracts_family_from_objects():
    labels = coerce_instrumentation_labels(
        [
            {"family": "piano", "role": "melody"},
            {"family": "bass", "role": "bass"},
            {"instrument": "violin", "role": "pad"},
        ]
    )
    assert labels == ["piano", "bass", "violin"]


def test_coerce_instrumentation_preserves_strings_and_dedupes():
    labels = coerce_instrumentation_labels(["Piano", {"family": "piano"}, "bass", "Bass"])
    assert labels == ["Piano", "bass"]


def test_composer_form_plan_accepts_object_shaped_instrumentation():
    form = ComposerFormPlan.model_validate(
        _valid_form_payload(
            instrumentation=[
                {"family": "piano", "role": "melody"},
                {"family": "bass", "role": "bass"},
                {"family": "violin", "role": "harmony"},
            ]
        )
    )
    assert form.instrumentation == ["piano", "bass", "violin"]


def test_composer_form_plan_rejects_objects_without_family():
    with pytest.raises(ValidationError):
        ComposerFormPlan.model_validate(
            _valid_form_payload(instrumentation=[{"role": "melody"}])
        )


def test_parse_form_plan_logs_coercion(caplog):
    payload = _valid_form_payload(
        instrumentation=[
            {"family": "piano", "role": "melody"},
            {"family": "bass", "role": "bass"},
            {"family": "violin", "role": "pad"},
        ]
    )
    with caplog.at_level(logging.INFO, logger="app.services.llm_music_generator"):
        form = _parse_form_plan(payload, {})
    assert form.instrumentation == ["piano", "bass", "violin"]
    assert "[FIX] Coerced plan_form instrumentation objects to family strings" in caplog.text
