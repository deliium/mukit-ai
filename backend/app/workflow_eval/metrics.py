"""Hard and observed scores for one composition.

Calls the existing constraint, integrity, repetition, contrast, and tonal
helpers. Does not mutate the composition. Observed numbers are not taste.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.critique_settings import load_critique_engine_settings
from app.services.composition_analysis import analyze_composition
from app.services.composition_analysis_context import build_analysis_context
from app.services.composition_critique_checks import check_section_contrast
from app.services.composition_repetition_analysis import analyze_repetition_from_context
from app.services.composition_revision_preserve import events_outside_targets_fingerprint
from app.services.composition_validator import validate_composition_integrity
from app.services.generation_constraints import (
    GenerationConstraints,
    validate_generation_constraints,
)
from app.workflow_eval.schemas import (
    COUNTER_METRIC_IDS,
    HARD_METRIC_IDS,
    OBSERVED_METRIC_IDS,
    CaseMetricsV1,
    MetricReadingV1,
    MetricStatus,
)

logger = logging.getLogger(__name__)

_HARD_NAMES = {
    "hard_constraint_compliance": "hard",
    "structural_compliance": "hard",
    "instrument_range_correctness": "hard",
    "revision_preservation": "hard",
    "invalid_composition": "hard",
}


def _reading(
    name: str,
    kind: str,
    status: MetricStatus,
    value: float | int | bool | None = None,
) -> MetricReadingV1:
    logger.debug("Metric scored", extra={"metric": name, "status": status})
    return MetricReadingV1(name=name, kind=kind, status=status, value=value)  # type: ignore[arg-type]


def _unavailable_musical(*, invalid: bool) -> list[MetricReadingV1]:
    readings: list[MetricReadingV1] = [
        _reading(
            "invalid_composition",
            "hard",
            "fail" if invalid else "pass",
            invalid,
        )
    ]
    for name in (
        "hard_constraint_compliance",
        "structural_compliance",
        "instrument_range_correctness",
        "revision_preservation",
        "motif_recurrence",
        "section_contrast",
        "tonal_consistency",
    ):
        kind = "hard" if name in _HARD_NAMES else "observed"
        readings.append(_reading(name, kind, "unavailable", None))
    return readings


def _structural_pass(composition: CompositionV2, constraints: GenerationConstraints) -> bool:
    if composition.bar_count != constraints.duration_bars:
        return False
    if not constraints.sections_user_specified:
        return True
    expected = constraints.sections or ()
    actual = composition.sections
    if len(expected) != len(actual):
        return False
    return all(
        left.type == right.type
        and left.start_bar == right.start_bar
        and left.bar_count == right.bar_count
        for left, right in zip(expected, actual, strict=True)
    )


def _motif_recurrence(composition: CompositionV2) -> MetricReadingV1:
    try:
        context = build_analysis_context(composition)
        result = analyze_repetition_from_context(context)
    except Exception as exc:  # noqa: BLE001 — record unavailable, do not fail the suite
        logger.debug(
            "Motif recurrence unavailable",
            extra={"error_type": type(exc).__name__},
        )
        return _reading("motif_recurrence", "observed", "unavailable", None)
    count = sum(
        1
        for family in result.motif_families
        if 1 + len(family.matched_occurrences) >= 2
    )
    return _reading("motif_recurrence", "observed", "measured", count)


def _section_contrast(composition: CompositionV2) -> MetricReadingV1:
    if len(composition.sections) < 2:
        return _reading("section_contrast", "observed", "not_applicable", None)
    try:
        report = analyze_composition(composition)
        findings = check_section_contrast(
            report,
            settings=load_critique_engine_settings(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Section contrast unavailable",
            extra={"error_type": type(exc).__name__},
        )
        return _reading("section_contrast", "observed", "unavailable", None)
    count = sum(1 for finding in findings if finding.code == "section_lacks_contrast")
    return _reading("section_contrast", "observed", "measured", count)


def _tonal_consistency(composition: CompositionV2) -> MetricReadingV1:
    try:
        from app.music_transformer.eval_metrics import compute_metrics_for_samples

        report = compute_metrics_for_samples(
            [{"composition": composition}],
            metrics=["tonal_consistency"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Tonal consistency unavailable",
            extra={"error_type": type(exc).__name__},
        )
        return _reading("tonal_consistency", "observed", "unavailable", None)
    metric = next((item for item in report.metrics if item.name == "tonal_consistency"), None)
    if metric is None or metric.status != "ok" or not isinstance(metric.value, (int, float)):
        return _reading("tonal_consistency", "observed", "unavailable", None)
    return _reading("tonal_consistency", "observed", "measured", float(metric.value))


def _preservation(
    composition: CompositionV2,
    *,
    source: CompositionV2 | None,
    affected_ranges: list[Any] | None,
    affected_tracks: list[str] | None,
    applicable: bool,
) -> MetricReadingV1:
    if not applicable or source is None:
        return _reading("revision_preservation", "hard", "not_applicable", None)
    ranges = list(affected_ranges or [])
    tracks = [str(track) for track in (affected_tracks or []) if str(track).strip()]
    if not ranges and not tracks:
        return _reading("revision_preservation", "hard", "not_applicable", None)
    before = events_outside_targets_fingerprint(
        source,
        affected_ranges=ranges,
        affected_tracks=tracks,
    )
    after = events_outside_targets_fingerprint(
        composition,
        affected_ranges=ranges,
        affected_tracks=tracks,
    )
    ok = before == after
    return _reading("revision_preservation", "hard", "pass" if ok else "fail", ok)


def _parse_composition(composition: CompositionV2 | dict[str, Any] | None) -> CompositionV2:
    if isinstance(composition, CompositionV2):
        return composition
    if isinstance(composition, dict):
        return CompositionV2.model_validate(composition)
    raise TypeError(type(composition).__name__)


def score_composition(
    composition: CompositionV2 | dict[str, Any] | None,
    constraints: GenerationConstraints | None,
    *,
    preservation_source: CompositionV2 | None = None,
    affected_ranges: list[Any] | None = None,
    affected_tracks: list[str] | None = None,
    preservation_applicable: bool = False,
) -> CaseMetricsV1:
    """Score one arm output. Musical metrics are unavailable when the piece is invalid."""
    logger.debug("score_composition entry", extra={"has_constraints": constraints is not None})
    try:
        parsed = _parse_composition(composition)
    except (ValidationError, TypeError, ValueError) as exc:
        logger.info(
            "Composition score summary",
            extra={
                "invalid_composition": True,
                "hard_constraint_compliance": None,
                "structural_compliance": None,
                "instrument_range_correctness": None,
                "revision_preservation": None,
                "musical_quality_claim": False,
                "error_type": type(exc).__name__,
            },
        )
        return CaseMetricsV1(metrics=_unavailable_musical(invalid=True))

    try:
        integrity = validate_composition_integrity(parsed, profile="canonical")
    except Exception as exc:  # noqa: BLE001 — integrity raised
        logger.error(
            "Integrity validation raised",
            extra={"error_type": type(exc).__name__},
        )
        return CaseMetricsV1(metrics=_unavailable_musical(invalid=True))

    if any(item.code == "schema_invalid" for item in integrity.errors):
        return CaseMetricsV1(metrics=_unavailable_musical(invalid=True))

    range_ok = not any(item.code == "event_out_of_range" for item in integrity.errors)
    hard_ok = False
    structural_ok = False
    if constraints is not None:
        report = validate_generation_constraints(parsed, constraints)
        hard_ok = len(report.errors) == 0
        structural_ok = _structural_pass(parsed, constraints)

    readings = [
        _reading("invalid_composition", "hard", "pass", False),
        _reading(
            "hard_constraint_compliance",
            "hard",
            "pass" if hard_ok else "fail",
            hard_ok,
        ),
        _reading(
            "structural_compliance",
            "hard",
            "pass" if structural_ok else "fail",
            structural_ok,
        ),
        _reading(
            "instrument_range_correctness",
            "hard",
            "pass" if range_ok else "fail",
            range_ok,
        ),
        _preservation(
            parsed,
            source=preservation_source,
            affected_ranges=affected_ranges,
            affected_tracks=affected_tracks,
            applicable=preservation_applicable,
        ),
        _motif_recurrence(parsed),
        _section_contrast(parsed),
        _tonal_consistency(parsed),
    ]
    logger.info(
        "Composition score summary",
        extra={
            "invalid_composition": False,
            "hard_constraint_compliance": hard_ok,
            "structural_compliance": structural_ok,
            "instrument_range_correctness": range_ok,
            "revision_preservation": next(
                item.status for item in readings if item.name == "revision_preservation"
            ),
            "musical_quality_claim": False,
        },
    )
    return CaseMetricsV1(metrics=readings)


def with_observed_runtime(
    metrics: CaseMetricsV1,
    *,
    generation_latency_ms: int,
    process_rss_kb: int | None,
    remote_cost_micros: int | None,
    model_calls: int | None,
    agent_calls: int | None,
    revision_passes: int | None,
    failed_stages: int | None,
    recovered_stages: int | None,
    time_to_valid_ms: int | None,
    applicable_counters: frozenset[str],
) -> CaseMetricsV1:
    """Attach latency, cost, and arm counters without treating them as taste."""
    readings = list(metrics.metrics)
    readings.append(
        _reading("generation_latency_ms", "observed", "measured", int(generation_latency_ms))
    )
    if process_rss_kb is None:
        readings.append(_reading("process_rss_kb", "observed", "unavailable", None))
    else:
        readings.append(_reading("process_rss_kb", "observed", "measured", int(process_rss_kb)))
    if remote_cost_micros is None:
        readings.append(_reading("remote_cost_micros", "observed", "unavailable", None))
    else:
        readings.append(
            _reading("remote_cost_micros", "observed", "measured", int(remote_cost_micros))
        )
    if model_calls is None:
        readings.append(_reading("model_calls", "observed", "unavailable", None))
    else:
        readings.append(_reading("model_calls", "observed", "measured", int(model_calls)))

    def _counter(name: str, value: int | None) -> MetricReadingV1:
        if name not in applicable_counters:
            return _reading(name, "counter", "not_applicable", None)
        if value is None:
            return _reading(name, "counter", "unavailable", None)
        return _reading(name, "counter", "measured", int(value))

    readings.extend(
        [
            _counter("agent_calls", agent_calls),
            _counter("revision_passes", revision_passes),
            _counter("failed_stages", failed_stages),
            _counter("recovered_stages", recovered_stages),
            _counter("time_to_valid_ms", time_to_valid_ms),
        ]
    )
    unused = set(HARD_METRIC_IDS + OBSERVED_METRIC_IDS + COUNTER_METRIC_IDS)
    for item in readings:
        unused.discard(item.name)
    if unused:
        logger.debug("Metric names not attached", extra={"metric_count": len(unused)})
    return CaseMetricsV1(metrics=readings)
