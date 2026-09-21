"""Grid quantization helpers for the Composition V2 tokenizer."""

from __future__ import annotations

import logging

from app.composition_schemas import round_half_away_from_zero
from app.tokenizer.schemas import TokenizerConfigV1


logger = logging.getLogger(__name__)


def snap_tick_to_grid(tick: int, grid_ticks: int) -> tuple[int, bool]:
    """Snap ``tick`` to nearest grid; half-up via ``round_half_away_from_zero``.

    Returns ``(snapped_tick, did_snap)``.
    """
    if grid_ticks <= 0:
        raise ValueError("grid_ticks must be positive")
    if tick % grid_ticks == 0:
        return tick, False
    steps = round_half_away_from_zero(tick / float(grid_ticks))
    snapped = max(0, steps * grid_ticks)
    return snapped, True


def snap_duration_steps(
    duration_ticks: int,
    config: TokenizerConfigV1,
) -> tuple[int, bool, bool]:
    """Snap duration to nearest ≥ 1 grid step; clamp to ``max_dur_steps``.

    Returns ``(dur_steps, did_snap, did_clamp)``.
    """
    grid = config.grid_ticks
    if duration_ticks <= 0:
        return 1, True, False
    steps = round_half_away_from_zero(duration_ticks / float(grid))
    did_snap = steps * grid != duration_ticks
    if steps < 1:
        steps = 1
        did_snap = True
    did_clamp = False
    if steps > config.max_dur_steps:
        steps = config.max_dur_steps
        did_clamp = True
    return steps, did_snap, did_clamp


def velocity_to_bin(velocity: int, bins: int) -> int:
    """Map MIDI velocity 1..127 into ``0..bins-1`` (0 velocity → bin 0)."""
    if bins <= 1:
        return 0
    v = max(0, min(127, int(velocity)))
    if v <= 0:
        return 0
    # Map 1..127 uniformly across bins.
    idx = ((v - 1) * bins) // 127
    return max(0, min(bins - 1, idx))


def bin_to_velocity(bin_index: int, bins: int) -> int:
    """Reconstruct bin-center velocity clamped to 1..127."""
    if bins <= 1:
        return 64
    idx = max(0, min(bins - 1, int(bin_index)))
    # Center of bin i in 1..127 space.
    lo = 1 + (idx * 127) // bins
    hi = 1 + ((idx + 1) * 127) // bins - 1
    if hi < lo:
        hi = lo
    center = (lo + hi) // 2
    return max(1, min(127, center))


def midi_to_pitch_name(midi: int) -> str:
    """Deterministic MIDI → sharp spelling (C-1 .. G9)."""
    if midi < 0 or midi > 127:
        raise ValueError("MIDI pitch out of range")
    names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
    octave = (midi // 12) - 1
    return f"{names[midi % 12]}{octave}"


def meter_token_slug(meter: str) -> str:
    return meter.strip().replace("/", "_")


def key_token_slug(key: str) -> str:
    return key.strip().replace(" ", "_").replace("#", "s")
