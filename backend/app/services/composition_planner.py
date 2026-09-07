"""Internal staged-composer contracts and LLM generation request bounds."""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..schemas import (
    KEY_PATTERN,
    LLMMusicGenerationRequest,
    SUPPORTED_SECTION_TYPES,
    SUPPORTED_TRACK_ROLES,
    _midi_pitch_number,
)


logger = logging.getLogger(__name__)

LLM_GENERATION_MAX_BARS = 32
LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS = 6
# Rough event budget: bars * instruments * notes-per-bar at moderate density.
LLM_GENERATION_MAX_EVENT_BUDGET = LLM_GENERATION_MAX_BARS * LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS * 8

_DRUM_INSTRUMENT_TOKENS = ("drum", "drums", "percussion", "perc")


class OversizedLLMGenerationRequestError(ValueError):
    """Raised when an LLM generation request exceeds practical staged-composer bounds."""


class ComposerFormSection(BaseModel):
    type: str
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1)
    intensity: str | None = Field(default=None, max_length=120)
    dynamic_notes: str | None = Field(default=None, max_length=240)

    @field_validator("type")
    @classmethod
    def validate_section_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_SECTION_TYPES:
            raise ValueError(f"Unsupported section type: {value}")
        return normalized


class ComposerFormPlan(BaseModel):
    tempo: int = Field(..., ge=40, le=240)
    key: str
    time_signature: str
    bar_count: int = Field(..., ge=1, le=LLM_GENERATION_MAX_BARS)
    sections: list[ComposerFormSection] = Field(..., min_length=1)
    instrumentation: list[str] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            raise ValueError("Key must use format like 'C minor' or 'F# major'")
        return key

    @model_validator(mode="after")
    def validate_contiguous_sections(self) -> "ComposerFormPlan":
        expected_start = 1
        covered = 0
        for section in self.sections:
            if section.start_bar != expected_start:
                raise ValueError(
                    f"Sections must be contiguous; expected start_bar={expected_start}, got {section.start_bar}"
                )
            expected_start = section.start_bar + section.bar_count
            covered += section.bar_count
        if covered != self.bar_count:
            raise ValueError(
                f"Section bar totals ({covered}) must equal form bar_count ({self.bar_count})"
            )
        return self


class ComposerHarmonyEvent(BaseModel):
    bar: int = Field(..., ge=1)
    chord: str = Field(..., min_length=1, max_length=32)
    section_type: str | None = None
    cadence: str | None = Field(default=None, max_length=64)
    function: str | None = Field(default=None, max_length=64)


class ComposerHarmonyPlan(BaseModel):
    events: list[ComposerHarmonyEvent] = Field(default_factory=list)


class ComposerMotifContext(BaseModel):
    motif_ids: list[str] = Field(default_factory=list)
    interval_cells: list[str] = Field(default_factory=list)
    rhythm_cells: list[str] = Field(default_factory=list)
    section_notes: list[str] = Field(default_factory=list)
    handoff: str | None = Field(default=None, max_length=400)


class ComposerDraftNote(BaseModel):
    pitch: str = Field(..., min_length=2, max_length=5)
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    velocity: int = Field(default=80, ge=1, le=127)
    staff: Literal["treble", "bass"] | None = None
    id: str | None = Field(default=None, max_length=120)

    @field_validator("pitch")
    @classmethod
    def validate_draft_pitch(cls, value: str) -> str:
        pitch = value.strip()
        try:
            _midi_pitch_number(pitch)
        except ValueError as exc:
            logger.debug(
                "Composer draft pitch rejected",
                extra={"pitch": value, "reason": str(exc)[:200]},
            )
            raise ValueError(str(exc)) from exc
        return pitch


class ComposerTrackDraft(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    instrument: str = Field(..., min_length=1, max_length=80)
    role: str
    midi_program: int | None = Field(default=None, ge=0, le=127)
    channel: int | None = Field(default=None, ge=1, le=16)
    is_drum: bool = False
    volume: int = Field(default=100, ge=0, le=127)
    pan: int = Field(default=0, ge=-64, le=63)
    staff: Literal["treble", "bass", "grand"] | None = None
    events: list[ComposerDraftNote] = Field(default_factory=list)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            raise ValueError(f"Unsupported track role: {value}")
        return normalized


class ValidationDiagnostic(BaseModel):
    code: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1, max_length=500)
    severity: Literal["error", "warning"] = "error"
    context: dict[str, Any] = Field(default_factory=dict)


