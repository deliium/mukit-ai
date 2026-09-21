"""Strict composition.plan.v1 DTOs — high-level musical intent only.

CompositionPlan is **not** a playable score. Canonical playable notes live only on
``composition.v2`` ``tracks[].events[]``. Relative motif cells on the theme plan are
planning aids, not audible placeholders.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .schemas import KEY_PATTERN, SUPPORTED_TRACK_ROLES
from .services.composition_planner import (
    THEME_INSTRUCTIONS_PROMPT_CHARS,
    ComposerFormPlan,
    ComposerHarmonyPlan,
    ComposerThemePlan,
    bounded_instructions_for_prompt,
)


logger = logging.getLogger(__name__)

COMPOSITION_PLAN_SCHEMA_VERSION: Literal["composition.plan.v1"] = "composition.plan.v1"
PLAN_DIGEST_LOG_PREFIX_LEN = 12
PLAN_MAX_MODULATION_TARGETS = 16
PLAN_MAX_INSTRUMENTATION_HINTS = 12
PLAN_MAX_TARGET_RANGES = 12
PLAN_MAX_SECTION_DENSITY = 32
PLAN_CONSTRAINTS_DIGEST_MAX_LEN = 128

# Fields that would turn the plan into a competing playable representation.
_FORBIDDEN_PLAYABLE_TOP_LEVEL = frozenset(
    {
        "tracks",
        "musicxml",
        "midi",
        "wav",
        "analysis",
        "composition",
        "music",
        "events",  # top-level absolute events (harmony.events is nested chord timeline)
        "note_events",
        "notes",
    }
)

DensityBand = Literal["sparse", "moderate", "dense"]

CompositionPlanErrorCode = Literal[
    "plan_invalid",
    "plan_constraint_mismatch",
    "plan_empty_sections",
    "plan_forbidden_playable_fields",
    "plan_schema_version_mismatch",
    "plan_modulation_key_invalid",
    "plan_target_range_invalid",
    "plan_density_section_oob",
]

COMPOSITION_PLAN_ERROR_CODES: dict[str, str] = {
    "plan_invalid": "Composition plan failed schema or structural validation.",
    "plan_constraint_mismatch": "Composition plan contradicts hard generation constraints.",
    "plan_empty_sections": "Composition plan form has no sections.",
    "plan_forbidden_playable_fields": "Composition plan must not contain playable score fields.",
    "plan_schema_version_mismatch": "Unexpected composition.plan schema_version.",
    "plan_modulation_key_invalid": "Modulation target key is not a supported key label.",
    "plan_target_range_invalid": "Target MIDI pitch window is empty or inverted.",
    "plan_density_section_oob": "Per-section density index is out of form section bounds.",
}


class CompositionPlanError(ValueError):
    """Domain error for composition.plan.v1 validation / conformance failures."""

    def __init__(
        self,
        message: str,
        *,
        code: CompositionPlanErrorCode = "plan_invalid",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: CompositionPlanErrorCode = code
        self.context = dict(context or {})


class PlanModulationTarget(BaseModel):
    """Optional soft key target for a form section (not a playable timeline)."""

    model_config = ConfigDict(extra="forbid")

    section_index: int = Field(..., ge=0)
    key: str
    start_bar: int | None = Field(default=None, ge=1)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            raise ValueError("Key must use format like 'C minor' or 'F# major'")
        return key


class PlanModulation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targets: list[PlanModulationTarget] = Field(
        default_factory=list,
        max_length=PLAN_MAX_MODULATION_TARGETS,
    )


class PlanInstrumentationHint(BaseModel):
    """Family / role guidance — not arrangement catalog IDs or V2 track rows."""

    model_config = ConfigDict(extra="forbid")

    family: str = Field(..., min_length=1, max_length=80)
    role: str | None = Field(default=None, max_length=40)
    label: str | None = Field(default=None, max_length=120)

    @field_validator("family", "label", mode="before")
    @classmethod
    def strip_optional_text(cls, value: Any) -> Any:
        if isinstance(value, str):
            return " ".join(value.strip().split()) or None
        return value

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            raise ValueError(f"Unsupported track role: {value}")
        return normalized


class PlanInstrumentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hints: list[PlanInstrumentationHint] = Field(
        default_factory=list,
        max_length=PLAN_MAX_INSTRUMENTATION_HINTS,
    )


class PlanSectionDensity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_index: int = Field(..., ge=0)
    band: DensityBand = "moderate"


class PlanDensity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    global_band: DensityBand = "moderate"
    per_section: list[PlanSectionDensity] = Field(
        default_factory=list,
        max_length=PLAN_MAX_SECTION_DENSITY,
    )


class PlanTargetRange(BaseModel):
    """Soft MIDI pitch window guidance per role (conditioning only)."""

    model_config = ConfigDict(extra="forbid")

    role: str
    midi_min: int = Field(..., ge=0, le=127)
    midi_max: int = Field(..., ge=0, le=127)

    @field_validator("role")
    @classmethod
    def validate_role(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            raise ValueError(f"Unsupported track role: {value}")
        return normalized

    @model_validator(mode="after")
    def validate_window(self) -> PlanTargetRange:
        if self.midi_min > self.midi_max:
            raise ValueError("midi_min must be <= midi_max")
        return self


class CompositionPlan(BaseModel):
    """Versioned non-playable composition plan envelope (composition.plan.v1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["composition.plan.v1"] = COMPOSITION_PLAN_SCHEMA_VERSION
    form: ComposerFormPlan
    modulation: PlanModulation = Field(default_factory=PlanModulation)
    harmony: ComposerHarmonyPlan = Field(default_factory=ComposerHarmonyPlan)
    instrumentation: PlanInstrumentation = Field(default_factory=PlanInstrumentation)
    motifs_themes: ComposerThemePlan = Field(default_factory=ComposerThemePlan)
    density: PlanDensity = Field(default_factory=PlanDensity)
    target_ranges: list[PlanTargetRange] = Field(
        default_factory=list,
        max_length=PLAN_MAX_TARGET_RANGES,
    )
    stylistic_instructions: str | None = Field(
        default=None,
        max_length=THEME_INSTRUCTIONS_PROMPT_CHARS,
    )
    constraints_digest: str | None = Field(
        default=None,
        max_length=PLAN_CONSTRAINTS_DIGEST_MAX_LEN,
    )

    @field_validator("stylistic_instructions", mode="before")
    @classmethod
    def bound_stylistic_instructions(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("stylistic_instructions must be a string")
        return bounded_instructions_for_prompt(value)

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_playable_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        forbidden = sorted(_FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
        if forbidden:
            logger.error(
                "Composition plan rejected: forbidden playable fields",
                extra={
                    "code": "plan_forbidden_playable_fields",
                    "schema_version": COMPOSITION_PLAN_SCHEMA_VERSION,
                    "forbidden_fields": forbidden,
                },
            )
            raise ValueError(
                "Composition plan must not include playable score fields: "
                + ", ".join(forbidden)
            )
        # Nested absolute note-event payloads under form (never allowed).
        form = data.get("form")
        if isinstance(form, dict):
            nested_forbidden = sorted(
                {"tracks", "events", "notes", "note_events"}.intersection(form.keys())
            )
            if nested_forbidden:
                raise ValueError(
                    "Composition plan form must not include playable fields: "
                    + ", ".join(nested_forbidden)
                )
        return data

    @model_validator(mode="after")
    def validate_cross_field_bounds(self) -> CompositionPlan:
        section_count = len(self.form.sections)
        if section_count < 1:
            raise ValueError("Composition plan form requires at least one section")

        for target in self.modulation.targets:
            if target.section_index >= section_count:
                raise ValueError(
                    f"modulation section_index {target.section_index} out of bounds "
                    f"(section_count={section_count})"
                )

        for item in self.density.per_section:
            if item.section_index >= section_count:
                raise ValueError(
                    f"density section_index {item.section_index} out of bounds "
                    f"(section_count={section_count})"
                )

        logger.info(
            "Composition plan schema accepted",
            extra={
                "schema_version": self.schema_version,
                "bar_count": self.form.bar_count,
                "section_count": section_count,
                "has_constraints_digest": bool(self.constraints_digest),
            },
        )
        logger.debug(
            "Composition plan field presence",
            extra=composition_plan_field_counts(self),
        )
        return self


def composition_plan_field_counts(plan: CompositionPlan) -> dict[str, Any]:
    """Sanitized field-presence counts for DEBUG logs (no instruction / cell payloads)."""
    theme = plan.motifs_themes
    seed = theme.seed
    return {
        "schema_version": plan.schema_version,
        "section_count": len(plan.form.sections),
        "instrumentation_form_count": len(plan.form.instrumentation),
        "instrumentation_hint_count": len(plan.instrumentation.hints),
        "harmony_event_count": len(plan.harmony.events),
        "modulation_target_count": len(plan.modulation.targets),
        "theme_enabled": theme.enabled,
        "theme_deployment_count": len(theme.deployments),
        "theme_relative_cell_count": len(seed.relative_cell) if seed else 0,
        "density_per_section_count": len(plan.density.per_section),
        "target_range_count": len(plan.target_ranges),
        "has_stylistic_instructions": bool(plan.stylistic_instructions),
        "stylistic_instructions_length": len(plan.stylistic_instructions or ""),
        "has_constraints_digest": bool(plan.constraints_digest),
    }


def parse_composition_plan(data: Any) -> CompositionPlan:
    """Parse and validate a composition.plan.v1 payload.

    Raises:
        CompositionPlanError: with a closed error code on validation failure.
    """
    logger.debug(
        "Parsing composition plan",
        extra={
            "schema_version": COMPOSITION_PLAN_SCHEMA_VERSION,
            "payload_type": type(data).__name__,
            "top_level_keys": sorted(data.keys()) if isinstance(data, dict) else None,
        },
    )
    try:
        if isinstance(data, CompositionPlan):
            return data
        plan = CompositionPlan.model_validate(data)
    except ValidationError as exc:
        code = _classify_plan_validation_error(exc)
        logger.error(
            "Composition plan parse failed",
            extra={
                "code": code,
                "error_count": exc.error_count(),
                "error_types": sorted({err.get("type", "") for err in exc.errors()}),
            },
        )
        raise CompositionPlanError(
            COMPOSITION_PLAN_ERROR_CODES.get(code, str(exc)),
            code=code,
            context={"error_count": exc.error_count()},
        ) from exc
    except ValueError as exc:
        message = str(exc)
        code: CompositionPlanErrorCode = "plan_invalid"
        if "playable" in message.lower() or "forbidden" in message.lower():
            code = "plan_forbidden_playable_fields"
        elif "section" in message.lower() and "at least one" in message.lower():
            code = "plan_empty_sections"
        logger.error(
            "Composition plan rejected",
            extra={"code": code, "detail": message[:200]},
        )
        raise CompositionPlanError(message, code=code) from exc
    return plan


def _classify_plan_validation_error(exc: ValidationError) -> CompositionPlanErrorCode:
    for err in exc.errors():
        loc = err.get("loc") or ()
        msg = str(err.get("msg", "")).lower()
        if loc and loc[0] in _FORBIDDEN_PLAYABLE_TOP_LEVEL:
            return "plan_forbidden_playable_fields"
        if "playable" in msg or "forbidden" in msg:
            return "plan_forbidden_playable_fields"
        if "schema_version" in loc:
            return "plan_schema_version_mismatch"
        if "midi_min" in loc or "midi_max" in loc or "inverted" in msg:
            return "plan_target_range_invalid"
        if "modulation" in loc and "key" in msg:
            return "plan_modulation_key_invalid"
        if "section_index" in msg or "out of bounds" in msg:
            return "plan_density_section_oob"
        if "sections" in loc and ("at least" in msg or "min_length" in str(err.get("type", ""))):
            return "plan_empty_sections"
    return "plan_invalid"


def summarize_composition_plan(plan: CompositionPlan | None) -> dict[str, Any]:
    """Response/log-safe plan summary — never includes instructions or relative cells."""
    if plan is None:
        return {"present": False, "schema_version": None}
    digest = plan.constraints_digest or ""
    return {
        "present": True,
        "schema_version": plan.schema_version,
        "bar_count": plan.form.bar_count,
        "tempo": plan.form.tempo,
        "key": plan.form.key,
        "time_signature": plan.form.time_signature,
        "section_count": len(plan.form.sections),
        "section_types": [section.type for section in plan.form.sections],
        "harmony_event_count": len(plan.harmony.events),
        "theme_enabled": plan.motifs_themes.enabled,
        "density_global": plan.density.global_band,
        "constraints_digest_prefix": digest[:PLAN_DIGEST_LOG_PREFIX_LEN] if digest else None,
        **composition_plan_field_counts(plan),
    }


__all__ = [
    "COMPOSITION_PLAN_ERROR_CODES",
    "COMPOSITION_PLAN_SCHEMA_VERSION",
    "CompositionPlan",
    "CompositionPlanError",
    "CompositionPlanErrorCode",
    "DensityBand",
    "PlanDensity",
    "PlanInstrumentation",
    "PlanInstrumentationHint",
    "PlanModulation",
    "PlanModulationTarget",
    "PlanSectionDensity",
    "PlanTargetRange",
    "composition_plan_field_counts",
    "parse_composition_plan",
    "summarize_composition_plan",
]
