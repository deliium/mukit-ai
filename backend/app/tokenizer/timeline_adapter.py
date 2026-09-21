"""Adapt Composition V2 timeline helpers for tokenizer bar/position mapping."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.composition_schemas import CompositionV2, bar_duration_ticks
from app.services.composition_timeline import CompiledTimeline, compile_timeline
from app.tokenizer.quantize import snap_tick_to_grid
from app.tokenizer.schemas import TokenizerConfigV1


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BarPosition:
    bar_index0: int  # 0-based
    position_steps: int
    bar_start_tick: int
    bar_end_tick: int
    meter: str


def compile_tokenizer_timeline(
    composition: CompositionV2,
    config: TokenizerConfigV1,
) -> CompiledTimeline:
    if composition.ticks_per_quarter != config.ticks_per_quarter:
        logger.warning(
            "Composition PPQ differs from tokenizer config",
            extra={
                "composition_ppq": composition.ticks_per_quarter,
                "config_ppq": config.ticks_per_quarter,
            },
        )
    timeline = compile_timeline(composition)
    logger.debug(
        "Tokenizer timeline compiled",
        extra={
            "bar_count": timeline.bar_count,
            "duration_ticks": timeline.duration_ticks,
            "grid_ticks": config.grid_ticks,
        },
    )
    return timeline


def tick_to_bar_position(
    tick: int,
    timeline: CompiledTimeline,
    config: TokenizerConfigV1,
) -> tuple[BarPosition, bool]:
    """Map absolute tick → bar + in-bar grid position (after snap)."""
    snapped, did_snap = snap_tick_to_grid(tick, config.grid_ticks)
    # Clamp into composition span for position lookup.
    clamped = min(max(0, snapped), max(0, timeline.duration_ticks - 1))
    bar_1based = timeline.bar_at_tick(clamped)
    bar_index0 = bar_1based - 1
    bar_start = timeline.bar_start_tick(bar_1based)
    bar_end = timeline.bar_end_tick(bar_1based)
    meter = timeline.active_time_signature(bar_start)
    offset = max(0, snapped - bar_start)
    position_steps = offset // config.grid_ticks
    bar_steps = (bar_end - bar_start) // config.grid_ticks
    if position_steps >= bar_steps and bar_steps > 0:
        position_steps = bar_steps - 1
    return (
        BarPosition(
            bar_index0=bar_index0,
            position_steps=position_steps,
            bar_start_tick=bar_start,
            bar_end_tick=bar_end,
            meter=meter,
        ),
        did_snap,
    )


def bar_position_to_tick(
    bar_index0: int,
    position_steps: int,
    timeline: CompiledTimeline,
    config: TokenizerConfigV1,
) -> int:
    bar_1based = bar_index0 + 1
    bar_start = timeline.bar_start_tick(bar_1based)
    return bar_start + position_steps * config.grid_ticks


def bar_steps_for_meter(meter: str, config: TokenizerConfigV1) -> int:
    ticks = bar_duration_ticks(meter, config.ticks_per_quarter)
    return ticks // config.grid_ticks
