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


def is_drum_instrument_label(instrument: str) -> bool:
    """Return True when an instrument label is percussion/drums.

    Prefer `generation_constraints.normalize_instrument_family` for family matching;
    this helper stays local for cheap bound checks without circular imports.
    """
    lowered = instrument.strip().lower()
    return any(token in lowered for token in _DRUM_INSTRUMENT_TOKENS)


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


def coerce_instrumentation_labels(value: Any) -> list[str]:
    """Normalize form-plan instrumentation to instrument-family strings.

    LLMs sometimes return objects like ``{"family": "piano", "role": "melody"}``
    instead of the required string list. Extract family/instrument labels and
    preserve first-seen order; roles belong to later compose stages.
    """
    if value is None:
        return []
    if isinstance(value, str):
        label = " ".join(value.strip().split())
        return [label] if label else []
    if not isinstance(value, (list, tuple)):
        raise ValueError("instrumentation must be a list of instrument family strings")

    labels: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        label: str | None = None
        if isinstance(item, str):
            label = " ".join(item.strip().split())
        elif isinstance(item, dict):
            for key in ("family", "instrument", "name"):
                raw = item.get(key)
                if isinstance(raw, str) and raw.strip():
                    label = " ".join(raw.strip().split())
                    break
            if label is None:
                raise ValueError(
                    "instrumentation entries must be strings or objects with "
                    f"family/instrument; got object at index {index}"
                )
        else:
            raise ValueError(
                f"instrumentation entries must be strings; got {type(item).__name__} at index {index}"
            )
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


class ComposerFormPlan(BaseModel):
    tempo: int = Field(..., ge=40, le=240)
    key: str
    time_signature: str
    bar_count: int = Field(..., ge=1, le=LLM_GENERATION_MAX_BARS)
    sections: list[ComposerFormSection] = Field(..., min_length=1)
    instrumentation: list[str] = Field(default_factory=list)

    @field_validator("instrumentation", mode="before")
    @classmethod
    def validate_instrumentation(cls, value: Any) -> list[str]:
        return coerce_instrumentation_labels(value)

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


# Bounded freeform instructions projected into form/theme prompts (never logged as content).
THEME_INSTRUCTIONS_PROMPT_CHARS = 500
THEME_MAX_DEPLOYMENTS = 4
THEME_MAX_RELATIVE_NOTES = 32
THEME_MAX_SECTION_HANDOFF_CHARS = 240

ThemeOperation = Literal[
    "repeat",
    "transpose",
    "rhythmic_variation",
    "melodic_variation",
    "inversion",
    "augmentation",
    "diminution",
    "sequence",
    "answer",
    "counterphrase",
]

MECHANICAL_THEME_OPERATIONS: frozenset[str] = frozenset(
    {"repeat", "transpose", "inversion", "augmentation", "diminution", "sequence"}
)
CREATIVE_THEME_OPERATIONS: frozenset[str] = frozenset(
    {"rhythmic_variation", "melodic_variation", "answer", "counterphrase"}
)


class ComposerThemeRelativeNote(BaseModel):
    """Immutable relative motif cell — never a playable placeholder."""

    relative_start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    pitch_semitone_offset: int = Field(..., ge=-48, le=48)
    velocity: int = Field(default=80, ge=1, le=127)


class ComposerThemeParameters(BaseModel):
    transpose_semitones: int | None = Field(default=None, ge=-48, le=48)
    inversion_axis_pitch: str | None = Field(default=None, min_length=2, max_length=5)
    time_scale_numerator: int | None = Field(default=None, ge=1, le=8)
    time_scale_denominator: int | None = Field(default=None, ge=1, le=8)
    sequence_steps: int | None = Field(default=None, ge=1, le=16)
    sequence_interval_semitones: int | None = Field(default=None, ge=-24, le=24)
    sequence_step_ticks: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_time_scale_pair(self) -> ComposerThemeParameters:
        has_num = self.time_scale_numerator is not None
        has_den = self.time_scale_denominator is not None
        if has_num != has_den:
            raise ValueError("time_scale_numerator and time_scale_denominator must be set together")
        return self


