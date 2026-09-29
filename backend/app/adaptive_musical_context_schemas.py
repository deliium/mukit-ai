"""Strict adaptive musical-context documents.

``adaptive.context.external.v1`` is a flat sample. ``adaptive.context.mapping.v1``
is session configuration. ``adaptive.musical_context.v1`` is the closed view after
mapping and smoothing. None of these documents is ``adaptive.score.v2`` or
``composition.v5``, and none of them stores note events.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic import BaseModel

from app.adaptive_musical_context_settings import load_adaptive_musical_context_settings
from app.adaptive_score_schemas import (
    ADAPTIVE_SCORE_ERROR_CODES,
    FORBIDDEN_NOTE_KEYS,
    AdaptiveScoreError,
    AdaptiveTransitionScheduleWarningV1,
    _FLAG_RE,
    _ID_RE,
    _reject_bool,
    log_adaptive_schema_failure,
)

logger = logging.getLogger(__name__)

ADAPTIVE_CONTEXT_EXTERNAL_SCHEMA: Literal["adaptive.context.external.v1"] = (
    "adaptive.context.external.v1"
)
ADAPTIVE_CONTEXT_MAPPING_SCHEMA: Literal["adaptive.context.mapping.v1"] = (
    "adaptive.context.mapping.v1"
)
ADAPTIVE_MUSICAL_CONTEXT_SCHEMA: Literal["adaptive.musical_context.v1"] = (
    "adaptive.musical_context.v1"
)

CONTEXT_SCHEMA_MESSAGES: dict[str, str] = {
    ADAPTIVE_CONTEXT_EXTERNAL_SCHEMA: "schema_version must be adaptive.context.external.v1.",
    ADAPTIVE_CONTEXT_MAPPING_SCHEMA: "schema_version must be adaptive.context.mapping.v1.",
    ADAPTIVE_MUSICAL_CONTEXT_SCHEMA: "schema_version must be adaptive.musical_context.v1.",
}
HYSTERESIS_GAP_MESSAGE = "Numeric band enter and exit are closer than the hysteresis gap."
CONTEXT_NOT_RUNNING_MESSAGE = "Musical context is not running for this score."

_CONTEXT_ID_RE = re.compile(r"^actx_[0-9a-f]{8}$")
_EXTERNAL_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")
_CLAMPED_NUMERIC_SLOTS = frozenset({"tension", "health", "danger", "intensity"})

ContextNumericSlot = Literal["tension", "health", "danger", "intensity", "custom_numeric"]
ContextBooleanSlot = Literal["custom_boolean", "character"]
ContextCategoricalSlot = Literal["state", "location", "custom_categorical"]
ContextNumericTransform = Literal["identity", "invert", "clamp01", "scale"]
ContextBandPolarity = Literal["high", "low"]
ContextFlagSource = Literal["custom_boolean", "character", "tag"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _cap(token: str) -> str:
    return f"cap_exceeded:{token}"


def _flag_token(value: str, *, model: str, field: str) -> str:
    if not _FLAG_RE.fullmatch(value):
        log_adaptive_schema_failure(model, field, "adaptive_score_invalid")
        raise ValueError(f"{field} must match the flag pattern")
    return value


def _score_state_token(value: str, *, model: str, field: str) -> str:
    if not _ID_RE.fullmatch(value):
        log_adaptive_schema_failure(model, field, "adaptive_score_invalid")
        raise ValueError(f"{field} must be a score state id")
    return value


def _external_key(value: str, *, model: str, field: str) -> str:
    if not _EXTERNAL_KEY_RE.fullmatch(value):
        log_adaptive_schema_failure(model, field, "adaptive_score_invalid")
        raise ValueError(f"{field} must be an external key")
    return value


def _rule_id(value: str, *, model: str, field: str) -> str:
    if not _RULE_ID_RE.fullmatch(value):
        log_adaptive_schema_failure(model, field, "adaptive_score_invalid")
        raise ValueError(f"{field} must be a rule id")
    return value


class AdaptiveContextExternalV1(_Strict):
    schema_version: Literal["adaptive.context.external.v1"] = ADAPTIVE_CONTEXT_EXTERNAL_SCHEMA
    source_id: str | None = Field(default=None, min_length=1, max_length=64)
    observed_at_ms: int | None = Field(default=None, ge=0)
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at_ms", mode="before")
    @classmethod
    def observed_int(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="observed_at_ms")
        return value

    @field_validator("values")
    @classmethod
    def flat_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        settings = load_adaptive_musical_context_settings()
        if len(value) > settings.max_values:
            log_adaptive_schema_failure(cls.__name__, "values", "adaptive_score_too_large")
            raise ValueError(_cap("values"))
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not _EXTERNAL_KEY_RE.fullmatch(key):
                log_adaptive_schema_failure(cls.__name__, "values", "adaptive_score_invalid")
                raise ValueError("values key must be an external key")
            if key in FORBIDDEN_NOTE_KEYS:
                log_adaptive_schema_failure(cls.__name__, "values", "embedded_note_material")
                raise ValueError("embedded_note_material")
            cleaned[key] = _scalar_value(item, model=cls.__name__)
        return cleaned


def _scalar_value(item: object, *, model: str) -> object:
    if isinstance(item, bool):
        return item
    if isinstance(item, (int, float)):
        _reject_bool(item, model=model, field="values")
        if not math.isfinite(float(item)):
            log_adaptive_schema_failure(model, "values", "adaptive_score_invalid")
            raise ValueError("values entry must be finite")
        return item
    if isinstance(item, str):
        if not 1 <= len(item) <= 80:
            log_adaptive_schema_failure(model, "values", "adaptive_score_invalid")
            raise ValueError("values string length is invalid")
        return item
    if isinstance(item, list):
        if not 1 <= len(item) <= 32:
            log_adaptive_schema_failure(model, "values", "adaptive_score_invalid")
            raise ValueError("values list length is invalid")
        for entry in item:
            if not isinstance(entry, str) or isinstance(entry, bool) or not 1 <= len(entry) <= 80:
                log_adaptive_schema_failure(model, "values", "adaptive_score_invalid")
                raise ValueError("values list entries must be short strings")
        return list(item)
    log_adaptive_schema_failure(model, "values", "adaptive_score_invalid")
    raise ValueError("values entry must be a flat scalar")


class AdaptiveContextNumericBindingV1(_Strict):
    id: str
    kind: Literal["numeric"]
    external_key: str
    slot: ContextNumericSlot
    custom_key: str | None = None
    transform: ContextNumericTransform = "identity"
    in_min: float | None = None
    in_max: float | None = None
    smooth_alpha: float = 1

    @field_validator("id")
    @classmethod
    def binding_id(cls, value: str) -> str:
        return _rule_id(value, model=cls.__name__, field="id")

    @field_validator("external_key")
    @classmethod
    def key_token(cls, value: str) -> str:
        return _external_key(value, model=cls.__name__, field="external_key")

    @field_validator("custom_key")
    @classmethod
    def custom_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="custom_key")

    @field_validator("in_min", "in_max", "smooth_alpha", mode="before")
    @classmethod
    def numeric_fields(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="smooth_alpha")
        return value

    @model_validator(mode="after")
    def numeric_shape(self) -> AdaptiveContextNumericBindingV1:
        if not 0 < self.smooth_alpha <= 1:
            log_adaptive_schema_failure(self.__class__.__name__, "smooth_alpha", "adaptive_score_invalid")
            raise ValueError("smooth_alpha must sit in (0, 1]")
        if not math.isfinite(self.smooth_alpha):
            log_adaptive_schema_failure(self.__class__.__name__, "smooth_alpha", "adaptive_score_invalid")
            raise ValueError("smooth_alpha must be finite")
        if self.slot == "custom_numeric" and self.custom_key is None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_numeric requires custom_key")
        if self.slot != "custom_numeric" and self.custom_key is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_key belongs on custom_numeric")
        if self.transform == "scale":
            if self.in_min is None or self.in_max is None:
                log_adaptive_schema_failure(self.__class__.__name__, "in_max", "adaptive_score_invalid")
                raise ValueError("scale requires in_min and in_max")
            if not math.isfinite(self.in_min) or not math.isfinite(self.in_max) or self.in_max <= self.in_min:
                log_adaptive_schema_failure(self.__class__.__name__, "in_max", "adaptive_score_invalid")
                raise ValueError("in_max must be greater than in_min")
        elif self.in_min is not None or self.in_max is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "in_min", "adaptive_score_invalid")
            raise ValueError("in_min is only valid for scale")
        return self


class AdaptiveContextBooleanBindingV1(_Strict):
    id: str
    kind: Literal["boolean"]
    external_key: str
    slot: ContextBooleanSlot
    custom_key: str | None = None
    character_id: str | None = None

    @field_validator("id")
    @classmethod
    def binding_id(cls, value: str) -> str:
        return _rule_id(value, model=cls.__name__, field="id")

    @field_validator("external_key")
    @classmethod
    def key_token(cls, value: str) -> str:
        return _external_key(value, model=cls.__name__, field="external_key")

    @field_validator("custom_key", "character_id")
    @classmethod
    def flag_fields(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="custom_key")

    @model_validator(mode="after")
    def boolean_shape(self) -> AdaptiveContextBooleanBindingV1:
        if self.slot == "character":
            if self.character_id is None:
                log_adaptive_schema_failure(self.__class__.__name__, "character_id", "adaptive_score_invalid")
                raise ValueError("character requires character_id")
            if self.custom_key is not None:
                log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
                raise ValueError("custom_key belongs on custom_boolean")
        else:
            if self.custom_key is None:
                log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
                raise ValueError("custom_boolean requires custom_key")
            if self.character_id is not None:
                log_adaptive_schema_failure(self.__class__.__name__, "character_id", "adaptive_score_invalid")
                raise ValueError("character_id belongs on character")
        return self


class AdaptiveContextCategoricalBindingV1(_Strict):
    id: str
    kind: Literal["categorical"]
    external_key: str
    slot: ContextCategoricalSlot
    custom_key: str | None = None
    allowed: list[str] | None = Field(default=None, max_length=16)

    @field_validator("id")
    @classmethod
    def binding_id(cls, value: str) -> str:
        return _rule_id(value, model=cls.__name__, field="id")

    @field_validator("external_key")
    @classmethod
    def key_token(cls, value: str) -> str:
        return _external_key(value, model=cls.__name__, field="external_key")

    @field_validator("custom_key")
    @classmethod
    def custom_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="custom_key")

    @field_validator("allowed")
    @classmethod
    def allowed_tokens(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned = []
        for item in value:
            cleaned.append(_flag_token(item, model=cls.__name__, field="allowed"))
        return cleaned

    @model_validator(mode="after")
    def categorical_shape(self) -> AdaptiveContextCategoricalBindingV1:
        if self.slot == "custom_categorical" and self.custom_key is None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_categorical requires custom_key")
        if self.slot != "custom_categorical" and self.custom_key is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_key belongs on custom_categorical")
        return self


class AdaptiveContextTagsBindingV1(_Strict):
    id: str
    kind: Literal["tags"]
    external_key: str

    @field_validator("id")
    @classmethod
    def binding_id(cls, value: str) -> str:
        return _rule_id(value, model=cls.__name__, field="id")

    @field_validator("external_key")
    @classmethod
    def key_token(cls, value: str) -> str:
        return _external_key(value, model=cls.__name__, field="external_key")


AdaptiveContextBindingV1 = Annotated[
    AdaptiveContextNumericBindingV1
    | AdaptiveContextBooleanBindingV1
    | AdaptiveContextCategoricalBindingV1
    | AdaptiveContextTagsBindingV1,
    Field(discriminator="kind"),
]


class _RuleBase(_Strict):
    id: str
    min_dwell_samples: int | None = None
    target_state_id: str
    priority: int = Field(ge=0, le=100)

    @field_validator("id")
    @classmethod
    def rule_token(cls, value: str) -> str:
        return _rule_id(value, model=cls.__name__, field="id")

    @field_validator("target_state_id")
    @classmethod
    def target_token(cls, value: str) -> str:
        return _score_state_token(value, model=cls.__name__, field="target_state_id")

    @field_validator("priority", "min_dwell_samples", mode="before")
    @classmethod
    def dwell_int(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="min_dwell_samples")
        return value

    @model_validator(mode="after")
    def dwell_bounds(self) -> _RuleBase:
        settings = load_adaptive_musical_context_settings()
        dwell = settings.default_dwell if self.min_dwell_samples is None else self.min_dwell_samples
        if dwell < 1 or dwell > settings.max_dwell:
            log_adaptive_schema_failure(self.__class__.__name__, "min_dwell_samples", "adaptive_score_invalid")
            raise ValueError("min_dwell_samples is outside the configured range")
        object.__setattr__(self, "min_dwell_samples", dwell)
        return self


class AdaptiveContextNumericBandRuleV1(_RuleBase):
    kind: Literal["numeric_band"]
    slot: ContextNumericSlot
    custom_key: str | None = None
    polarity: ContextBandPolarity
    enter: float
    exit: float

    @field_validator("custom_key")
    @classmethod
    def custom_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="custom_key")

    @field_validator("enter", "exit", mode="before")
    @classmethod
    def ends_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="enter")
        return value

    @model_validator(mode="after")
    def band(self) -> AdaptiveContextNumericBandRuleV1:
        if self.slot == "custom_numeric" and self.custom_key is None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_numeric requires custom_key")
        if self.slot != "custom_numeric" and self.custom_key is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_key belongs on custom_numeric")
        if not math.isfinite(self.enter) or not math.isfinite(self.exit):
            log_adaptive_schema_failure(self.__class__.__name__, "enter", "adaptive_score_invalid")
            raise ValueError("enter and exit must be finite")
        if self.slot in _CLAMPED_NUMERIC_SLOTS and not (
            0 <= self.enter <= 1 and 0 <= self.exit <= 1
        ):
            log_adaptive_schema_failure(self.__class__.__name__, "enter", "adaptive_score_invalid")
            raise ValueError("enter and exit must lie in 0..1")
        gap = load_adaptive_musical_context_settings().min_hysteresis_gap
        if self.polarity == "high":
            legal = self.enter >= self.exit + gap
        else:
            legal = self.exit >= self.enter + gap
        if not legal:
            log_adaptive_schema_failure(self.__class__.__name__, "enter", "hysteresis_gap")
            raise ValueError("hysteresis_gap")
        return self


class AdaptiveContextCategoryEqualsRuleV1(_RuleBase):
    kind: Literal["category_equals"]
    slot: Literal["state", "location", "custom_categorical"]
    custom_key: str | None = None
    equals: str

    @field_validator("custom_key")
    @classmethod
    def custom_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="custom_key")

    @field_validator("equals")
    @classmethod
    def equals_token(cls, value: str) -> str:
        return _flag_token(value, model=cls.__name__, field="equals")

    @model_validator(mode="after")
    def category_shape(self) -> AdaptiveContextCategoryEqualsRuleV1:
        if self.slot == "custom_categorical" and self.custom_key is None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_categorical requires custom_key")
        if self.slot != "custom_categorical" and self.custom_key is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_key belongs on custom_categorical")
        return self


class AdaptiveContextTagPresentRuleV1(_RuleBase):
    kind: Literal["tag_present"]
    tag: str

    @field_validator("tag")
    @classmethod
    def tag_token(cls, value: str) -> str:
        return _flag_token(value, model=cls.__name__, field="tag")


class AdaptiveContextCharacterPresentRuleV1(_RuleBase):
    kind: Literal["character_present"]
    character_id: str

    @field_validator("character_id")
    @classmethod
    def character_token(cls, value: str) -> str:
        return _flag_token(value, model=cls.__name__, field="character_id")


AdaptiveContextStateRuleV1 = Annotated[
    AdaptiveContextNumericBandRuleV1
    | AdaptiveContextCategoryEqualsRuleV1
    | AdaptiveContextTagPresentRuleV1
    | AdaptiveContextCharacterPresentRuleV1,
    Field(discriminator="kind"),
]


class AdaptiveContextIntensitySourceV1(_Strict):
    slot: ContextNumericSlot
    custom_key: str | None = None
    emit_epsilon: float = 0.02

    @field_validator("custom_key")
    @classmethod
    def custom_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="custom_key")

    @field_validator("emit_epsilon", mode="before")
    @classmethod
    def epsilon_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="emit_epsilon")
        return value

    @model_validator(mode="after")
    def intensity_shape(self) -> AdaptiveContextIntensitySourceV1:
        if not math.isfinite(self.emit_epsilon) or not 0 <= self.emit_epsilon <= 1:
            log_adaptive_schema_failure(self.__class__.__name__, "emit_epsilon", "adaptive_score_invalid")
            raise ValueError("emit_epsilon must sit in 0..1")
        if self.slot == "custom_numeric" and self.custom_key is None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_numeric requires custom_key")
        if self.slot != "custom_numeric" and self.custom_key is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_key", "adaptive_score_invalid")
            raise ValueError("custom_key belongs on custom_numeric")
        return self


class AdaptiveContextFlagRuleV1(_Strict):
    id: str
    source: ContextFlagSource
    source_key: str
    flag: str

    @field_validator("id")
    @classmethod
    def rule_token(cls, value: str) -> str:
        return _rule_id(value, model=cls.__name__, field="id")

    @field_validator("source_key", "flag")
    @classmethod
    def flag_fields(cls, value: str) -> str:
        return _flag_token(value, model=cls.__name__, field="flag")


class AdaptiveContextMappingV1(_Strict):
    schema_version: Literal["adaptive.context.mapping.v1"] = ADAPTIVE_CONTEXT_MAPPING_SCHEMA
    baseline_state_id: str
    bindings: list[AdaptiveContextBindingV1] = Field(default_factory=list)
    state_rules: list[AdaptiveContextStateRuleV1] = Field(default_factory=list)
    intensity: AdaptiveContextIntensitySourceV1 | None = None
    flag_rules: list[AdaptiveContextFlagRuleV1] = Field(default_factory=list)

    @field_validator("baseline_state_id")
    @classmethod
    def baseline_token(cls, value: str) -> str:
        return _score_state_token(value, model=cls.__name__, field="baseline_state_id")

    @model_validator(mode="after")
    def unique_slots(self) -> AdaptiveContextMappingV1:
        settings = load_adaptive_musical_context_settings()
        if len(self.bindings) > settings.max_bindings:
            log_adaptive_schema_failure(self.__class__.__name__, "bindings", "adaptive_score_too_large")
            raise ValueError(_cap("bindings"))
        if len(self.state_rules) > settings.max_rules or len(self.flag_rules) > settings.max_rules:
            log_adaptive_schema_failure(self.__class__.__name__, "state_rules", "adaptive_score_too_large")
            raise ValueError(_cap("state_rules"))
        ids = [binding.id for binding in self.bindings]
        ids.extend(rule.id for rule in self.state_rules)
        ids.extend(rule.id for rule in self.flag_rules)
        if len(ids) != len(set(ids)):
            log_adaptive_schema_failure(self.__class__.__name__, "bindings", "adaptive_score_invalid")
            raise ValueError("duplicate_id")
        slots = [_binding_slot(binding) for binding in self.bindings]
        if len(slots) != len(set(slots)):
            log_adaptive_schema_failure(self.__class__.__name__, "bindings", "adaptive_score_invalid")
            raise ValueError("duplicate_slot")
        return self


def _binding_slot(binding: AdaptiveContextBindingV1) -> str:
    if binding.kind == "numeric":
        if binding.slot == "custom_numeric":
            return f"custom_numeric:{binding.custom_key}"
        return binding.slot
    if binding.kind == "boolean":
        if binding.slot == "character":
            return f"character:{binding.character_id}"
        return f"custom_boolean:{binding.custom_key}"
    if binding.kind == "categorical":
        if binding.slot == "custom_categorical":
            return f"custom_categorical:{binding.custom_key}"
        return binding.slot
    return "narrative_tags"


class AdaptiveContextStartRequest(_Strict):
    expected_document_revision: int = Field(ge=1)
    mapping: AdaptiveContextMappingV1

    @field_validator("expected_document_revision", mode="before")
    @classmethod
    def revision_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="expected_document_revision")
        return value


class AdaptiveContextCharacterV1(_Strict):
    id: str
    present: bool

    @field_validator("id")
    @classmethod
    def character_token(cls, value: str) -> str:
        return _flag_token(value, model=cls.__name__, field="id")


class AdaptiveContextEmittedIntensityV1(_Strict):
    op: Literal["set_intensity"]
    intensity: float = Field(ge=0, le=1)

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value


class AdaptiveContextEmittedFlagsV1(_Strict):
    op: Literal["set_flags"]
    flags: dict[str, bool] = Field(default_factory=dict)

    @field_validator("flags")
    @classmethod
    def flag_map(cls, value: dict[str, bool]) -> dict[str, bool]:
        if len(value) > 64:
            log_adaptive_schema_failure(cls.__name__, "flags", "adaptive_score_too_large")
            raise ValueError(_cap("flags"))
        for key in value:
            _flag_token(key, model=cls.__name__, field="flags")
        return value


class AdaptiveContextEmittedStateV1(_Strict):
    op: Literal["request_state"]
    to_state_id: str

    @field_validator("to_state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _score_state_token(value, model=cls.__name__, field="to_state_id")


AdaptiveContextEmittedV1 = Annotated[
    AdaptiveContextEmittedIntensityV1
    | AdaptiveContextEmittedFlagsV1
    | AdaptiveContextEmittedStateV1,
    Field(discriminator="op"),
]


class AdaptiveMusicalContextTelemetryV1(_Strict):
    sample_count: int = Field(ge=0)
    state_change_count: int = Field(ge=0)
    intensity_emit_count: int = Field(ge=0)
    rejected_sample_count: int = Field(ge=0)

    @field_validator(
        "sample_count",
        "state_change_count",
        "intensity_emit_count",
        "rejected_sample_count",
        mode="before",
    )
    @classmethod
    def counters_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="sample_count")
        return value


class AdaptiveMusicalContextV1(_Strict):
    schema_version: Literal["adaptive.musical_context.v1"] = ADAPTIVE_MUSICAL_CONTEXT_SCHEMA
    context_id: str
    sample_index: int = Field(ge=0)
    state: str | None = None
    intensity: float | None = Field(default=None, ge=0, le=1)
    tension: float | None = Field(default=None, ge=0, le=1)
    location: str | None = None
    health: float | None = Field(default=None, ge=0, le=1)
    danger: float | None = Field(default=None, ge=0, le=1)
    narrative_tags: list[str] = Field(default_factory=list)
    characters: list[AdaptiveContextCharacterV1] = Field(default_factory=list)
    custom_numeric: dict[str, float] = Field(default_factory=dict)
    custom_boolean: dict[str, bool] = Field(default_factory=dict)
    custom_categorical: dict[str, str] = Field(default_factory=dict)
    musical_state_id: str
    emitted: list[AdaptiveContextEmittedV1] = Field(default_factory=list)
    dwell_rule_id: str | None = None
    dwell_count: int = Field(ge=0)
    warnings: list[AdaptiveTransitionScheduleWarningV1] = Field(default_factory=list, max_length=8)
    telemetry: AdaptiveMusicalContextTelemetryV1
    document_revision: int = Field(ge=1)

    @field_validator("context_id")
    @classmethod
    def context_token(cls, value: str) -> str:
        if not _CONTEXT_ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "context_id", "adaptive_score_invalid")
            raise ValueError("context_id must match actx_ and 8 hex characters")
        return value

    @field_validator("sample_index", "dwell_count", "document_revision", mode="before")
    @classmethod
    def snapshot_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="sample_index")
        return value

    @field_validator("intensity", "tension", "health", "danger", mode="before")
    @classmethod
    def unit_number(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("state", "location")
    @classmethod
    def slot_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _flag_token(value, model=cls.__name__, field="state")

    @field_validator("musical_state_id")
    @classmethod
    def musical_token(cls, value: str) -> str:
        return _score_state_token(value, model=cls.__name__, field="musical_state_id")

    @field_validator("dwell_rule_id")
    @classmethod
    def dwell_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _rule_id(value, model=cls.__name__, field="dwell_rule_id")

    @field_validator("narrative_tags")
    @classmethod
    def tag_list(cls, value: list[str]) -> list[str]:
        settings = load_adaptive_musical_context_settings()
        if len(value) > settings.max_tags:
            log_adaptive_schema_failure(cls.__name__, "narrative_tags", "adaptive_score_too_large")
            raise ValueError(_cap("narrative_tags"))
        cleaned = [_flag_token(item, model=cls.__name__, field="narrative_tags") for item in value]
        if len(cleaned) != len(set(cleaned)) or cleaned != sorted(cleaned):
            log_adaptive_schema_failure(cls.__name__, "narrative_tags", "adaptive_score_invalid")
            raise ValueError("narrative_tags must be unique and sorted")
        return cleaned

    @model_validator(mode="after")
    def snapshot_shape(self) -> AdaptiveMusicalContextV1:
        settings = load_adaptive_musical_context_settings()
        if len(self.characters) > settings.max_characters:
            log_adaptive_schema_failure(self.__class__.__name__, "characters", "adaptive_score_too_large")
            raise ValueError(_cap("characters"))
        character_ids = [row.id for row in self.characters]
        if len(character_ids) != len(set(character_ids)) or character_ids != sorted(character_ids):
            log_adaptive_schema_failure(self.__class__.__name__, "characters", "adaptive_score_invalid")
            raise ValueError("characters must be unique and sorted")
        if len(self.custom_numeric) > settings.max_custom:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_numeric", "adaptive_score_too_large")
            raise ValueError(_cap("custom_numeric"))
        if len(self.custom_boolean) > settings.max_custom:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_boolean", "adaptive_score_too_large")
            raise ValueError(_cap("custom_boolean"))
        if len(self.custom_categorical) > settings.max_custom:
            log_adaptive_schema_failure(self.__class__.__name__, "custom_categorical", "adaptive_score_too_large")
            raise ValueError(_cap("custom_categorical"))
        for key, number in self.custom_numeric.items():
            _flag_token(key, model=self.__class__.__name__, field="custom_numeric")
            if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(float(number)):
                log_adaptive_schema_failure(self.__class__.__name__, "custom_numeric", "adaptive_score_invalid")
                raise ValueError("custom_numeric values must be finite")
        for key, present in self.custom_boolean.items():
            _flag_token(key, model=self.__class__.__name__, field="custom_boolean")
            if not isinstance(present, bool):
                log_adaptive_schema_failure(self.__class__.__name__, "custom_boolean", "adaptive_score_invalid")
                raise ValueError("custom_boolean values must be booleans")
        for key, token in self.custom_categorical.items():
            _flag_token(key, model=self.__class__.__name__, field="custom_categorical")
            _flag_token(token, model=self.__class__.__name__, field="custom_categorical")
        ops = [item.op for item in self.emitted]
        order = ("set_intensity", "set_flags", "request_state")
        if len(ops) > 3 or len(ops) != len(set(ops)) or ops != sorted(ops, key=order.index):
            log_adaptive_schema_failure(self.__class__.__name__, "emitted", "adaptive_score_invalid")
            raise ValueError("emitted ops must stay in command order")
        return self


def _validation_field(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    for part in reversed(loc):
        if isinstance(part, str):
            return part
    return "body"


def _message_token(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return ""
    return str(errors[0].get("msg") or "")


def _raise_validation(label: str, exc: ValidationError) -> None:
    token = _message_token(exc)
    field = "bindings" if "duplicate_slot" in token else _validation_field(exc)
    if "hysteresis_gap" in token:
        code = "hysteresis_gap"
        message = HYSTERESIS_GAP_MESSAGE
    elif "embedded_note_material" in token:
        code = "embedded_note_material"
        message = ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"]
    elif "cap_exceeded" in token:
        code = "adaptive_score_too_large"
        message = ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_too_large"]
    else:
        code = "adaptive_score_invalid"
        message = ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_invalid"]
    log_adaptive_schema_failure(label, field, code)
    raise AdaptiveScoreError(
        code,
        message,
        http_status=422,
        details={"field": field},
    ) from exc


def _require_object(data: object, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        log_adaptive_schema_failure(label, "body", "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            f"{label} must be an object",
            http_status=422,
            details={"field": "body"},
        )
    return data


def _require_schema(data: dict[str, Any], expected: str, label: str) -> None:
    version = data.get("schema_version")
    if version != expected:
        log_adaptive_schema_failure(label, "schema_version", "unsupported_schema_version")
        raise AdaptiveScoreError(
            "unsupported_schema_version",
            CONTEXT_SCHEMA_MESSAGES[expected],
            http_status=422,
            details={"field": "schema_version"},
        )


def parse_adaptive_context_external(data: object) -> AdaptiveContextExternalV1:
    """Validate one flat sample. A wrong version leaves any session untouched."""
    body = _require_object(data, "AdaptiveContextExternalV1")
    _require_schema(body, ADAPTIVE_CONTEXT_EXTERNAL_SCHEMA, "AdaptiveContextExternalV1")
    values = body.get("values")
    if isinstance(values, dict):
        for key in values:
            if str(key) in FORBIDDEN_NOTE_KEYS:
                log_adaptive_schema_failure(
                    "AdaptiveContextExternalV1",
                    "values",
                    "embedded_note_material",
                )
                raise AdaptiveScoreError(
                    "embedded_note_material",
                    ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"],
                    http_status=422,
                    details={"field": "values"},
                )
    try:
        return AdaptiveContextExternalV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveContextExternalV1", exc)
    raise AssertionError("adaptive context external")


def parse_adaptive_context_mapping(data: object) -> AdaptiveContextMappingV1:
    body = _require_object(data, "AdaptiveContextMappingV1")
    _require_schema(body, ADAPTIVE_CONTEXT_MAPPING_SCHEMA, "AdaptiveContextMappingV1")
    try:
        return AdaptiveContextMappingV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveContextMappingV1", exc)
    raise AssertionError("adaptive context mapping")


def parse_adaptive_musical_context(data: object) -> AdaptiveMusicalContextV1:
    body = _require_object(data, "AdaptiveMusicalContextV1")
    _require_schema(body, ADAPTIVE_MUSICAL_CONTEXT_SCHEMA, "AdaptiveMusicalContextV1")
    try:
        return AdaptiveMusicalContextV1.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveMusicalContextV1", exc)
    raise AssertionError("adaptive musical context")


def parse_adaptive_context_start(data: object) -> AdaptiveContextStartRequest:
    body = _require_object(data, "AdaptiveContextStartRequest")
    mapping = body.get("mapping")
    if isinstance(mapping, dict):
        parse_adaptive_context_mapping(mapping)
    try:
        return AdaptiveContextStartRequest.model_validate(body)
    except ValidationError as exc:
        _raise_validation("AdaptiveContextStartRequest", exc)
    raise AssertionError("adaptive context start")
