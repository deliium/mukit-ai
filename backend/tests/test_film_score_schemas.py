"""Accept and reject tests for film.score.plan.v1."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.film_score_schemas import (
    FilmScorePlanV1,
    FilmScorePreviewRequest,
)


def _plan(**overrides) -> dict:
    base = {
        "schema_version": "film.score.plan.v1",
        "project_id": "proj_film",
        "source_fingerprint": "abc123fingerprint",
        "scoring_document_revision": 1,
        "target_duration_seconds": 16.0,
        "music_start_seconds": 0.0,
        "music_end_seconds": 16.0,
        "sync_origin": {"video_origin_seconds": 0.0, "musical_origin_tick": 0},
        "time_signature": "4/4",
        "key": "C major",
        "root_tempo": 120,
        "tempo_min": 96,
        "tempo_max": 132,
        "sections": [
            {
                "id": "sec_01",
                "label": "Opening",
                "start_bar": 1,
                "bar_count": 8,
                "density": "moderate",
                "tempo_bpm": 120,
                "start_video_seconds": 0.0,
                "end_video_seconds": 16.0,
            }
        ],
        "tempo_strategy": {"policy": "section_boundary", "changes": []},
        "harmonic_arc": [],
        "motif_appearances": [],
        "hit_alignments": [
            {
                "cue_id": "hit_0123abcd",
                "kind": "hit_point",
                "importance": "critical",
                "status": "aligned",
                "target_bar": 1,
                "delta_seconds": 0.0,
                "tempo_change_added": False,
            }
        ],
        "density_regions": [],
        "agent_sequence": [],
        "warnings": [],
        "committed": False,
    }
    base.update(overrides)
    return base


def test_plan_accepts_one_section_empty_changes_and_aligned_hit() -> None:
    plan = FilmScorePlanV1.model_validate(_plan())
    assert plan.schema_version == "film.score.plan.v1"
    assert plan.tempo_strategy.changes == []
    assert plan.hit_alignments[0].status == "aligned"
    assert plan.committed is False


def test_request_defaults_omit_motifs_and_strength() -> None:
    request = FilmScorePreviewRequest.model_validate(
        {"brief": "Score the chase", "instruments": ["piano"]}
    )
    assert request.motif_ids == []
    assert request.profile_strength == "off"
    assert request.tempo_min == 96
    assert request.tempo_max == 132
    assert request.replace_existing is False


def test_plan_rejects_tracks_and_events(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValidationError):
            FilmScorePlanV1.model_validate(_plan(tracks=[]))
        with pytest.raises(ValidationError):
            FilmScorePlanV1.model_validate(_plan(events=[]))
    assert "film_score_invalid" in caplog.text
    assert "Score the chase" not in caplog.text


def test_plan_rejects_ninth_tempo_change() -> None:
    changes = [
        {"tick": 1920 * (index + 1), "bpm": 120, "section_id": "sec_01", "reason_code": "phrase_fit"}
        for index in range(9)
    ]
    with pytest.raises(ValidationError):
        FilmScorePlanV1.model_validate(_plan(tempo_strategy={"policy": "section_boundary", "changes": changes}))


def test_plan_rejects_33rd_section() -> None:
    sections = [
        {
            "id": f"sec_{index:02d}",
            "label": "Phrase",
            "start_bar": 1,
            "bar_count": 8,
            "density": "moderate",
            "tempo_bpm": 120,
            "start_video_seconds": 0.0,
            "end_video_seconds": 16.0,
        }
        for index in range(33)
    ]
    with pytest.raises(ValidationError):
        FilmScorePlanV1.model_validate(_plan(sections=sections))


def test_plan_rejects_unknown_hit_status() -> None:
    payload = _plan()
    payload["hit_alignments"][0]["status"] = "late"
    with pytest.raises(ValidationError):
        FilmScorePlanV1.model_validate(payload)


def test_tempo_min_above_tempo_max_rejected() -> None:
    with pytest.raises(ValidationError):
        FilmScorePlanV1.model_validate(_plan(tempo_min=140, tempo_max=100))
    with pytest.raises(ValidationError):
        FilmScorePreviewRequest.model_validate(
            {
                "brief": "Score the chase",
                "instruments": ["piano"],
                "tempo_min": 140,
                "tempo_max": 96,
            }
        )


def test_brief_of_2001_characters_rejected(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValidationError):
            FilmScorePreviewRequest.model_validate(
                {"brief": "a" * 2001, "instruments": ["piano"]}
            )
    assert "film_score_invalid" in caplog.text
    assert "a" * 40 not in caplog.text
