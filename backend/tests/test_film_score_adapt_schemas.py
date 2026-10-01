"""Accept and reject tests for film.score.adaptation.v1."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.film_score_adapt_schemas import (
    FilmScoreAdaptPreviewRequest,
    FilmScoreAdaptationV1,
)


def _proposal(**overrides) -> dict:
    base = {
        "schema_version": "film.score.adaptation.v1",
        "project_id": "proj_adapt",
        "source_fingerprint": "a" * 64,
        "scoring_document_revision": 1,
        "previous_timeline_fingerprint": "b" * 64,
        "operations": [
            {
                "op_id": "edit_0123abcd",
                "kind": "delete_span",
                "strategy": "phrase_contract",
                "start_bar": 9,
                "end_bar": 11,
                "bars_delta": -3,
                "tempo_bpm": None,
                "section_id": "sec_b",
                "reason_code": "picture_shorten",
            }
        ],
        "hit_changes": [
            {
                "cue_id": "hit_bbbb0002",
                "change": "moved",
                "previous_seconds": 36,
                "next_seconds": 30,
                "status": "aligned",
            }
        ],
        "preserved": {
            "motifs": True,
            "melodies": True,
            "harmony": True,
            "climax": True,
            "instrumentation": True,
        },
        "counts": {
            "events_unchanged": 8,
            "events_shifted": 21,
            "events_removed": 3,
            "events_added": 0,
        },
        "warnings": [],
        "committed": False,
    }
    base.update(overrides)
    return base


def _previous() -> dict:
    return {
        "duration_seconds": 64,
        "frame_rate_numerator": 24,
        "frame_rate_denominator": 1,
        "video_origin_seconds": 0,
        "musical_origin_tick": 0,
        "cues": [
            {
                "id": "hit_aaaa0001",
                "kind": "hit_point",
                "importance": "critical",
                "video_seconds": 6,
                "tolerance_frames": 0,
            }
        ],
    }


def test_proposal_accepts_phrase_contract() -> None:
    proposal = FilmScoreAdaptationV1.model_validate(_proposal())
    assert proposal.committed is False
    assert proposal.operations[0].strategy == "phrase_contract"
    assert proposal.warnings == []


def test_proposal_rejects_playable_keys_and_unknown_strategy() -> None:
    with pytest.raises(ValidationError):
        FilmScoreAdaptationV1.model_validate(_proposal(tracks=[]))
    with pytest.raises(ValidationError):
        FilmScoreAdaptationV1.model_validate(_proposal(events=[]))
    body = _proposal()
    body["operations"][0]["strategy"] = "replace_score"
    with pytest.raises(ValidationError):
        FilmScoreAdaptationV1.model_validate(body)


def test_request_rejects_extra_edit_and_bad_span_and_replace_existing(caplog) -> None:
    edits = [
        {
            "op_id": f"edit_{index:08x}",
            "kind": "delete_span",
            "start_seconds": 1,
            "end_seconds": 2,
        }
        for index in range(17)
    ]
    with pytest.raises(ValidationError):
        FilmScoreAdaptPreviewRequest.model_validate(
            {
                "previous": _previous(),
                "edits": edits,
                "expected_source_fingerprint": "abc",
            }
        )
    with pytest.raises(ValidationError):
        FilmScoreAdaptPreviewRequest.model_validate(
            {
                "previous": _previous(),
                "edits": [
                    {
                        "op_id": "edit_0123abcd",
                        "kind": "delete_span",
                        "start_seconds": 4,
                        "end_seconds": 4,
                    }
                ],
                "expected_source_fingerprint": "abc",
            }
        )
    caplog.set_level(logging.DEBUG)
    with pytest.raises(ValidationError):
        FilmScoreAdaptPreviewRequest.model_validate(
            {
                "previous": _previous(),
                "edits": [
                    {
                        "op_id": "edit_0123abcd",
                        "kind": "delete_span",
                        "start_seconds": 1,
                        "end_seconds": 2,
                    }
                ],
                "expected_source_fingerprint": "abc",
                "replace_existing": True,
            }
        )
    assert any(
        "film adapt model rejected" in record.message and "error_code=film_adapt_invalid" in record.message
        for record in caplog.records
    )