class ComposerThemeDeployment(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    target_section_index: int = Field(..., ge=0)
    target_track_role: Literal["melody", "lead", "countermelody"] = "melody"
    start_bar_offset: int = Field(default=0, ge=0)
    operation: ThemeOperation
    parameters: ComposerThemeParameters = Field(default_factory=ComposerThemeParameters)
    variation_strength: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("id")
    @classmethod
    def validate_deployment_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("deployment id must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_operation_params(self) -> ComposerThemeDeployment:
        op = self.operation
        params = self.parameters
        if op in MECHANICAL_THEME_OPERATIONS and self.variation_strength is not None:
            raise ValueError("variation_strength is not applicable for mechanical theme operations")
        if op in CREATIVE_THEME_OPERATIONS and self.variation_strength is None:
            self.variation_strength = 0.5
        if op == "transpose" and params.transpose_semitones is None:
            raise ValueError("transpose requires transpose_semitones")
        if op in {"augmentation", "diminution"} and (
            params.time_scale_numerator is None or params.time_scale_denominator is None
        ):
            raise ValueError(f"{op} requires time_scale_numerator and time_scale_denominator")
        if op == "sequence" and (
            params.sequence_steps is None
            or params.sequence_interval_semitones is None
            or params.sequence_step_ticks is None
        ):
            raise ValueError("sequence requires steps, interval, and step_ticks")
        return self


class ComposerThemeSeedSpec(BaseModel):
    section_index: int = Field(..., ge=0)
    track_role: Literal["melody", "lead"] = "melody"
    start_bar_offset: int = Field(default=0, ge=0)
    bar_span: int = Field(default=1, ge=1, le=2)
    # Filled after melody seed composition; immutable for later stages.
    relative_cell: list[ComposerThemeRelativeNote] = Field(
        default_factory=list,
        max_length=THEME_MAX_RELATIVE_NOTES,
    )
    seed_event_ids: list[str] = Field(default_factory=list, max_length=THEME_MAX_RELATIVE_NOTES)
    prior_section_handoff: str | None = Field(default=None, max_length=THEME_MAX_SECTION_HANDOFF_CHARS)


class ComposerThemePlan(BaseModel):
    """Bounded structured theme plan replacing free-form ComposerMotifContext."""

    enabled: bool = False
    motif_id: str = Field(default="motif-a", min_length=1, max_length=80)
    motif_label: str = Field(default="Motif A", min_length=1, max_length=120)
    seed: ComposerThemeSeedSpec | None = None
    deployments: list[ComposerThemeDeployment] = Field(
        default_factory=list,
        max_length=THEME_MAX_DEPLOYMENTS,
    )
    truncated: bool = False
    no_theme_reason: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_plan_consistency(self) -> ComposerThemePlan:
        if not self.enabled:
            return self
        if self.seed is None:
            raise ValueError("enabled theme plan requires a seed")
        if not self.deployments:
            raise ValueError("enabled theme plan requires at least one deployment")
        dep_ids = [item.id for item in self.deployments]
        if len(dep_ids) != len(set(dep_ids)):
            raise ValueError("theme deployment ids must be unique")
        return self


# Back-compat alias for any residual imports during staged migration.
ComposerMotifContext = ComposerThemePlan


def bounded_instructions_for_prompt(instructions: str | None) -> str | None:
    """Return truncated instructions for provider prompts; callers must not log the text."""
    if instructions is None:
        return None
    text = " ".join(instructions.strip().split())
    if not text:
        return None
    if len(text) <= THEME_INSTRUCTIONS_PROMPT_CHARS:
        return text
    return text[: THEME_INSTRUCTIONS_PROMPT_CHARS - 1] + "…"


def summarize_theme_plan(plan: ComposerThemePlan | None) -> dict[str, Any]:
    """Sanitized theme-plan summary — never includes relative cells or instruction text."""
    if plan is None:
        return {"present": False, "enabled": False}
    seed = plan.seed
    return {
        "present": True,
        "enabled": plan.enabled,
        "motif_id": plan.motif_id if plan.enabled else None,
        "deployment_count": len(plan.deployments),
        "deployment_operations": [item.operation for item in plan.deployments],
        "seed_section_index": seed.section_index if seed else None,
        "seed_bar_span": seed.bar_span if seed else None,
        "relative_cell_count": len(seed.relative_cell) if seed else 0,
        "seed_event_id_count": len(seed.seed_event_ids) if seed else 0,
        "truncated": plan.truncated,
        "no_theme_reason": plan.no_theme_reason,
    }


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
        if is_drum_instrument_label(instrument):
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
