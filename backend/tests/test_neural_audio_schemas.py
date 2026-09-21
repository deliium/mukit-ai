"""Schema accept/reject tests for neural_audio_render.job.v1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.neural_audio_schemas import (
    NEURAL_AUDIO_JOB_SCHEMA_VERSION,
    NeuralAudioEnqueueRequest,
    NeuralAudioJobResponse,
)
from tests.test_composition_v2_schema import minimal_v2


def test_enqueue_accepts_composition_body() -> None:
    req = NeuralAudioEnqueueRequest.model_validate(
        {
            "composition": minimal_v2(),
            "instructions": "warm strings",
            "genre": "chamber",
            "mood": "calm",
        }
    )
    assert req.composition is not None
    assert req.instructions == "warm strings"


def test_enqueue_accepts_project_revision() -> None:
    req = NeuralAudioEnqueueRequest.model_validate(
        {
            "project_id": "proj-1",
            "source_revision_id": "rev-1",
            "instructions": "bright",
        }
    )
    assert req.project_id == "proj-1"
    assert req.source_revision_id == "rev-1"


def test_enqueue_rejects_missing_source() -> None:
    with pytest.raises(ValidationError):
        NeuralAudioEnqueueRequest.model_validate({"instructions": "x"})


def test_job_response_never_mutates_composition_flag() -> None:
    job = NeuralAudioJobResponse.model_validate(
        {
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "source_fingerprint": "abc123",
            "status": "queued",
            "model_id": "fake:neural-audio",
            "adapter_kind": "text_prompt",
            "fidelity_class": "generative",
            "fidelity_label": "Generative AI (not note-perfect)",
            "created_at": "2026-09-21T00:00:00Z",
        }
    )
    assert job.schema_version == NEURAL_AUDIO_JOB_SCHEMA_VERSION
    assert job.mutates_composition is False
