"""Read-only active harmony at tick for live predict validation.

Mirrors FE ``activeHarmonyAtTick`` — never invents playable notes from chords.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

from app.live_performance_schemas import LIVE_HARMONY_EMPTY, LiveActiveHarmony

logger = logging.getLogger(__name__)

_empty_warn_count = 0


def active_harmony_at_tick(
    composition: Mapping[str, Any] | None,
    tick: int,
) -> LiveActiveHarmony:
    """Return harmony span covering ``tick``, or empty with warning semantics."""
    global _empty_warn_count
    safe_tick = max(0, int(tick))
    if not composition:
        return _empty()
    harmony = composition.get("harmony")
    if not isinstance(harmony, list) or len(harmony) == 0:
        return _empty()

    for item in harmony:
        span = _as_span(item)
        if span is None:
            continue
        start, dur, chord = span
        if start <= safe_tick < start + dur:
            if not chord:
                return _empty()
            logger.debug(
                "Live harmony hit",
                extra={"tick": safe_tick, "start_tick": start, "symbol_len": len(chord)},
            )
            return LiveActiveHarmony(
                symbol=chord,
                start_tick=start,
                duration_ticks=dur,
            )
    return _empty()


def active_harmony_from_spans(
    spans: Sequence[Any],
    tick: int,
) -> LiveActiveHarmony:
    """Lookup against a pre-extracted span list (predict path)."""
    safe_tick = max(0, int(tick))
    for item in spans:
        span = _as_span(item)
        if span is None:
            continue
        start, dur, chord = span
        if start <= safe_tick < start + dur and chord:
            return LiveActiveHarmony(
                symbol=chord,
                start_tick=start,
                duration_ticks=dur,
            )
    return _empty()


def _as_span(item: Any) -> tuple[int, int, str] | None:
    if item is None:
        return None
    if hasattr(item, "start_tick"):
        try:
            start = int(item.start_tick)
            dur = int(item.duration_ticks)
            chord = str(getattr(item, "chord", "") or "").strip()
            if dur <= 0:
                return None
            return start, dur, chord
        except (TypeError, ValueError):
            return None
    if isinstance(item, Mapping):
        # Canonical spans only for BE twin (legacy bar points not expanded here).
        if "start_tick" not in item or "duration_ticks" not in item:
            return None
        try:
            start = int(item["start_tick"])
            dur = int(item["duration_ticks"])
            chord = str(item.get("chord") or "").strip()
            if dur <= 0:
                return None
            return start, dur, chord
        except (TypeError, ValueError):
            return None
    return None


def _empty() -> LiveActiveHarmony:
    global _empty_warn_count
    _empty_warn_count += 1
    if _empty_warn_count <= 3 or _empty_warn_count % 32 == 0:
        logger.warning(
            "live_harmony_empty",
            extra={"code": LIVE_HARMONY_EMPTY, "count": _empty_warn_count},
        )
    return LiveActiveHarmony(symbol=None, start_tick=None, duration_ticks=None)


def reset_live_harmony_empty_warn_count_for_tests() -> None:
    global _empty_warn_count
    _empty_warn_count = 0
