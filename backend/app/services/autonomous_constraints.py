"""Completion checks for an autonomous composition stage.

Failures carry a public code. Note text stays out of the error and the log.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.autonomous_composer_schemas import (
    AUTONOMOUS_CONSTRAINT_FAILED,
    ProjectPlanV1,
)
from app.composition_schemas import CompositionV2, midi_pitch_number
from app.services.composition_timing import bar_duration_ticks
from app.services.composition_tonality import parse_key
from app.services.composition_validator import validate_composition_integrity
from app.services.generation_constraints import (
    GenerationConstraints,
    LockedSectionConstraint,
    validate_generation_constraints,
)
from app.services.instrument_identity import analyze_instrumentation, normalize_instrument

logger = logging.getLogger(__name__)

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)


class AutonomousConstraintError(ValueError):
    """A completion code failed. The stage must not commit."""

    def __init__(
        self,
        message: str,
        *,
        completion_code: str,
        recoverable: int = 0,
        code: str = AUTONOMOUS_CONSTRAINT_FAILED,
    ) -> None:
        super().__init__(message)
        self.completion_code = completion_code
        self.recoverable = recoverable
        self.code = code


@dataclass(frozen=True)
class NoteRhythm:
    start_tick: int
    duration_ticks: int


def constraints_from_project_plan(plan: ProjectPlanV1) -> GenerationConstraints:
    sections = tuple(
        LockedSectionConstraint(
            type=section.type,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
        )
        for section in plan.sections
    )
    instruments = tuple(plan.constraints.instruments)
    return GenerationConstraints(
        key=plan.constraints.opening_key,
        key_user_specified=True,
        time_signature=plan.constraints.time_signature,
        duration_bars=plan.constraints.duration_bars,
        tempo_min=plan.constraints.tempo_min,
        tempo_max=plan.constraints.tempo_max,
        sections=sections,
        sections_user_specified=True,
        required_instrument_families=instruments,
        requested_instruments=instruments,
        allow_extra_instrument_families=False,
        mood="",
        genre="",
        complexity="moderate",
        has_instructions=False,
    )


def assert_stage_completion(
    code: str,
    *,
    composition: CompositionV2 | None = None,
    plan: ProjectPlanV1 | None = None,
    motif_rows_present: bool = False,
    rhythm_before: tuple[NoteRhythm, ...] = (),
    rhythm_after: tuple[NoteRhythm, ...] = (),
    outro_pitches: tuple[str, ...] = (),
) -> None:
    """Raise ``AutonomousConstraintError`` when ``code`` does not hold."""
    logger.debug("Checking autonomous completion code", extra={"completion_code": code})
    if code == "composition_v2_ok":
        if composition is None:
            _fail(code)
        result = validate_composition_integrity(composition, profile="canonical")
        if not result.ok:
            logger.warning(
                "Autonomous constraint failed",
                extra={"completion_code": code},
            )
            _fail(code)
        return
    if code == "hard_constraints_ok":
        if composition is None or plan is None:
            _fail(code)
        report = validate_generation_constraints(
            composition,
            constraints_from_project_plan(plan),
        )
        if report.status == "failed":
            logger.warning(
                "Autonomous constraint failed",
                extra={"completion_code": code},
            )
            _fail(code)
        return
    if code == "no_forbidden_instruments":
        if composition is None or plan is None:
            _fail(code)
        _assert_no_forbidden(composition, plan)
        return
    if code == "motif_identity_ok":
        if plan is not None and plan.constraints.motif_must_remain_recognizable:
            if not motif_rows_present:
                raise AutonomousConstraintError(
                    "motif rows missing",
                    completion_code=code,
                    code="autonomous_motif_missing",
                )
        return
    if code == "final_section_mode_ok":
        if plan is None or not plan.constraints.final_section_key:
            return
        if rhythm_before != rhythm_after:
            logger.warning("Autonomous constraint failed", extra={"completion_code": code})
            _fail(code)
        if not _pitches_on_key(outro_pitches, plan.constraints.final_section_key):
            logger.warning("Autonomous constraint failed", extra={"completion_code": code})
            _fail(code)
        return
    logger.debug("Completion code deferred to a later stage", extra={"completion_code": code})


def pitch_rhythm(events: list) -> tuple[NoteRhythm, ...]:
    return tuple(
        NoteRhythm(start_tick=int(event.start_tick), duration_ticks=int(event.duration_ticks))
        for event in events
    )


def scale_pcs(key: str) -> set[int]:
    parsed = parse_key(key)
    if parsed is None:
        return set()
    intervals = _MAJOR_SCALE if parsed.mode == "major" else (0, 2, 3, 5, 7, 8, 10)
    return {(parsed.tonic_pc + interval) % 12 for interval in intervals}


def section_start_tick(composition: CompositionV2, start_bar: int) -> int:
    ticks = bar_duration_ticks(composition.time_signature, composition.ticks_per_quarter)
    return (start_bar - 1) * ticks


def _assert_no_forbidden(composition: CompositionV2, plan: ProjectPlanV1) -> None:
    forbidden = set(plan.constraints.forbidden_instrument_families)
    for track in composition.tracks:
        if getattr(track, "is_drum", False):
            logger.warning(
                "Autonomous constraint failed",
                extra={"completion_code": "no_forbidden_instruments"},
            )
            _fail("no_forbidden_instruments")
        normalized = normalize_instrument(track.instrument)
        if normalized is not None and (
            normalized.family in forbidden or normalized.identity in forbidden
        ):
            logger.warning(
                "Autonomous constraint failed",
                extra={"completion_code": "no_forbidden_instruments"},
            )
            _fail("no_forbidden_instruments")
    analysis = analyze_instrumentation(
        list(plan.constraints.instruments),
        composition.tracks,
        allow_extra=False,
    )
    for identity in analysis.present_identities:
        if identity in forbidden:
            _fail("no_forbidden_instruments")


def _pitches_on_key(pitches: tuple[str, ...], key: str) -> bool:
    allowed = scale_pcs(key)
    if not pitches or not allowed:
        return False
    for pitch in pitches:
        if midi_pitch_number(pitch) % 12 not in allowed:
            return False
    return True


def _fail(code: str) -> None:
    raise AutonomousConstraintError("completion check failed", completion_code=code)
