"""Pure constant-tempo bar ↔ sample alignment helpers for Ardour exchange.

Ship-1 assumes a single tempo across the selected region. Multi-tempo spans
may WARN in the caller and still carry SMF conductor data.
"""

from __future__ import annotations

import logging
import math

from app.ardour_exchange_schemas import ArdourExchangeError
from app.composition_schemas import bar_duration_ticks

logger = logging.getLogger(__name__)


def samples_per_bar(*, tempo_bpm: int | float, sample_rate: int, time_signature: str = "4/4") -> float:
    """Return samples for one bar at constant tempo (4/4 ⇒ 4 beats)."""
    _validate_tempo_rate(tempo_bpm, sample_rate)
    beats = _beats_per_bar(time_signature)
    seconds_per_beat = 60.0 / float(tempo_bpm)
    return beats * seconds_per_beat * float(sample_rate)


def bars_to_samples(
    *,
    bar_count: int,
    tempo_bpm: int | float,
    sample_rate: int,
    time_signature: str = "4/4",
) -> int:
    """Convert a bar span to sample length (rounded half away from zero)."""
    if bar_count < 1:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "bar_count"},
        )
    per_bar = samples_per_bar(
        tempo_bpm=tempo_bpm,
        sample_rate=sample_rate,
        time_signature=time_signature,
    )
    return int(round(per_bar * bar_count))


def samples_to_bar_offset(
    *,
    samples: int,
    tempo_bpm: int | float,
    sample_rate: int,
    time_signature: str = "4/4",
    start_bar: int = 1,
) -> int:
    """Map an absolute sample position to a 1-based bar index (floor)."""
    if samples < 0:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "samples"},
        )
    if start_bar < 1:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "start_bar"},
        )
    per_bar = samples_per_bar(
        tempo_bpm=tempo_bpm,
        sample_rate=sample_rate,
        time_signature=time_signature,
    )
    if per_bar <= 0:
        raise ArdourExchangeError("ardour_exchange_alignment_invalid")
    offset = int(math.floor(samples / per_bar))
    bar = start_bar + offset
    logger.debug(
        "Ardour exchange samples_to_bar",
        extra={"samples": samples, "bar": bar, "tempo_bpm": int(tempo_bpm)},
    )
    return bar


def expected_duration_ticks(
    *,
    bar_count: int,
    time_signature: str,
    ticks_per_quarter: int,
) -> int:
    """Ticks for ``bar_count`` complete bars under a constant meter."""
    if bar_count < 1:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "bar_count"},
        )
    per_bar = bar_duration_ticks(time_signature, ticks_per_quarter)
    return per_bar * bar_count


def length_samples_matches(
    *,
    length_samples: int | None,
    bar_count: int,
    tempo_bpm: int | float,
    sample_rate: int,
    time_signature: str,
    tolerance_samples: int = 2,
) -> bool:
    """Return whether optional ``length_samples`` matches the bar span."""
    if length_samples is None:
        return True
    expected = bars_to_samples(
        bar_count=bar_count,
        tempo_bpm=tempo_bpm,
        sample_rate=sample_rate,
        time_signature=time_signature,
    )
    return abs(int(length_samples) - expected) <= tolerance_samples


def _beats_per_bar(time_signature: str) -> float:
    text = time_signature.strip()
    numerator_s, denominator_s = text.split("/", 1)
    numerator = int(numerator_s)
    denominator = int(denominator_s)
    if numerator <= 0 or denominator <= 0:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "time_signature"},
        )
    # Quarter-note beats: a 6/8 bar is 3 quarter beats under PPQ mapping used elsewhere.
    return numerator * (4.0 / float(denominator))


def _validate_tempo_rate(tempo_bpm: int | float, sample_rate: int) -> None:
    if isinstance(tempo_bpm, bool) or not isinstance(tempo_bpm, (int, float)):
        raise ArdourExchangeError("ardour_exchange_alignment_invalid")
    if not math.isfinite(float(tempo_bpm)) or float(tempo_bpm) < 40 or float(tempo_bpm) > 240:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "tempo_bpm"},
        )
    if sample_rate < 8000 or sample_rate > 192000:
        raise ArdourExchangeError(
            "ardour_exchange_alignment_invalid",
            details={"reason": "sample_rate"},
        )
