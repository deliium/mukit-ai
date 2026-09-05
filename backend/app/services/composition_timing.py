import logging
from fractions import Fraction
from typing import Any


logger = logging.getLogger(__name__)

SUPPORTED_DENOMINATORS = {1, 2, 4, 8, 16, 32}


def parse_time_signature(time_signature: str) -> tuple[int, int]:
    logger.debug("Parsing composition time signature", extra={"time_signature": time_signature})
    parts = time_signature.strip().split("/")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        logger.warning(
            "Rejected malformed composition time signature",
            extra={"time_signature": time_signature, "reason": "expected numeric meter like 4/4"},
        )
        raise ValueError("Time signature must use format like '4/4'")

    numerator, denominator = int(parts[0]), int(parts[1])
    if numerator < 1 or numerator > 32 or denominator not in SUPPORTED_DENOMINATORS:
        logger.warning(
            "Rejected unsupported composition time signature",
            extra={
                "time_signature": time_signature,
                "numerator": numerator,
                "denominator": denominator,
                "reason": "unsupported numerator or denominator",
            },
        )
        raise ValueError("Time signature has unsupported numerator or denominator")

    logger.debug(
        "Parsed composition time signature",
        extra={"time_signature": time_signature, "numerator": numerator, "denominator": denominator},
    )
    return numerator, denominator


def bar_duration_ticks(time_signature: str, ticks_per_quarter: int) -> int:
    logger.debug(
        "Calculating bar duration ticks",
        extra={"time_signature": time_signature, "ticks_per_quarter": ticks_per_quarter},
    )
    if ticks_per_quarter <= 0:
        logger.warning(
            "Rejected invalid ticks_per_quarter",
            extra={"ticks_per_quarter": ticks_per_quarter, "reason": "must be positive"},
        )
        raise ValueError("ticks_per_quarter must be positive")

    numerator, denominator = parse_time_signature(time_signature)
    ticks = Fraction(numerator * 4 * ticks_per_quarter, denominator)
    if ticks.denominator != 1:
        logger.warning(
            "Rejected non-integral bar duration",
            extra={
                "time_signature": time_signature,
                "ticks_per_quarter": ticks_per_quarter,
                "duration_fraction": str(ticks),
                "reason": "bar duration is not exactly representable as integer ticks",
            },
        )
        raise ValueError("Time signature does not produce an integer tick duration")

    result = int(ticks)
    logger.debug(
        "Calculated bar duration ticks",
        extra={"time_signature": time_signature, "ticks_per_quarter": ticks_per_quarter, "bar_ticks": result},
    )
    return result


def bar_to_start_tick(bar: int, time_signature: str, ticks_per_quarter: int) -> int:
    logger.debug(
        "Converting bar to start tick",
        extra={"bar": bar, "time_signature": time_signature, "ticks_per_quarter": ticks_per_quarter},
    )
    if bar < 1:
        logger.warning("Rejected invalid bar number", extra={"bar": bar, "reason": "bar is one-based"})
        raise ValueError("bar must be greater than or equal to 1")

    result = (bar - 1) * bar_duration_ticks(time_signature, ticks_per_quarter)
    logger.debug("Converted bar to start tick", extra={"bar": bar, "start_tick": result})
    return result


