"""Section / time window helpers for mix analysis loci.

Bars are only populated when a composition (or explicit bar map) is provided.
Without composition → seconds-only loci; never invent bars.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisWindow:
    start_seconds: float
    end_seconds: float
    start_bar: int | None = None
    end_bar: int | None = None
    label: str = "full"


def full_window(duration_seconds: float) -> AnalysisWindow:
    return AnalysisWindow(
        start_seconds=0.0,
        end_seconds=max(0.0, duration_seconds),
        label="full",
    )


def seconds_to_bars(
    start_seconds: float,
    end_seconds: float,
    *,
    tempo_bpm: float | None,
    origin_tick: int = 0,
    ticks_per_beat: int = 480,
    composition: dict[str, Any] | None = None,
) -> tuple[int | None, int | None]:
    """Map seconds window to inclusive bars when composition/tempo available."""
    if composition is None or not tempo_bpm or tempo_bpm <= 0:
        return None, None
    # Prefer composition sections / meter when present.
    try:
        from app.composition_schemas import CompositionV2

        comp = CompositionV2.model_validate(composition)
        tpq = int(getattr(comp.meta, "ticks_per_quarter", None) or ticks_per_beat)
        bpm = float(tempo_bpm)
        seconds_per_tick = 60.0 / (bpm * tpq)
        if seconds_per_tick <= 0:
            return None, None
        start_tick = int(round(start_seconds / seconds_per_tick)) + int(origin_tick)
        end_tick = int(round(end_seconds / seconds_per_tick)) + int(origin_tick)
        start_bar = _bar_at_tick(comp, start_tick)
        end_bar = _bar_at_tick(comp, max(start_tick, end_tick - 1))
        if start_bar is None or end_bar is None:
            return None, None
        return start_bar, max(start_bar, end_bar)
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Bar mapping skipped",
            extra={"error_type": type(exc).__name__},
        )
        return None, None


def _bar_at_tick(composition: Any, tick: int) -> int | None:
    sections = getattr(composition, "sections", None) or []
    if not sections:
        # Fallback: 4/4 bars from ticks
        meta = getattr(composition, "meta", None)
        tpq = int(getattr(meta, "ticks_per_quarter", None) or 480)
        beats_per_bar = 4
        ticks_per_bar = tpq * beats_per_bar
        if ticks_per_bar <= 0:
            return None
        return max(1, tick // ticks_per_bar + 1)
    for section in sections:
        start = getattr(section, "start_tick", None)
        dur = getattr(section, "duration_ticks", None)
        start_bar = getattr(section, "start_bar", None)
        bar_count = getattr(section, "bar_count", None)
        if start is None or dur is None or start_bar is None:
            continue
        if start <= tick < start + dur:
            if bar_count and dur > 0:
                offset = tick - start
                bar_offset = int(offset * bar_count / dur)
                return int(start_bar) + bar_offset
            return int(start_bar)
    # Outside sections: estimate from first section meter
    first = sections[0]
    start_bar = getattr(first, "start_bar", 1) or 1
    start_tick = getattr(first, "start_tick", 0) or 0
    meta = getattr(composition, "meta", None)
    tpq = int(getattr(meta, "ticks_per_quarter", None) or 480)
    ticks_per_bar = tpq * 4
    if ticks_per_bar <= 0:
        return int(start_bar)
    delta = tick - int(start_tick)
    return max(1, int(start_bar) + delta // ticks_per_bar)


def build_section_windows(
    duration_seconds: float,
    *,
    tempo_bpm: float | None,
    origin_tick: int = 0,
    composition: dict[str, Any] | None = None,
    max_windows: int = 8,
) -> list[AnalysisWindow]:
    """Full-file window plus optional section windows when composition has sections."""
    windows = [full_window(duration_seconds)]
    if composition is None:
        return windows
    try:
        from app.composition_schemas import CompositionV2

        comp = CompositionV2.model_validate(composition)
        sections = list(comp.sections or [])
    except Exception:  # noqa: BLE001
        return windows
    if not sections or not tempo_bpm or tempo_bpm <= 0:
        return windows

    meta = comp.meta
    tpq = int(getattr(meta, "ticks_per_quarter", None) or 480)
    bpm = float(tempo_bpm)
    seconds_per_tick = 60.0 / (bpm * tpq)
    added = 0
    for section in sections:
        if added >= max_windows:
            break
        start_tick = getattr(section, "start_tick", None)
        dur = getattr(section, "duration_ticks", None)
        if start_tick is None or dur is None or dur <= 0:
            continue
        start_s = max(0.0, (int(start_tick) - int(origin_tick)) * seconds_per_tick)
        end_s = min(duration_seconds, start_s + int(dur) * seconds_per_tick)
        if end_s <= start_s:
            continue
        start_bar = getattr(section, "start_bar", None)
        bar_count = getattr(section, "bar_count", None)
        end_bar = None
        if start_bar is not None and bar_count is not None:
            end_bar = int(start_bar) + int(bar_count) - 1
        windows.append(
            AnalysisWindow(
                start_seconds=start_s,
                end_seconds=end_s,
                start_bar=int(start_bar) if start_bar is not None else None,
                end_bar=end_bar,
                label=str(getattr(section, "type", None) or getattr(section, "id", None) or "section"),
            )
        )
        added += 1
    logger.debug(
        "Mix analysis windows built",
        extra={"window_count": len(windows), "has_composition": True},
    )
    return windows


def slice_mono(
    mono: Sequence[float],
    sample_rate: int,
    window: AnalysisWindow,
) -> list[float]:
    if sample_rate <= 0 or not mono:
        return list(mono)
    start = max(0, int(window.start_seconds * sample_rate))
    end = min(len(mono), int(window.end_seconds * sample_rate))
    if end <= start:
        return []
    return list(mono[start:end])