def count_non_drum_instruments(instruments: list[str]) -> int:
    count = 0
    for instrument in instruments:
        lowered = instrument.strip().lower()
        if any(token in lowered for token in _DRUM_INSTRUMENT_TOKENS):
            continue
        count += 1
    return count


def approximate_event_budget(bar_count: int, instrument_count: int, complexity: str) -> int:
    density = {"simple": 4, "moderate": 8, "complex": 12}.get(complexity, 8)
    return max(0, bar_count) * max(0, instrument_count) * density


def enforce_llm_generation_bounds(request: LLMMusicGenerationRequest) -> None:
    """Reject oversized initial LLM Composition V1 requests before any provider calls."""
    prompt = request.prompt
    requested_bars = prompt.duration_bars
    requested_instruments = list(prompt.instruments)
    non_drum_count = count_non_drum_instruments(requested_instruments)
    event_budget = approximate_event_budget(requested_bars, non_drum_count, prompt.complexity)

    logger.debug(
        "Checking LLM generation request bounds",
        extra={
            "requested_bars": requested_bars,
            "requested_instrument_count": len(requested_instruments),
            "non_drum_instrument_count": non_drum_count,
            "complexity": prompt.complexity,
            "approximate_event_budget": event_budget,
            "max_bars": LLM_GENERATION_MAX_BARS,
            "max_non_drum_instruments": LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS,
            "max_event_budget": LLM_GENERATION_MAX_EVENT_BUDGET,
        },
    )

    violations: list[str] = []
    if requested_bars > LLM_GENERATION_MAX_BARS:
        violations.append(f"bars={requested_bars}>{LLM_GENERATION_MAX_BARS}")
    if non_drum_count > LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS:
        violations.append(
            f"instruments={non_drum_count}>{LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS}"
        )
    if event_budget > LLM_GENERATION_MAX_EVENT_BUDGET:
        violations.append(f"event_budget={event_budget}>{LLM_GENERATION_MAX_EVENT_BUDGET}")

    if not violations:
        logger.info(
            "LLM generation request within staged composer bounds",
            extra={
                "requested_bars": requested_bars,
                "non_drum_instrument_count": non_drum_count,
                "complexity": prompt.complexity,
                "approximate_event_budget": event_budget,
            },
        )
        return

    message = (
        "Initial LLM Composition V1 generation supports up to "
        f"{LLM_GENERATION_MAX_BARS} bars and {LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS} instruments; "
        "reduce duration or instrumentation."
    )
    logger.warning(
        "Rejected oversized LLM generation request",
        extra={
            "code": "oversized_generation_request",
            "requested_bars": requested_bars,
            "requested_instrument_count": len(requested_instruments),
            "non_drum_instrument_count": non_drum_count,
            "complexity": prompt.complexity,
            "approximate_event_budget": event_budget,
            "max_bars": LLM_GENERATION_MAX_BARS,
            "max_non_drum_instruments": LLM_GENERATION_MAX_NON_DRUM_INSTRUMENTS,
            "max_event_budget": LLM_GENERATION_MAX_EVENT_BUDGET,
            "violations": violations,
            "provider": request.selection.provider,
            "model": request.selection.model,
        },
    )
    raise OversizedLLMGenerationRequestError(message)


def summarize_form_plan(form: ComposerFormPlan | None) -> dict[str, Any]:
    if form is None:
        return {"present": False}
    return {
        "present": True,
        "bar_count": form.bar_count,
        "section_count": len(form.sections),
        "section_types": [section.type for section in form.sections],
        "tempo": form.tempo,
        "key": form.key,
        "time_signature": form.time_signature,
        "instrumentation_count": len(form.instrumentation),
    }


def summarize_track_draft(draft: ComposerTrackDraft | None) -> dict[str, Any]:
    if draft is None:
        return {"present": False}
    pitches = [event.pitch for event in draft.events]
    return {
        "present": True,
        "id": draft.id,
        "role": draft.role,
        "instrument": draft.instrument,
        "event_count": len(draft.events),
        "pitch_min": min(pitches) if pitches else None,
        "pitch_max": max(pitches) if pitches else None,
    }


def summarize_diagnostics(diagnostics: list[ValidationDiagnostic] | None) -> dict[str, Any]:
    items = diagnostics or []
    return {
        "count": len(items),
        "error_codes": [item.code for item in items if item.severity == "error"],
        "warning_codes": [item.code for item in items if item.severity == "warning"],
    }