def quarter_units_to_ticks(quarter_units: int | float | str, ticks_per_quarter: int) -> int:
    logger.debug(
        "Converting quarter units to ticks",
        extra={"quarter_units": str(quarter_units), "ticks_per_quarter": ticks_per_quarter},
    )
    if ticks_per_quarter <= 0:
        logger.warning(
            "Rejected invalid ticks_per_quarter",
            extra={"ticks_per_quarter": ticks_per_quarter, "reason": "must be positive"},
        )
        raise ValueError("ticks_per_quarter must be positive")

    try:
        quarter_fraction = Fraction(str(quarter_units))
    except ValueError as exc:
        logger.warning(
            "Rejected malformed quarter-unit timing value",
            extra={"quarter_units": str(quarter_units), "reason": "not a valid rational number"},
        )
        raise ValueError("quarter_units must be a valid rational number") from exc

    ticks = quarter_fraction * ticks_per_quarter
    if ticks.denominator != 1:
        logger.warning(
            "Rejected non-integral quarter-unit timing value",
            extra={
                "quarter_units": str(quarter_units),
                "ticks_per_quarter": ticks_per_quarter,
                "ticks_fraction": str(ticks),
                "reason": "value is not exactly representable as integer ticks",
            },
        )
        raise ValueError("quarter_units does not convert to an integer tick value")

    result = int(ticks)
    logger.debug(
        "Converted quarter units to ticks",
        extra={"quarter_units": str(quarter_units), "ticks_per_quarter": ticks_per_quarter, "ticks": result},
    )
    return result


def legacy_bar_beat_to_start_tick(
    bar: int,
    beat: int | float | str,
    time_signature: str,
    ticks_per_quarter: int,
) -> int:
    logger.debug(
        "Converting legacy bar/beat to start tick",
        extra={
            "bar": bar,
            "beat": str(beat),
            "time_signature": time_signature,
            "ticks_per_quarter": ticks_per_quarter,
        },
    )
    if bar < 1:
        logger.warning("Rejected invalid legacy bar", extra={"bar": bar, "reason": "bar is one-based"})
        raise ValueError("bar must be greater than or equal to 1")

    try:
        beat_fraction = Fraction(str(beat))
    except ValueError as exc:
        logger.warning(
            "Rejected malformed legacy beat",
            extra={"bar": bar, "beat": str(beat), "reason": "not a valid rational number"},
        )
        raise ValueError("beat must be a valid rational number") from exc

    if beat_fraction < 1:
        logger.warning(
            "Rejected invalid legacy beat",
            extra={"bar": bar, "beat": str(beat), "reason": "beat is one-based"},
        )
        raise ValueError("beat must be greater than or equal to 1")

    bar_offset = bar_to_start_tick(bar, time_signature, ticks_per_quarter)
    beat_offset = quarter_units_to_ticks(beat_fraction - 1, ticks_per_quarter)
    result = bar_offset + beat_offset
    logger.debug(
        "Converted legacy bar/beat to start tick",
        extra={"bar": bar, "beat": str(beat), "start_tick": result},
    )
    return result


def derive_section_boundaries(
    sections: list[Any],
    time_signature: str,
    ticks_per_quarter: int,
) -> list[dict[str, int | str]]:
    logger.debug(
        "Deriving composition section boundaries",
        extra={
            "section_count": len(sections),
            "time_signature": time_signature,
            "ticks_per_quarter": ticks_per_quarter,
        },
    )
    if not sections:
        logger.warning("Rejected empty sections for boundary derivation", extra={"reason": "sections are required"})
        raise ValueError("At least one section is required")

    bar_ticks = bar_duration_ticks(time_signature, ticks_per_quarter)
    start_bar = 1
    start_tick = 0
    derived: list[dict[str, int | str]] = []

    for section in sections:
        section_data = section if isinstance(section, dict) else getattr(section, "model_dump", lambda: section)()
        section_type = str(section_data.get("type", "")).strip()
        bar_count = int(section_data.get("bar_count", section_data.get("bars", 0)))
        if not section_type or bar_count < 1:
            logger.warning(
                "Rejected malformed section for boundary derivation",
                extra={"section": str(section_data)[:120], "reason": "missing type or positive bar count"},
            )
            raise ValueError("Sections require a type and positive bar count")

        duration_ticks = bar_count * bar_ticks
        derived.append(
            {
                "type": section_type,
                "start_bar": start_bar,
                "bar_count": bar_count,
                "start_tick": start_tick,
                "duration_ticks": duration_ticks,
            }
        )
        start_bar += bar_count
        start_tick += duration_ticks

    logger.debug(
        "Derived composition section boundaries",
        extra={"section_count": len(derived), "bar_count": start_bar - 1, "duration_ticks": start_tick},
    )
    return derived
