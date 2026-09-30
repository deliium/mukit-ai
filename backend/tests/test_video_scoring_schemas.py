"""Accept and reject tests for video.asset.v1 and video.scoring.v1."""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from app.video_scoring_schemas import (
    VideoAssetV1,
    VideoScoringPersistedV1,
    VideoScoringV1,
)


def _asset(**overrides) -> dict:
    base = {
        "schema_version": "video.asset.v1",
        "asset_id": "vid_0123abcd",
        "project_id": "proj_picture",
        "container": "mp4",
        "content_type": "video/mp4",
        "byte_size": 128,
        "sha256_prefix": "abcdef0123456789",
        "duration_seconds": 2.0,
        "frame_rate_numerator": 24,
        "frame_rate_denominator": 1,
        "has_audio": False,
        "width": 320,
        "height": 180,
        "created_at": "2026-09-30T12:00:00Z",
    }
    base.update(overrides)
    return base


def _scoring(**overrides) -> dict:
    base = {
        "schema_version": "video.scoring.v1",
        "project_id": "proj_picture",
        "asset_id": "vid_0123abcd",
        "frame_rate_numerator": 24,
        "frame_rate_denominator": 1,
        "frame_rate_source": "explicit",
        "timecode_mode": "non_drop",
        "start_timecode": "00:00:00:00",
        "video_origin_seconds": 0,
        "musical_origin_tick": 0,
        "hit_points": [
            {
                "id": "hit_0123abcd",
                "label": "Cut to street",
                "video_seconds": 1.5,
                "musical_tick": 480,
            }
        ],
        "document_revision": 1,
    }
    base.update(overrides)
    return base


def test_golden_pair_accepts() -> None:
    asset = VideoAssetV1.model_validate(_asset())
    scoring = VideoScoringV1.model_validate(_scoring())
    assert asset.schema_version == "video.asset.v1"
    assert asset.width == 320
    assert scoring.schema_version == "video.scoring.v1"
    assert scoring.hit_points[0].label == "Cut to street"
    persisted = VideoScoringPersistedV1.model_validate(_scoring())
    assert persisted.document_revision == 1


def test_scoring_accepts_null_frame_rate_fields() -> None:
    doc = VideoScoringV1.model_validate(
        _scoring(
            asset_id=None,
            frame_rate_numerator=None,
            frame_rate_denominator=None,
            frame_rate_source=None,
            hit_points=[],
            document_revision=0,
        )
    )
    assert doc.frame_rate_numerator is None
    assert doc.frame_rate_source is None
    assert doc.document_revision == 0


def test_persisted_revision_zero_is_rejected() -> None:
    with pytest.raises(ValidationError):
        VideoScoringPersistedV1.model_validate(_scoring(document_revision=0))


def test_unknown_frame_rate_is_rejected(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValidationError) as exc:
            VideoScoringV1.model_validate(
                _scoring(frame_rate_numerator=23, frame_rate_denominator=1)
            )
    assert "video_frame_rate_unsupported" in str(exc.value)
    assert any(
        getattr(record, "error_code", None) == "video_frame_rate_unsupported"
        for record in caplog.records
    )


def test_drop_frame_at_24_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(_scoring(timecode_mode="drop_frame"))
    assert "video_drop_frame_unsupported" in str(exc.value)


def test_drop_frame_with_null_rate_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(
            _scoring(
                frame_rate_numerator=None,
                frame_rate_denominator=None,
                frame_rate_source=None,
                timecode_mode="drop_frame",
            )
        )
    assert "video_drop_frame_unsupported" in str(exc.value)


def test_source_without_rate_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(
            _scoring(
                frame_rate_numerator=None,
                frame_rate_denominator=None,
                frame_rate_source="explicit",
            )
        )
    assert "video_frame_rate_incomplete" in str(exc.value)


def test_nested_hit_point_is_rejected() -> None:
    nested = _scoring()
    nested["hit_points"] = [
        {
            "id": "hit_0123abcd",
            "label": "Cue",
            "video_seconds": 1,
            "musical_tick": 0,
            "hit_point": {
                "id": "hit_deadbeef",
                "label": "Inner",
                "video_seconds": 0,
                "musical_tick": 0,
            },
        }
    ]
    with pytest.raises(ValidationError):
        VideoScoringV1.model_validate(nested)


def test_extra_key_is_rejected() -> None:
    payload = _scoring()
    payload["notes"] = []
    with pytest.raises(ValidationError):
        VideoScoringV1.model_validate(payload)


def test_golden_hit_defaults_spotting_fields() -> None:
    scoring = VideoScoringV1.model_validate(_scoring())
    cue = scoring.hit_points[0]
    assert cue.kind == "hit_point"
    assert cue.tolerance_frames == 0
    assert cue.importance == "medium"
    assert cue.instruction == ""
    assert cue.timecode is None


def test_explicit_null_timecode_stays_null() -> None:
    payload = _scoring()
    payload["hit_points"][0]["timecode"] = None
    cue = VideoScoringV1.model_validate(payload).hit_points[0]
    assert cue.timecode is None
    assert cue.video_seconds == 1.5
    assert cue.musical_tick == 480


def test_each_cue_kind_is_accepted() -> None:
    kinds = (
        "music_start",
        "music_stop",
        "hit_point",
        "reveal",
        "cut",
        "action",
        "dialogue",
        "emotional_cue",
        "user_defined",
    )
    points = []
    for index, kind in enumerate(kinds):
        points.append(
            {
                "id": f"hit_{index:08x}",
                "kind": kind,
                "label": "Street reveal" if kind == "user_defined" else kind,
                "timecode": "00:00:01:00",
                "video_seconds": 1.0,
                "musical_tick": 480,
            }
        )
    scoring = VideoScoringV1.model_validate(_scoring(hit_points=points))
    assert [point.kind for point in scoring.hit_points] == list(kinds)
    assert scoring.hit_points[-1].label == "Street reveal"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "scene"),
        ("tolerance_frames", -1),
        ("tolerance_frames", 241),
        ("importance", "urgent"),
        ("instruction", "x" * 241),
    ],
)
def test_spotting_field_outside_set_is_video_scoring_invalid(field: str, value: object) -> None:
    payload = _scoring()
    payload["hit_points"][0][field] = value
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(payload)
    assert "video_scoring_invalid" in str(exc.value)


def test_duplicate_hit_id_is_rejected() -> None:
    payload = _scoring()
    payload["hit_points"] = [
        payload["hit_points"][0],
        {**payload["hit_points"][0], "label": "Again"},
    ]
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(payload)
    assert "video_scoring_invalid" in str(exc.value)


def test_cue_frame_at_nominal_rate_is_rejected() -> None:
    payload = _scoring()
    payload["hit_points"][0]["timecode"] = "00:00:00:24"
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(payload)
    assert "video_timecode_invalid" in str(exc.value)


def test_cue_before_start_timecode_is_rejected() -> None:
    payload = _scoring(start_timecode="00:00:10:00")
    payload["hit_points"][0]["timecode"] = "00:00:00:00"
    with pytest.raises(ValidationError) as exc:
        VideoScoringV1.model_validate(payload)
    assert "video_timecode_invalid" in str(exc.value)


def test_drop_frame_2997_is_accepted() -> None:
    doc = VideoScoringV1.model_validate(
        _scoring(
            frame_rate_numerator=30000,
            frame_rate_denominator=1001,
            timecode_mode="drop_frame",
        )
    )
    assert doc.timecode_mode == "drop_frame"
