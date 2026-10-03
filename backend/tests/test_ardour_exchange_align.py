"""Constant-tempo bar ↔ sample alignment helpers."""

from __future__ import annotations

import pytest

from app.ardour_exchange_schemas import ArdourExchangeError
from app.services.ardour_exchange_align import (
    bars_to_samples,
    expected_duration_ticks,
    length_samples_matches,
    samples_per_bar,
    samples_to_bar_offset,
)


def test_eight_bar_samples_at_120_bpm_48k() -> None:
    # 8 bars * 4 beats * 0.5 s/beat * 48000 = 768000
    assert bars_to_samples(
        bar_count=8,
        tempo_bpm=120,
        sample_rate=48000,
        time_signature="4/4",
    ) == 768_000
    assert samples_per_bar(tempo_bpm=120, sample_rate=48000) == 96_000.0


def test_samples_to_bar_offset() -> None:
    assert samples_to_bar_offset(
        samples=0,
        tempo_bpm=120,
        sample_rate=48000,
        start_bar=1,
    ) == 1
    assert samples_to_bar_offset(
        samples=96_000,
        tempo_bpm=120,
        sample_rate=48000,
        start_bar=1,
    ) == 2


def test_expected_duration_ticks_eight_bars() -> None:
    assert expected_duration_ticks(
        bar_count=8,
        time_signature="4/4",
        ticks_per_quarter=480,
    ) == 8 * 1920


def test_length_samples_matches_tolerance() -> None:
    assert length_samples_matches(
        length_samples=768_000,
        bar_count=8,
        tempo_bpm=120,
        sample_rate=48000,
        time_signature="4/4",
    )
    assert length_samples_matches(
        length_samples=768_001,
        bar_count=8,
        tempo_bpm=120,
        sample_rate=48000,
        time_signature="4/4",
    )
    assert not length_samples_matches(
        length_samples=700_000,
        bar_count=8,
        tempo_bpm=120,
        sample_rate=48000,
        time_signature="4/4",
    )


def test_invalid_tempo_raises() -> None:
    with pytest.raises(ArdourExchangeError) as excinfo:
        bars_to_samples(bar_count=1, tempo_bpm=10, sample_rate=48000)
    assert excinfo.value.code == "ardour_exchange_alignment_invalid"
