"""Variable-tempo / variable-meter timeline compilation for composition.v2."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Sequence

from app.composition_schemas import compile_bar_boundaries, round_half_away_from_zero
from app.services.composition_timing import bar_duration_ticks


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TimelineState:
    tick: int
    tempo: int
    time_signature: str
    key: str
    bar: int


@dataclass(frozen=True)
class CompiledTimeline:
    """Compiled bar/tempo/key map derived only from canonical composition fields."""

    ticks_per_quarter: int
    duration_ticks: int
    bar_count: int
    bar_boundaries: tuple[int, ...]  # length bar_count + 1
    root_tempo: int
    root_time_signature: str
    root_key: str
    tempo_changes: tuple[tuple[int, int], ...]  # (tick, bpm)
    time_signature_changes: tuple[tuple[int, str], ...]
    key_changes: tuple[tuple[int, str], ...]

    def bar_start_tick(self, bar: int) -> int:
        if bar < 1 or bar > self.bar_count:
            raise ValueError(f"bar {bar} is outside 1..{self.bar_count}")
        return self.bar_boundaries[bar - 1]

    def bar_end_tick(self, bar: int) -> int:
        if bar < 1 or bar > self.bar_count:
            raise ValueError(f"bar {bar} is outside 1..{self.bar_count}")
        return self.bar_boundaries[bar]

    def bar_range_ticks(self, start_bar: int, end_bar: int) -> tuple[int, int]:
        """Inclusive start_bar / end_bar → [start_tick, end_tick)."""
        if end_bar < start_bar:
            raise ValueError("end_bar must be >= start_bar")
        return self.bar_start_tick(start_bar), self.bar_end_tick(end_bar)

    def bar_at_tick(self, tick: int) -> int:
        if tick < 0 or tick > self.duration_ticks:
            raise ValueError("tick is outside composition duration")
        if tick == self.duration_ticks:
            return self.bar_count
        # Find largest bar start <= tick
        lo, hi = 0, self.bar_count - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            start = self.bar_boundaries[mid]
            end = self.bar_boundaries[mid + 1]
            if start <= tick < end:
                return mid + 1
            if tick < start:
                hi = mid - 1
            else:
                lo = mid + 1
        return self.bar_count

    def active_tempo(self, tick: int) -> int:
        tempo = self.root_tempo
        for change_tick, bpm in self.tempo_changes:
            if change_tick <= tick:
                tempo = bpm
            else:
                break
        return tempo

    def active_time_signature(self, tick: int) -> str:
        meter = self.root_time_signature
        for change_tick, signature in self.time_signature_changes:
            if change_tick <= tick:
                meter = signature
            else:
                break
        return meter

    def active_key(self, tick: int) -> str:
        key = self.root_key
        for change_tick, value in self.key_changes:
            if change_tick <= tick:
                key = value
            else:
                break
        return key

    def state_at_tick(self, tick: int) -> TimelineState:
        return TimelineState(
            tick=tick,
            tempo=self.active_tempo(tick),
            time_signature=self.active_time_signature(tick),
            key=self.active_key(tick),
            bar=self.bar_at_tick(tick) if tick <= self.duration_ticks else self.bar_count,
        )

    def tick_to_seconds(self, tick: int) -> float:
        if tick < 0:
            raise ValueError("tick cannot be negative")
        if tick > self.duration_ticks:
            raise ValueError("tick exceeds composition duration")
        return self._integrate_ticks(0, tick)

    def seconds_to_tick(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("seconds cannot be negative")
        total = self.total_duration_seconds()
        if seconds >= total:
            return float(self.duration_ticks)

        # Walk tempo segments until cumulative seconds reach target.
        cursor_tick = 0
        remaining = float(seconds)
        tempo = self.root_tempo
        change_index = 0
        while cursor_tick < self.duration_ticks:
            next_change = (
                self.tempo_changes[change_index][0]
                if change_index < len(self.tempo_changes)
                else self.duration_ticks
            )
            segment_end = min(next_change, self.duration_ticks)
            if segment_end <= cursor_tick:
                if change_index < len(self.tempo_changes) and next_change == cursor_tick:
                    tempo = self.tempo_changes[change_index][1]
                    change_index += 1
                    continue
                break
            seconds_per_tick = 60.0 / tempo / self.ticks_per_quarter
            segment_ticks = segment_end - cursor_tick
            segment_seconds = segment_ticks * seconds_per_tick
            if remaining <= segment_seconds:
                return cursor_tick + (remaining / seconds_per_tick)
            remaining -= segment_seconds
            cursor_tick = segment_end
            if change_index < len(self.tempo_changes) and next_change == cursor_tick:
                tempo = self.tempo_changes[change_index][1]
                change_index += 1
        return float(self.duration_ticks)

    def total_duration_seconds(self) -> float:
        return self._integrate_ticks(0, self.duration_ticks)

    def _integrate_ticks(self, start_tick: int, end_tick: int) -> float:
        if end_tick < start_tick:
            raise ValueError("end_tick must be >= start_tick")
        if start_tick == end_tick:
            return 0.0

        total = 0.0
        cursor = start_tick
        tempo = self.active_tempo(start_tick)
        # Upcoming tempo change ticks strictly after start.
        upcoming = [tick for tick, _bpm in self.tempo_changes if tick > start_tick]
        change_index = 0
        while cursor < end_tick:
            next_boundary = upcoming[change_index] if change_index < len(upcoming) else end_tick
            segment_end = min(next_boundary, end_tick)
            seconds_per_tick = 60.0 / tempo / self.ticks_per_quarter
            total += (segment_end - cursor) * seconds_per_tick
            cursor = segment_end
            if change_index < len(upcoming) and upcoming[change_index] == cursor:
                tempo = self.active_tempo(cursor)
                change_index += 1
        return total

    def summary_extra(self) -> dict[str, Any]:
        return {
            "bar_count": self.bar_count,
            "duration_ticks": self.duration_ticks,
            "tempo_segment_count": len(self.tempo_changes) + 1,
            "meter_change_count": len(self.time_signature_changes),
            "key_change_count": len(self.key_changes),
            "total_seconds": round(self.total_duration_seconds(), 6),
        }


def _change_pairs(items: Sequence[Any], tick_attr: str, value_attr: str) -> tuple[tuple[int, Any], ...]:
    pairs: list[tuple[int, Any]] = []
    for item in items or ():
        if hasattr(item, tick_attr):
            tick = int(getattr(item, tick_attr))
            value = getattr(item, value_attr)
        else:
            tick = int(item[tick_attr])
            value = item[value_attr]
        pairs.append((tick, value))
    return tuple(sorted(pairs, key=lambda pair: pair[0]))


def compile_timeline(composition: Any) -> CompiledTimeline:
    """Compile a timeline from a CompositionV2-like object or mapping."""
    if hasattr(composition, "model_dump"):
        data = composition
        ticks_per_quarter = int(data.ticks_per_quarter)
        duration_ticks = int(data.duration_ticks)
        bar_count = int(data.bar_count)
        root_tempo = int(data.tempo)
        root_meter = str(data.time_signature)
        root_key = str(data.key)
        tempo_changes = getattr(data, "tempo_changes", ()) or ()
        meter_changes = getattr(data, "time_signature_changes", ()) or ()
        key_changes = getattr(data, "key_changes", ()) or ()
    else:
        data = composition
        ticks_per_quarter = int(data.get("ticks_per_quarter") or 480)
        duration_ticks = int(data["duration_ticks"])
        bar_count = int(data["bar_count"])
        root_tempo = int(data["tempo"])
        root_meter = str(data["time_signature"])
        root_key = str(data["key"])
        tempo_changes = data.get("tempo_changes") or ()
        meter_changes = data.get("time_signature_changes") or ()
        key_changes = data.get("key_changes") or ()

    boundaries = compile_bar_boundaries(
        time_signature=root_meter,
        ticks_per_quarter=ticks_per_quarter,
        bar_count=bar_count,
        duration_ticks=duration_ticks,
        time_signature_changes=list(meter_changes),
    )
    timeline = CompiledTimeline(
        ticks_per_quarter=ticks_per_quarter,
        duration_ticks=duration_ticks,
        bar_count=bar_count,
        bar_boundaries=tuple(boundaries),
        root_tempo=root_tempo,
        root_time_signature=root_meter,
        root_key=root_key,
        tempo_changes=_change_pairs(tempo_changes, "tick", "bpm"),
        time_signature_changes=_change_pairs(meter_changes, "tick", "time_signature"),
        key_changes=_change_pairs(key_changes, "tick", "key"),
    )
    logger.info(
        "Compiled composition timeline",
        extra=timeline.summary_extra(),
    )
    logger.debug(
        "Timeline compile details",
        extra={
            "boundary_count": len(timeline.bar_boundaries),
            "first_bar_ticks": timeline.bar_boundaries[1] - timeline.bar_boundaries[0] if bar_count else 0,
            "root_tempo": root_tempo,
            "root_time_signature": root_meter,
        },
    )
    return timeline


def constant_bar_ticks(time_signature: str, ticks_per_quarter: int) -> int:
    """Compatibility helper for single-meter consumers."""
    return bar_duration_ticks(time_signature, ticks_per_quarter)


def round_tick(value: float) -> int:
    return round_half_away_from_zero(value)
