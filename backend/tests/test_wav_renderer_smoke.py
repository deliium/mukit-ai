"""Opt-in smoke test for installed FluidSynth + SoundFont WAV rendering.

Skipped unless RUN_WAV_RENDERER_SMOKE=1. Inside the standard backend Docker image:

    RUN_WAV_RENDERER_SMOKE=1 python -m pytest tests/test_wav_renderer_smoke.py
"""

from __future__ import annotations

import logging
import os

import pytest

from app.services.composition_wav import (
    expected_duration_seconds,
    load_wav_renderer_config,
    render_wav,
)
from tests.test_export_fidelity import build_export_fidelity_composition

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WAV_RENDERER_SMOKE") != "1",
    reason="Set RUN_WAV_RENDERER_SMOKE=1 and install FluidSynth + SoundFont (Docker default)",
)


def test_fluidsynth_renders_multi_track_wav(caplog):
    config = load_wav_renderer_config()
    if not config.fluidsynth_exists or not config.soundfont_exists:
        pytest.skip(
            "FluidSynth binary or SoundFont missing "
            f"(bin={config.fluidsynth_basename}, sf={config.soundfont_basename})"
        )

    composition = build_export_fidelity_composition()
    expected = expected_duration_seconds(composition)

    with caplog.at_level("INFO"):
        logger.info(
            "WAV renderer smoke started",
            extra={
                "fluidsynth": config.fluidsynth_basename,
                "soundfont": config.soundfont_basename,
                "sample_rate": config.sample_rate,
            },
        )
        wav_bytes = render_wav(composition)
        logger.info(
            "WAV renderer smoke completed",
            extra={
                "byte_length": len(wav_bytes),
                "expected_seconds": round(expected, 4),
            },
        )

    assert wav_bytes[:4] == b"RIFF"
    assert wav_bytes[8:12] == b"WAVE"
    assert len(wav_bytes) > 1000
    from app.services.composition_wav import _wav_duration_seconds

    measured = _wav_duration_seconds(wav_bytes)
    assert measured == pytest.approx(expected, abs=0.25)
    assert "WAV render completed" in caplog.text
