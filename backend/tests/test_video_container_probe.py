"""Fixture-file probe for the read-only ISO-BMFF reader."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.services.video_container_probe import probe_iso_bmff
from app.video_scoring_schemas import VideoScoringError
from tests.fixtures.video.iso_bmff import write_iso_bmff


def _write(tmp_path: Path, name: str, **kwargs) -> Path:
    return write_iso_bmff(tmp_path / name, **kwargs)


def test_silent_24_fixture(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "silent.mp4",
        mvhd_timescale=600,
        mvhd_duration=1200,
        video={
            "sample_count": 48,
            "timescale": 24,
            "media_duration": 48,
            "width": 320,
            "height": 180,
        },
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    probe = probe_iso_bmff(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert probe.container == "mp4"
    assert probe.duration_seconds == pytest.approx(2.0)
    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (24, 1)
    assert probe.frame_rate_snapped is True
    assert probe.width == 320
    assert probe.height == 180
    assert probe.has_audio is False


def test_2997_with_audio_snaps(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "ntsc.mp4",
        mvhd_timescale=30000,
        mvhd_duration=60060,
        video={
            "sample_count": 60,
            "timescale": 30000,
            "media_duration": 60060,
            "width": 640,
            "height": 360,
        },
        audio={"sample_count": 60, "timescale": 48000, "media_duration": 96000, "width": 0, "height": 0},
    )
    probe = probe_iso_bmff(path)
    assert probe.duration_seconds == pytest.approx(2.002)
    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (30000, 1001)
    assert probe.frame_rate_snapped is True
    assert probe.has_audio is True


def test_unsupported_brand(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "bad.mp4",
        major_brand=b"XXXX",
        compatible_brands=(b"XXXX",),
        video={"sample_count": 24, "timescale": 24, "media_duration": 24},
    )
    with pytest.raises(VideoScoringError) as exc:
        probe_iso_bmff(path)
    assert exc.value.code == "video_container_unsupported"


def test_missing_video_trak(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "audio-only.mov",
        major_brand=b"qt  ",
        compatible_brands=(b"qt  ",),
        video=None,
        audio={"sample_count": 10, "timescale": 48000, "media_duration": 48000},
    )
    with pytest.raises(VideoScoringError) as exc:
        probe_iso_bmff(path)
    assert exc.value.code == "video_missing_video_track"


def test_no_moov(tmp_path: Path) -> None:
    path = _write(tmp_path, "empty.mp4", include_moov=False, mdat_payload=b"\x00" * 8)
    with pytest.raises(VideoScoringError) as exc:
        probe_iso_bmff(path)
    assert exc.value.code == "video_moov_not_found"


def test_mdat_before_moov_keeps_hash(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "mdat-first.mp4",
        mdat_before_moov=True,
        mdat_payload=b"\x11" * 64,
        video={
            "sample_count": 48,
            "timescale": 24,
            "media_duration": 48,
            "width": 320,
            "height": 180,
        },
    )
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    probe = probe_iso_bmff(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert probe.duration_seconds == pytest.approx(2.0)
    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (24, 1)


def test_version_1_headers(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "v1.mp4",
        header_version=1,
        mvhd_timescale=600,
        mvhd_duration=1200,
        video={
            "sample_count": 48,
            "timescale": 24,
            "media_duration": 48,
            "width": 1920,
            "height": 1080,
        },
    )
    probe = probe_iso_bmff(path)
    assert probe.duration_seconds == pytest.approx(2.0)
    assert probe.width == 1920
    assert probe.height == 1080
    assert (probe.frame_rate_numerator, probe.frame_rate_denominator) == (24, 1)


def test_probe_log_omits_path(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    folder = tmp_path / "secret-video-root"
    folder.mkdir()
    path = write_iso_bmff(
        folder / "clip.mp4",
        video={"sample_count": 24, "timescale": 24, "media_duration": 24, "width": 16, "height": 16},
    )
    with caplog.at_level(logging.INFO):
        probe_iso_bmff(path)
    assert "secret-video-root" not in caplog.text
    assert "clip.mp4" not in caplog.text
