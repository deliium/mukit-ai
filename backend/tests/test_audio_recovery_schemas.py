"""Schema accept/reject tests for audio.recovery.*.v1 contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.audio_recovery_schemas import (
    AUDIO_RECOVERY_PREVIEW_SCHEMA_VERSION,
    AUDIO_RECOVERY_RESULT_SCHEMA_VERSION,
    AudioRecoveryBindRequestV1,
    AudioRecoveryOverlayEntry,
    AudioRecoveryPreviewNote,
    AudioRecoveryPreviewV1,
    AudioRecoveryResultV1,
    AudioRecoveryScaffolding,
    AudioRecoveryStemV1,
)


def _scaffolding(**overrides) -> dict:
    base = {
        "tempo_bpm": 120,
        "tempo_confidence": 0.8,
        "tempo_source": "estimated",
        "meter": "4/4",
        "beat_grid": {
            "downbeat_offset_seconds": 0.0,
            "ticks_per_quarter": 480,
            "confidence": 0.75,
        },
        "structure": [
            {
                "label": "A",
                "start_tick": 0,
                "end_tick": 1920,
                "confidence": 0.7,
            }
        ],
        "key": {"tonic": "C", "mode": "major", "confidence": 0.6},
        "harmony": [
            {
                "symbol": "C",
                "start_tick": 0,
                "end_tick": 1920,
                "confidence": 0.55,
            }
        ],
    }
    base.update(overrides)
    return base


def _minimal_preview(**overrides) -> AudioRecoveryPreviewV1:
    note = {
        "provisional_id": "p1",
        "stem": "melody",
        "pitch": 60,
        "start_tick": 0,
        "duration_ticks": 480,
        "velocity": 80,
        "confidence": 0.9,
    }
    base = {
        "preview_fingerprint": "fp_deadbeef01",
        "stems": [
            {
                "stem": "melody",
                "engine_id": "fake:audio-mono",
                "notes": [note],
                "issues": [],
            }
        ],
        "notes": [note],
        "scaffolding": _scaffolding(),
        "issues": [],
        "summary": {
            "note_count": 1,
            "low_confidence_count": 0,
            "excluded_low_confidence_count": 0,
            "include_threshold": 0.5,
            "stem_count": 1,
            "separation_status": "complete",
        },
        "engine": {
            "id": "fake:audio-recovery",
            "separation_engine_id": "fake:stems",
            "version": "1",
            "fake": True,
        },
    }
    base.update(overrides)
    return AudioRecoveryPreviewV1.model_validate(base)


def test_preview_accepts_minimal_valid_document() -> None:
    preview = _minimal_preview()
    assert preview.schema_version == AUDIO_RECOVERY_PREVIEW_SCHEMA_VERSION
    assert preview.playable is False
    assert preview.notes[0].confidence == 0.9
    assert preview.scaffolding.tempo_bpm == 120


def test_preview_note_rejects_extra_and_out_of_range_confidence() -> None:
    with pytest.raises(ValidationError):
        AudioRecoveryPreviewNote(
            provisional_id="p1",
            stem="melody",
            pitch=60,
            start_tick=0,
            duration_ticks=480,
            confidence=1.5,
        )
    with pytest.raises(ValidationError):
        AudioRecoveryPreviewNote.model_validate(
            {
                "provisional_id": "p1",
                "stem": "melody",
                "pitch": 60,
                "start_tick": 0,
                "duration_ticks": 480,
                "confidence": 0.5,
                "articulation": "staccato",
            }
        )


def test_preview_rejects_duplicate_provisional_ids() -> None:
    note_a = {
        "provisional_id": "dup",
        "stem": "melody",
        "pitch": 60,
        "start_tick": 0,
        "duration_ticks": 480,
        "confidence": 0.8,
    }
    note_b = {
        "provisional_id": "dup",
        "stem": "melody",
        "pitch": 62,
        "start_tick": 480,
        "duration_ticks": 480,
        "confidence": 0.7,
    }
    with pytest.raises(ValidationError):
        _minimal_preview(notes=[note_a, note_b], stems=[])


def test_stem_rejects_mismatched_note_stem() -> None:
    with pytest.raises(ValidationError):
        AudioRecoveryStemV1.model_validate(
            {
                "stem": "bass",
                "engine_id": "fake:audio-mono",
                "notes": [
                    {
                        "provisional_id": "p1",
                        "stem": "melody",
                        "pitch": 36,
                        "start_tick": 0,
                        "duration_ticks": 480,
                        "confidence": 0.8,
                    }
                ],
            }
        )


def test_scaffolding_rejects_invalid_structure_span() -> None:
    with pytest.raises(ValidationError):
        AudioRecoveryScaffolding.model_validate(
            _scaffolding(
                structure=[
                    {
                        "label": "A",
                        "start_tick": 100,
                        "end_tick": 50,
                        "confidence": 0.5,
                    }
                ]
            )
        )


def test_bind_requires_project_and_rejects_duplicate_map() -> None:
    bind = AudioRecoveryBindRequestV1.model_validate(
        {
            "project_id": "proj_1",
            "preview_fingerprint": "fp_deadbeef01",
            "event_map": [
                {
                    "provisional_id": "p1",
                    "event_id": "ev1",
                    "track_id": "tr_melody",
                }
            ],
        }
    )
    assert bind.project_id == "proj_1"
    with pytest.raises(ValidationError):
        AudioRecoveryBindRequestV1.model_validate(
            {
                "preview_fingerprint": "fp_deadbeef01",
                "event_map": [],
            }
        )
    with pytest.raises(ValidationError):
        AudioRecoveryBindRequestV1.model_validate(
            {
                "project_id": "proj_1",
                "preview_fingerprint": "fp_deadbeef01",
                "event_map": [
                    {
                        "provisional_id": "p1",
                        "event_id": "ev1",
                        "track_id": "t1",
                    },
                    {
                        "provisional_id": "p1",
                        "event_id": "ev2",
                        "track_id": "t1",
                    },
                ],
            }
        )


def test_result_overlay_rejects_extra_and_duplicate_event_ids() -> None:
    with pytest.raises(ValidationError):
        AudioRecoveryOverlayEntry.model_validate(
            {
                "event_id": "e1",
                "track_id": "t1",
                "confidence": 0.8,
                "stem": "melody",
                "status": "recovered",
                "secret": True,
            }
        )
    result_payload = {
        "job_id": "job_1",
        "project_id": "proj_1",
        "preview_fingerprint": "fp_deadbeef01",
        "source_audio_asset_id": "asset_src",
        "scaffolding": _scaffolding(),
        "overlay": [
            {
                "event_id": "e1",
                "track_id": "t1",
                "confidence": 0.9,
                "stem": "melody",
                "status": "recovered",
            },
            {
                "event_id": "e1",
                "track_id": "t1",
                "confidence": 0.5,
                "stem": "melody",
                "status": "user_edited",
            },
        ],
        "issues": [],
        "engine": {"id": "fake:audio-recovery", "fake": True},
    }
    with pytest.raises(ValidationError):
        AudioRecoveryResultV1.model_validate(result_payload)
    result_payload["overlay"] = [result_payload["overlay"][0]]
    result = AudioRecoveryResultV1.model_validate(result_payload)
    assert result.schema_version == AUDIO_RECOVERY_RESULT_SCHEMA_VERSION
    assert result.playable is False
