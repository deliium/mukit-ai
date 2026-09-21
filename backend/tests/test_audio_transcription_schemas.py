"""Schema accept/reject tests for transcription.preview.v1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.audio_transcription_schemas import (
    TRANSCRIPTION_PREVIEW_SCHEMA_VERSION,
    TranscriptionPreviewEngine,
    TranscriptionPreviewNote,
    TranscriptionPreviewSummary,
    TranscriptionPreviewTiming,
    TranscriptionPreviewV1,
)


def _minimal_preview(**overrides) -> TranscriptionPreviewV1:
    base = {
        "notes": [
            {
                "provisional_id": "p1",
                "pitch": 60,
                "start_tick": 0,
                "duration_ticks": 480,
                "velocity": 80,
                "confidence": 0.9,
            }
        ],
        "issues": [],
        "summary": {
            "note_count": 1,
            "low_confidence_count": 0,
            "excluded_low_confidence_count": 0,
            "include_threshold": 0.5,
        },
        "timing": {
            "tempo_bpm": 120,
            "ticks_per_quarter": 480,
            "origin_tick": 0,
            "tempo_source": "provided",
            "meter": "4/4",
        },
        "engine": {"id": "fake:audio-mono", "version": "1", "fake": True},
    }
    base.update(overrides)
    return TranscriptionPreviewV1.model_validate(base)


def test_preview_accepts_minimal_valid_document() -> None:
    preview = _minimal_preview()
    assert preview.schema_version == TRANSCRIPTION_PREVIEW_SCHEMA_VERSION
    assert preview.notes[0].confidence == 0.9
    assert preview.engine.fake is True


def test_preview_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        TranscriptionPreviewNote(
            provisional_id="p1",
            pitch=60,
            start_tick=0,
            duration_ticks=480,
            confidence=1.5,
        )


def test_preview_rejects_extra_fields_on_notes() -> None:
    with pytest.raises(ValidationError):
        TranscriptionPreviewNote.model_validate(
            {
                "provisional_id": "p1",
                "pitch": 60,
                "start_tick": 0,
                "duration_ticks": 480,
                "confidence": 0.5,
                "articulation": "staccato",
            }
        )


def test_preview_rejects_duplicate_provisional_ids() -> None:
    with pytest.raises(ValidationError):
        _minimal_preview(
            notes=[
                {
                    "provisional_id": "dup",
                    "pitch": 60,
                    "start_tick": 0,
                    "duration_ticks": 480,
                    "confidence": 0.8,
                },
                {
                    "provisional_id": "dup",
                    "pitch": 62,
                    "start_tick": 480,
                    "duration_ticks": 480,
                    "confidence": 0.7,
                },
            ]
        )


def test_summary_and_timing_reject_extra() -> None:
    with pytest.raises(ValidationError):
        TranscriptionPreviewSummary.model_validate(
            {
                "note_count": 0,
                "low_confidence_count": 0,
                "excluded_low_confidence_count": 0,
                "include_threshold": 0.5,
                "secret": True,
            }
        )
    with pytest.raises(ValidationError):
        TranscriptionPreviewTiming.model_validate(
            {
                "tempo_bpm": 120,
                "ticks_per_quarter": 480,
                "tempo_source": "defaulted",
                "extra_map": {},
            }
        )
    engine = TranscriptionPreviewEngine(id="librosa_pyin")
    assert engine.fake is False
