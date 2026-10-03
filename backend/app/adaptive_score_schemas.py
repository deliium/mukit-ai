"""Strict ``adaptive.score.v1`` contracts.

An adaptive score references canonical ``composition.v2`` material. It is not a
playable score and it is not ``composition.v5``. Note events never belong here.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic import TypeAdapter

from app.adaptive_score_settings import load_adaptive_score_settings

logger = logging.getLogger(__name__)

ADAPTIVE_SCORE_SCHEMA: Literal["adaptive.score.v1"] = "adaptive.score.v1"

FORBIDDEN_NOTE_KEYS: frozenset[str] = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
        "composition",
        "composition_json",
    }
)

AdaptiveQuantization = Literal["immediate", "beat", "bar", "next_exit", "custom"]
AdaptiveTransitionQuantization = Literal[
    "immediate",
    "beat",
    "bar",
    "next_exit",
    "custom",
    "phrase",
    "loop_end",
    "cue",
]
AdaptiveRealizationKind = Literal["cut", "crossfade", "phrase", "stinger", "overlap"]
ADAPTIVE_TRANSITION_SCHEDULE_SCHEMA: Literal["adaptive.transition.schedule.v1"] = (
    "adaptive.transition.schedule.v1"
)
AdaptiveMixHint = Literal["bed", "foreground", "ornament"]
AdaptiveLayerRole = Literal[
    "harmony",
    "bass",
    "percussion",
    "strings",
    "brass",
    "ambient",
    "other",
]
AdaptiveLayerFadePolicy = Literal["cut", "linear", "bar"]
AdaptiveLayerReason = Literal["in_window", "out_of_window", "out_of_state", "suppressed"]
ADAPTIVE_LAYER_INTENSITY_SCHEMA: Literal["adaptive.layer.intensity.v1"] = (
    "adaptive.layer.intensity.v1"
)
AdaptiveAssetKind = Literal["neural_stem", "neural_mix", "recovery_source", "alignment"]
AdaptiveMaterialKind = Literal[
    "section",
    "bar_range",
    "track_range",
    "motif",
    "revision_region",
    "asset",
]
AdaptiveBindingStatus = Literal["fresh", "stale", "unpinned", "unchecked"]
AdaptiveFindingSeverity = Literal["warning", "error"]

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_FLAG_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
_SCORE_ID_RE = re.compile(r"^ascore_[0-9a-f]{16}$")

ADAPTIVE_SCORE_ERROR_CODES: dict[str, str] = {
    "unsupported_schema_version": "schema_version must be adaptive.score.v1.",
    "identity_mismatch": "Score id or project id does not match the route.",
    "embedded_note_material": "Adaptive scores cannot embed note events.",
    "adaptive_score_too_large": "Adaptive score exceeded a configured cap.",
    "adaptive_score_invalid": "Adaptive score failed schema validation.",
    "adaptive_score_not_found": "Adaptive score id was not found.",
    "adaptive_score_conflict": "expected_document_revision does not match the stored score.",
    "adaptive_score_name_conflict": "An adaptive score with this name already exists in the project.",
    "project_not_found": "Project id was not found.",
    "revision_not_found": "Revision id was not found on this project.",
    "persistence_secret_rejected": "Adaptive score payload contains a secret field or value.",
    "duplicate_id": "Entity ids must be unique within one adaptive score.",
    "dangling_state_ref": "A state reference does not name a state in this score.",
    "transition_eligibility_mismatch": "Transition eligibility does not match the source state.",
    "duration_bounds": "Duration bounds are inconsistent.",
    "quantization_grid": "Custom quantization requires custom_grid_bars in 1..64.",
    "loop_bounds": "An enabled loop requires ordered absolute bars.",
    "state_unreachable": "State is not reachable from the initial state.",
    "mixed_revision_targets": "Non-asset material refs must share one revision id.",
    "composition_unavailable": "No composition is available to bind material references.",
    "material_target_missing": "A material reference does not match the composition.",
    "material_range_outside": "A bar range is outside the composition.",
    "fallback_transition_missing": "fallback_transition_id does not name a transition.",
    "initial_state_missing": "initial_state_id is unset while states exist.",
    "default_state_missing": "default_state_id is unset while states exist.",
    "impossible_transition": "An eligible transition can never be satisfied.",
    "missing_fallback_state": "A default_state policy has no default state.",
    "transition_deadlock": "A reachable cycle cannot make musical progress.",
    "timing_incompatible": "Entry, exit, or loop timing does not agree.",
    "adaptive_score_store_failed": "Adaptive score persistence failed.",
    "transition_unsatisfied": "No eligible transition satisfies the runtime conditions.",
    "phrase_unavailable": "Phrase quantization has no material span to align.",
    "loop_unavailable": "loop_end requires an enabled source loop.",
    "loop_end_passed": "position_tick is already past the authored loop end.",
    "cue_not_found": "No on-grid rehearsal cue matches cue_label at or after position_tick.",
    "exit_passed": "position_tick is already past the source exit.",
    "exit_off_grid": "A tick exit is not a beat boundary.",
    "exit_unavailable": "The source exit cannot name a tick.",
    "boundary_unavailable": "No grid boundary remains at or after position_tick.",
    "meter_grid_indivisible": "The active meter does not divide the bar into integer beat ticks.",
    "position_outside": "position_tick is past the composition duration.",
    "realization_invalid": "The transition realization cannot be scheduled.",
    "transition_request_not_pending": "That transition request is not the current pending request.",
    "playback_not_running": "Adaptive playback is not running for this score.",
    "adaptive_continuous_disabled": "Continuous generative music is disabled.",
    "adaptive_engine_continuous_disabled": "Adaptive engine continuous maintain is disabled.",
}


def log_adaptive_schema_failure(model: str, field: str, code: str) -> None:
    """DEBUG schema rejection without the document body."""
    logger.debug(
        "Adaptive score schema rejected field",
        extra={"model": model, "field": field, "code": code},
    )


def normalize_adaptive_score_name(name: str) -> str:
    """NFKC, collapse whitespace, then casefold. Local copy; no history import."""
    cleaned = " ".join(unicodedata.normalize("NFKC", name).strip().split())
    return unicodedata.normalize("NFKC", cleaned).casefold()


def is_server_score_id(value: str) -> bool:
    return bool(_SCORE_ID_RE.fullmatch(value))


class AdaptiveScoreError(Exception):
    """Domain error mapped to a structured HTTP detail."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message[:200]
        self.http_status = http_status
        self.details = details or {}


def map_adaptive_score_error_to_http(exc: AdaptiveScoreError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.details:
        safe: dict[str, Any] = {}
        for key, value in exc.details.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                safe[key] = value[:16]
            elif key == "findings" and _finding_detail_list(value):
                safe[key] = value[:64]
        if safe:
            detail["details"] = safe
    return int(exc.http_status), detail


_FINDING_DETAIL_KEYS = frozenset({"code", "severity", "target_id", "message"})


def _finding_detail_list(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if set(item) != _FINDING_DETAIL_KEYS:
            return False
        if not all(isinstance(item[key], str) or item[key] is None for key in _FINDING_DETAIL_KEYS):
            return False
        if item["severity"] not in {"warning", "error"}:
            return False
    return True


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _reject_bool(value: object, *, model: str, field: str) -> None:
    if isinstance(value, bool):
        log_adaptive_schema_failure(model, field, "adaptive_score_invalid")
        raise ValueError(f"{field} must not be a boolean")


class AdaptiveScoreFallbackV1(_Strict):
    on_missing_material: Literal["hold", "silence", "default_state"] = "hold"
    on_invalid_transition: Literal["stay", "default_state"] = "stay"
    on_unresolved_condition: Literal["stay", "default_state"] = "stay"


class AdaptiveBoundaryV1(_Strict):
    kind: Literal["bar", "tick", "material_start", "material_end"]
    bar: int | None = Field(default=None, ge=1)
    tick: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def locator_matches_kind(self) -> AdaptiveBoundaryV1:
        if self.kind == "bar" and self.bar is None:
            log_adaptive_schema_failure(self.__class__.__name__, "bar", "adaptive_score_invalid")
            raise ValueError("bar is required when kind is bar")
        if self.kind == "tick" and self.tick is None:
            log_adaptive_schema_failure(self.__class__.__name__, "tick", "adaptive_score_invalid")
            raise ValueError("tick is required when kind is tick")
        return self


class AdaptiveLoopV1(_Strict):
    enabled: bool = False
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)


class AdaptiveMaterialRefV1(_Strict):
    kind: AdaptiveMaterialKind
    section_id: str | None = Field(default=None, min_length=1, max_length=120)
    start_bar: int | None = Field(default=None, ge=1)
    end_bar: int | None = Field(default=None, ge=1)
    track_ids: list[str] = Field(default_factory=list)
    motif_id: str | None = Field(default=None, min_length=1, max_length=120)
    revision_id: str | None = Field(default=None, min_length=1, max_length=80)
    snapshot_fingerprint: str | None = Field(default=None, min_length=8, max_length=200)
    start_tick: int | None = Field(default=None, ge=0)
    end_tick: int | None = Field(default=None, ge=0)
    asset_kind: AdaptiveAssetKind | None = None
    asset_id: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("track_ids")
    @classmethod
    def track_id_count(cls, value: list[str]) -> list[str]:
        if len(value) > 16:
            log_adaptive_schema_failure(cls.__name__, "track_ids", "adaptive_score_too_large")
            raise ValueError("adaptive_score_too_large")
        for item in value:
            if not isinstance(item, str) or not item.strip():
                log_adaptive_schema_failure(cls.__name__, "track_ids", "adaptive_score_invalid")
                raise ValueError("track ids must be non-empty strings")
        return value

    @model_validator(mode="after")
    def required_fields_for_kind(self) -> AdaptiveMaterialRefV1:
        if self.start_bar is not None and self.end_bar is not None and self.end_bar < self.start_bar:
            log_adaptive_schema_failure(self.__class__.__name__, "end_bar", "duration_bounds")
            raise ValueError("end_bar must be >= start_bar")
        if self.start_tick is not None and self.end_tick is not None and self.end_tick < self.start_tick:
            log_adaptive_schema_failure(self.__class__.__name__, "end_tick", "duration_bounds")
            raise ValueError("end_tick must be >= start_tick")
        if self.kind == "section" and not self.section_id:
            log_adaptive_schema_failure(self.__class__.__name__, "section_id", "adaptive_score_invalid")
            raise ValueError("section_id is required for kind section")
        if self.kind == "bar_range" and (self.start_bar is None or self.end_bar is None):
            log_adaptive_schema_failure(self.__class__.__name__, "start_bar", "adaptive_score_invalid")
            raise ValueError("bar_range requires start_bar and end_bar")
        if self.kind == "track_range" and not self.track_ids:
            log_adaptive_schema_failure(self.__class__.__name__, "track_ids", "adaptive_score_invalid")
            raise ValueError("track_range requires track_ids")
        if self.kind == "motif" and not self.motif_id:
            log_adaptive_schema_failure(self.__class__.__name__, "motif_id", "adaptive_score_invalid")
            raise ValueError("motif_id is required for kind motif")
        if self.kind == "revision_region":
            has_region = bool(self.section_id) or (
                self.start_bar is not None and self.end_bar is not None
            )
            if not self.revision_id or not has_region:
                log_adaptive_schema_failure(
                    self.__class__.__name__, "revision_id", "adaptive_score_invalid"
                )
                raise ValueError("revision_region requires revision_id and a section or bar range")
        if self.kind == "asset" and (self.asset_kind is None or not self.asset_id):
            log_adaptive_schema_failure(self.__class__.__name__, "asset_id", "adaptive_score_invalid")
            raise ValueError("asset refs require asset_kind and asset_id")
        return self


class AdaptiveConditionManualV1(_Strict):
    kind: Literal["manual"]


class AdaptiveConditionIntensityV1(_Strict):
    kind: Literal["intensity_at_least", "intensity_at_most"]
    value: float = Field(ge=0, le=1)

    @field_validator("value")
    @classmethod
    def value_is_number(cls, value: float) -> float:
        _reject_bool(value, model=cls.__name__, field="value")
        return value


class AdaptiveConditionFlagV1(_Strict):
    kind: Literal["flag_equals"]
    flag: str
    value: bool

    @field_validator("flag")
    @classmethod
    def flag_token(cls, value: str) -> str:
        if not _FLAG_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "flag", "adaptive_score_invalid")
            raise ValueError("flag must match ^[a-z][a-z0-9_]{0,40}$")
        return value


class AdaptiveConditionMinTimeV1(_Strict):
    kind: Literal["min_time_in_state_bars"]
    value: int = Field(ge=0)

    @field_validator("value")
    @classmethod
    def value_is_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="value")
        return value


AdaptiveConditionV1 = Annotated[
    AdaptiveConditionManualV1
    | AdaptiveConditionIntensityV1
    | AdaptiveConditionFlagV1
    | AdaptiveConditionMinTimeV1,
    Field(discriminator="kind"),
]


class AdaptiveTransitionRealizationV1(_Strict):
    """How a bed switch is realized. Omitted on a transition means kind ``cut``."""

    kind: AdaptiveRealizationKind = "cut"
    crossfade_ms: int | None = Field(default=None, ge=0, le=4000)
    phrase_material: AdaptiveMaterialRefV1 | None = None
    stinger_id: str | None = None
    overlap_bars: int | None = Field(default=None, ge=1, le=16)

    @field_validator("crossfade_ms", "overlap_bars", mode="before")
    @classmethod
    def companion_int(cls, value: int | None) -> int | None:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="companion")
        return value

    @field_validator("stinger_id")
    @classmethod
    def stinger_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "stinger_id", "adaptive_score_invalid")
            raise ValueError("stinger_id must be a short token")
        return value

    @model_validator(mode="after")
    def companions_match_kind(self) -> AdaptiveTransitionRealizationV1:
        present = {
            name
            for name, value in (
                ("crossfade_ms", self.crossfade_ms),
                ("phrase_material", self.phrase_material),
                ("stinger_id", self.stinger_id),
                ("overlap_bars", self.overlap_bars),
            )
            if value is not None
        }
        required = {
            "cut": set(),
            "crossfade": {"crossfade_ms"},
            "phrase": {"phrase_material"},
            "stinger": {"stinger_id"},
            "overlap": {"overlap_bars"},
        }[self.kind]
        if present != required:
            log_adaptive_schema_failure(self.__class__.__name__, "kind", "adaptive_score_invalid")
            raise ValueError("realization companions must match kind")
        return self


class AdaptiveScoreTransitionV1(_Strict):
    id: str
    from_state_id: str
    to_state_id: str
    quantization: AdaptiveTransitionQuantization
    custom_grid_bars: int | None = Field(default=None, ge=1, le=64)
    priority: int = 0
    conditions: list[AdaptiveConditionV1] = Field(default_factory=list)
    fallback_behavior: Literal["stay", "default_state", "alternate_transition"] = "stay"
    fallback_transition_id: str | None = None
    cue_label: str | None = Field(default=None, max_length=80)
    realization: AdaptiveTransitionRealizationV1 = Field(
        default_factory=AdaptiveTransitionRealizationV1
    )

    @field_validator("id", "from_state_id", "to_state_id")
    @classmethod
    def entity_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "id", "adaptive_score_invalid")
            raise ValueError("id must be a short token")
        return value

    @field_validator("priority")
    @classmethod
    def priority_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="priority")
        return value

    @field_validator("cue_label")
    @classmethod
    def cue_label_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned or len(cleaned) > 80:
            log_adaptive_schema_failure(cls.__name__, "cue_label", "adaptive_score_invalid")
            raise ValueError("cue_label must be 1..80 characters after strip")
        return cleaned

    @model_validator(mode="after")
    def cue_label_matches_quantization(self) -> AdaptiveScoreTransitionV1:
        if self.quantization == "cue" and not self.cue_label:
            log_adaptive_schema_failure(self.__class__.__name__, "cue_label", "adaptive_score_invalid")
            raise ValueError("cue_label is required when quantization is cue")
        if self.quantization != "cue" and self.cue_label is not None:
            log_adaptive_schema_failure(self.__class__.__name__, "cue_label", "adaptive_score_invalid")
            raise ValueError("cue_label is only valid when quantization is cue")
        return self

    @model_validator(mode="after")
    def condition_cap_and_fallback(self) -> AdaptiveScoreTransitionV1:
        settings = load_adaptive_score_settings()
        if len(self.conditions) > settings.max_conditions_per_transition:
            log_adaptive_schema_failure(
                self.__class__.__name__, "conditions", "adaptive_score_too_large"
            )
            raise ValueError("adaptive_score_too_large")
        if self.fallback_behavior == "alternate_transition" and not self.fallback_transition_id:
            log_adaptive_schema_failure(
                self.__class__.__name__, "fallback_transition_id", "adaptive_score_invalid"
            )
            raise ValueError("alternate_transition requires fallback_transition_id")
        if self.fallback_behavior != "alternate_transition" and self.fallback_transition_id:
            log_adaptive_schema_failure(
                self.__class__.__name__, "fallback_transition_id", "adaptive_score_invalid"
            )
            raise ValueError("fallback_transition_id is only valid for alternate_transition")
        return self


class AdaptiveScoreStateV1(_Strict):
    id: str
    name: str
    intensity: float = Field(ge=0, le=1)
    material: AdaptiveMaterialRefV1
    loop: AdaptiveLoopV1 = Field(default_factory=AdaptiveLoopV1)
    entry: AdaptiveBoundaryV1 = Field(
        default_factory=lambda: AdaptiveBoundaryV1(kind="material_start")
    )
    exit: AdaptiveBoundaryV1 = Field(
        default_factory=lambda: AdaptiveBoundaryV1(kind="material_end")
    )
    min_duration_bars: int = Field(default=0, ge=0)
    max_duration_bars: int | None = Field(default=None, ge=0)
    transition_ids: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def entity_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "id", "adaptive_score_invalid")
            raise ValueError("id must be a short token")
        return value

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        settings = load_adaptive_score_settings()
        cleaned = value.strip()
        if not cleaned or len(cleaned) > settings.max_name_length:
            log_adaptive_schema_failure(cls.__name__, "name", "adaptive_score_invalid")
            raise ValueError("name length is invalid")
        return cleaned

    @field_validator("intensity")
    @classmethod
    def intensity_number(cls, value: float) -> float:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("min_duration_bars", "max_duration_bars")
    @classmethod
    def duration_int(cls, value: int | None) -> int | None:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="duration")
        return value


class AdaptiveScoreVariantV1(_Strict):
    id: str
    state_id: str
    name: str
    material: AdaptiveMaterialRefV1
    intensity_min: float = Field(ge=0, le=1)
    intensity_max: float = Field(ge=0, le=1)

    @field_validator("id", "state_id")
    @classmethod
    def entity_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "id", "adaptive_score_invalid")
            raise ValueError("id must be a short token")
        return value

    @field_validator("intensity_min", "intensity_max")
    @classmethod
    def intensity_number(cls, value: float) -> float:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @model_validator(mode="after")
    def intensity_window(self) -> AdaptiveScoreVariantV1:
        if self.intensity_max < self.intensity_min:
            log_adaptive_schema_failure(
                self.__class__.__name__, "intensity_max", "duration_bounds"
            )
            raise ValueError("intensity_max must be >= intensity_min")
        return self


class AdaptiveLayerFadeV1(_Strict):
    in_policy: AdaptiveLayerFadePolicy = "cut"
    out_policy: AdaptiveLayerFadePolicy = "cut"
    fade_in_ms: int = Field(default=0, ge=0, le=4000)
    fade_out_ms: int = Field(default=0, ge=0, le=4000)

    @field_validator("fade_in_ms", "fade_out_ms", mode="before")
    @classmethod
    def fade_ms_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="fade_ms")
        return value


class AdaptiveScoreLayerV1(_Strict):
    id: str
    name: str
    state_id: str | None = None
    material: AdaptiveMaterialRefV1
    intensity_min: float = Field(ge=0, le=1)
    intensity_max: float = Field(ge=0, le=1)
    mix_hint: AdaptiveMixHint
    default_active: bool = False
    role: AdaptiveLayerRole = "other"
    exclusive_group: str | None = None
    priority: int = 0
    fade: AdaptiveLayerFadeV1 = Field(default_factory=AdaptiveLayerFadeV1)

    @field_validator("id")
    @classmethod
    def entity_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "id", "adaptive_score_invalid")
            raise ValueError("id must be a short token")
        return value

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "state_id", "adaptive_score_invalid")
            raise ValueError("state_id must be a short token")
        return value

    @field_validator("exclusive_group")
    @classmethod
    def group_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _FLAG_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "exclusive_group", "adaptive_score_invalid")
            raise ValueError("exclusive_group must match ^[a-z][a-z0-9_]{0,40}$")
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def priority_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="priority")
        return value

    @model_validator(mode="after")
    def intensity_window(self) -> AdaptiveScoreLayerV1:
        if self.intensity_max < self.intensity_min:
            log_adaptive_schema_failure(self.__class__.__name__, "intensity_max", "duration_bounds")
            raise ValueError("intensity_max must be >= intensity_min")
        return self


class AdaptiveScoreStingerV1(_Strict):
    id: str
    name: str
    material: AdaptiveMaterialRefV1
    associated_state_id: str | None = None
    interrupt_policy: Literal["overlay", "duck_bed", "wait_for_exit"]
    quantization: AdaptiveQuantization
    custom_grid_bars: int | None = Field(default=None, ge=1, le=64)
    retrigger: Literal["once", "always"]

    @field_validator("id")
    @classmethod
    def entity_id(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "id", "adaptive_score_invalid")
            raise ValueError("id must be a short token")
        return value

    @field_validator("associated_state_id")
    @classmethod
    def state_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "associated_state_id", "adaptive_score_invalid")
            raise ValueError("associated_state_id must be a short token")
        return value


class AdaptiveScoreV1(_Strict):
    schema_version: Literal["adaptive.score.v1"] = ADAPTIVE_SCORE_SCHEMA
    id: str | None = None
    project_id: str | None = None
    name: str
    initial_state_id: str | None = None
    default_state_id: str | None = None
    fallback: AdaptiveScoreFallbackV1 = Field(default_factory=AdaptiveScoreFallbackV1)
    states: list[AdaptiveScoreStateV1] = Field(default_factory=list)
    variants: list[AdaptiveScoreVariantV1] = Field(default_factory=list)
    transitions: list[AdaptiveScoreTransitionV1] = Field(default_factory=list)
    layers: list[AdaptiveScoreLayerV1] = Field(default_factory=list)
    stingers: list[AdaptiveScoreStingerV1] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_foreign_schema(cls, data: Any) -> Any:
        if isinstance(data, dict) and "schema_version" in data:
            version = data.get("schema_version")
            if version != ADAPTIVE_SCORE_SCHEMA:
                log_adaptive_schema_failure(cls.__name__, "schema_version", "unsupported_schema_version")
                raise ValueError("unsupported_schema_version")
        return data

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        settings = load_adaptive_score_settings()
        cleaned = " ".join(value.strip().split())
        if not cleaned or len(cleaned) > settings.max_name_length:
            log_adaptive_schema_failure(cls.__name__, "name", "adaptive_score_invalid")
            raise ValueError("name length is invalid")
        return cleaned

    @field_validator("id")
    @classmethod
    def optional_score_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not is_server_score_id(value):
            log_adaptive_schema_failure(cls.__name__, "id", "identity_mismatch")
            raise ValueError("identity_mismatch")
        return value

    @model_validator(mode="after")
    def caps(self) -> AdaptiveScoreV1:
        settings = load_adaptive_score_settings()
        if len(self.states) > settings.max_states:
            log_adaptive_schema_failure(self.__class__.__name__, "states", "adaptive_score_too_large")
            raise ValueError("adaptive_score_too_large")
        if len(self.transitions) > settings.max_transitions:
            log_adaptive_schema_failure(
                self.__class__.__name__, "transitions", "adaptive_score_too_large"
            )
            raise ValueError("adaptive_score_too_large")
        if len(self.layers) > settings.max_layers:
            log_adaptive_schema_failure(self.__class__.__name__, "layers", "adaptive_score_too_large")
            raise ValueError("adaptive_score_too_large")
        if len(self.stingers) > settings.max_stingers:
            log_adaptive_schema_failure(
                self.__class__.__name__, "stingers", "adaptive_score_too_large"
            )
            raise ValueError("adaptive_score_too_large")
        per_state: dict[str, int] = {}
        for variant in self.variants:
            per_state[variant.state_id] = per_state.get(variant.state_id, 0) + 1
            if per_state[variant.state_id] > settings.max_variants_per_state:
                log_adaptive_schema_failure(
                    self.__class__.__name__, "variants", "adaptive_score_too_large"
                )
                raise ValueError("adaptive_score_too_large")
        return self


class AdaptiveScoreFindingV1(_Strict):
    code: str = Field(min_length=1, max_length=80)
    severity: AdaptiveFindingSeverity
    target_id: str | None = Field(default=None, max_length=80)
    message: str = Field(default="", max_length=200)


class AdaptiveScoreSummaryV1(_Strict):
    id: str
    name: str
    schema_version: Literal["adaptive.score.v1"]
    is_default: bool
    document_revision: int = Field(ge=1)
    state_count: int = Field(ge=0)
    updated_at: str


class AdaptiveScoreListResponse(_Strict):
    scores: list[AdaptiveScoreSummaryV1]


class AdaptiveScoreGetResponse(_Strict):
    score: AdaptiveScoreV1
    document_revision: int = Field(ge=1)
    is_default: bool
    binding_status: AdaptiveBindingStatus
    created_at: str
    updated_at: str


class AdaptiveScoreValidateResponse(_Strict):
    ready: bool
    binding_status: AdaptiveBindingStatus
    findings: list[AdaptiveScoreFindingV1]
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)


class AdaptiveScoreCreateRequest(_Strict):
    is_default: bool = False
    score: dict[str, Any]


class AdaptiveScoreUpdateRequest(_Strict):
    expected_document_revision: int = Field(ge=1)
    is_default: bool | None = None
    score: dict[str, Any]


def _entity_token(value: str, *, model: str, field: str) -> str:
    if not _ID_RE.fullmatch(value):
        log_adaptive_schema_failure(model, field, "adaptive_score_invalid")
        raise ValueError("id must be a short token")
    return value


def _optional_entity_token(value: str | None, *, model: str, field: str) -> str | None:
    if value is None:
        return None
    return _entity_token(value, model=model, field=field)


def _payload_name(value: str, *, model: str) -> str:
    settings = load_adaptive_score_settings()
    cleaned = value.strip()
    if not cleaned or len(cleaned) > settings.max_name_length:
        log_adaptive_schema_failure(model, "name", "adaptive_score_invalid")
        raise ValueError("name length is invalid")
    return cleaned


class CreateStatePayload(_Strict):
    name: str
    id: str | None = None
    material: AdaptiveMaterialRefV1 | None = None

    @field_validator("name")
    @classmethod
    def name_text(cls, value: str) -> str:
        return _payload_name(value, model=cls.__name__)

    @field_validator("id")
    @classmethod
    def optional_id(cls, value: str | None) -> str | None:
        return _optional_entity_token(value, model=cls.__name__, field="id")


class DeleteStatePayload(_Strict):
    state_id: str

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")


class DuplicateStatePayload(_Strict):
    state_id: str
    name: str | None = None

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")

    @field_validator("name")
    @classmethod
    def optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _payload_name(value, model=cls.__name__)


class AssignMaterialPayload(_Strict):
    state_id: str
    material: AdaptiveMaterialRefV1

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")


class CreateTransitionPayload(_Strict):
    from_state_id: str
    to_state_id: str
    quantization: AdaptiveTransitionQuantization
    id: str | None = None
    priority: int = 0
    conditions: list[AdaptiveConditionV1] = Field(default_factory=list)
    fallback_behavior: Literal["stay", "default_state", "alternate_transition"] = "stay"
    fallback_transition_id: str | None = None
    custom_grid_bars: int | None = Field(default=None, ge=1, le=64)
    cue_label: str | None = Field(default=None, max_length=80)
    realization: AdaptiveTransitionRealizationV1 | None = None

    @field_validator("from_state_id", "to_state_id")
    @classmethod
    def endpoint_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="id")

    @field_validator("id", "fallback_transition_id")
    @classmethod
    def optional_transition_token(cls, value: str | None) -> str | None:
        return _optional_entity_token(value, model=cls.__name__, field="id")

    @field_validator("priority")
    @classmethod
    def priority_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="priority")
        return value


class EditTransitionPayload(_Strict):
    transition_id: str
    from_state_id: str | None = None
    to_state_id: str | None = None
    quantization: AdaptiveTransitionQuantization | None = None
    priority: int | None = None
    conditions: list[AdaptiveConditionV1] | None = None
    fallback_behavior: Literal["stay", "default_state", "alternate_transition"] | None = None
    fallback_transition_id: str | None = None
    custom_grid_bars: int | None = Field(default=None, ge=1, le=64)
    cue_label: str | None = Field(default=None, max_length=80)
    realization: AdaptiveTransitionRealizationV1 | None = None

    @field_validator("transition_id")
    @classmethod
    def transition_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="transition_id")

    @field_validator("from_state_id", "to_state_id", "fallback_transition_id")
    @classmethod
    def optional_endpoint(cls, value: str | None) -> str | None:
        return _optional_entity_token(value, model=cls.__name__, field="id")

    @field_validator("priority")
    @classmethod
    def priority_int(cls, value: int | None) -> int | None:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="priority")
        return value


class AssignLoopPayload(_Strict):
    state_id: str
    enabled: bool
    start_bar: int = Field(ge=1)
    end_bar: int = Field(ge=1)

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")

    @field_validator("start_bar", "end_bar")
    @classmethod
    def bar_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="bar")
        return value


class AssignIntensityPayload(_Strict):
    state_id: str
    intensity: float = Field(ge=0, le=1)

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")

    @field_validator("intensity")
    @classmethod
    def intensity_number(cls, value: float) -> float:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value


class AssignBoundaryPayload(_Strict):
    state_id: str
    which: Literal["entry", "exit"]
    boundary: AdaptiveBoundaryV1

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")


class _CommandEnvelope(_Strict):
    expected_document_revision: int = Field(ge=1)


class CreateStateCommand(_CommandEnvelope):
    op: Literal["create_state"]
    payload: CreateStatePayload


class DeleteStateCommand(_CommandEnvelope):
    op: Literal["delete_state"]
    payload: DeleteStatePayload


class DuplicateStateCommand(_CommandEnvelope):
    op: Literal["duplicate_state"]
    payload: DuplicateStatePayload


class AssignMaterialCommand(_CommandEnvelope):
    op: Literal["assign_material"]
    payload: AssignMaterialPayload


class CreateTransitionCommand(_CommandEnvelope):
    op: Literal["create_transition"]
    payload: CreateTransitionPayload


class EditTransitionCommand(_CommandEnvelope):
    op: Literal["edit_transition"]
    payload: EditTransitionPayload


class AssignLoopCommand(_CommandEnvelope):
    op: Literal["assign_loop"]
    payload: AssignLoopPayload


class AssignIntensityCommand(_CommandEnvelope):
    op: Literal["assign_intensity"]
    payload: AssignIntensityPayload


class AssignBoundaryCommand(_CommandEnvelope):
    op: Literal["assign_boundary"]
    payload: AssignBoundaryPayload


class AdaptiveScoreCommandResponse(_Strict):
    score: AdaptiveScoreV1
    document_revision: int = Field(ge=1)
    is_default: bool
    binding_status: AdaptiveBindingStatus
    created_at: str
    updated_at: str
    findings: list[AdaptiveScoreFindingV1] = Field(default_factory=list)


class AdaptiveTransitionRuntimeV1(_Strict):
    intensity: float = Field(default=0, ge=0, le=1)
    flags: dict[str, bool] = Field(default_factory=dict)
    bars_in_state: int = Field(default=0, ge=0)

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: float) -> float:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("bars_in_state", mode="before")
    @classmethod
    def bars_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="bars_in_state")
        return value

    @field_validator("flags")
    @classmethod
    def flag_map(cls, value: dict[str, bool]) -> dict[str, bool]:
        if len(value) > 64:
            log_adaptive_schema_failure(cls.__name__, "flags", "adaptive_score_too_large")
            raise ValueError("adaptive_score_too_large")
        for key, flag_value in value.items():
            if not isinstance(key, str) or not _FLAG_RE.fullmatch(key):
                log_adaptive_schema_failure(cls.__name__, "flags", "adaptive_score_invalid")
                raise ValueError("flag keys must match ^[a-z][a-z0-9_]{0,40}$")
            if not isinstance(flag_value, bool):
                log_adaptive_schema_failure(cls.__name__, "flags", "adaptive_score_invalid")
                raise ValueError("flag values must be booleans")
        return value


class AdaptiveTransitionScheduleRequest(_Strict):
    expected_document_revision: int = Field(ge=1)
    from_state_id: str
    to_state_id: str
    transition_id: str | None = None
    position_tick: int = Field(ge=0)
    runtime: AdaptiveTransitionRuntimeV1 = Field(default_factory=AdaptiveTransitionRuntimeV1)

    @field_validator("expected_document_revision", "position_tick", mode="before")
    @classmethod
    def request_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="position_tick")
        return value

    @field_validator("from_state_id", "to_state_id")
    @classmethod
    def endpoint_token(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "from_state_id", "adaptive_score_invalid")
            raise ValueError("state id must be a short token")
        return value

    @field_validator("transition_id")
    @classmethod
    def optional_transition_token(cls, value: str | None) -> str | None:
        if value is not None and not _ID_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "transition_id", "adaptive_score_invalid")
            raise ValueError("transition id must be a short token")
        return value


class AdaptiveTransitionScheduleWarningV1(_Strict):
    code: str = Field(min_length=1, max_length=80)
    target_id: str | None = Field(default=None, max_length=80)
    message: str = Field(default="", max_length=200)


class AdaptiveLayerProposalV1(_Strict):
    """Create-layer fields without an id. Preview and commands share this shape."""

    name: str
    material: AdaptiveMaterialRefV1
    state_id: str | None = None
    intensity_min: float = Field(ge=0, le=1)
    intensity_max: float = Field(ge=0, le=1)
    mix_hint: AdaptiveMixHint
    default_active: bool = False
    role: AdaptiveLayerRole = "other"
    exclusive_group: str | None = None
    priority: int = 0
    fade: AdaptiveLayerFadeV1 = Field(default_factory=AdaptiveLayerFadeV1)

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str | None) -> str | None:
        return _optional_entity_token(value, model=cls.__name__, field="state_id")

    @field_validator("exclusive_group")
    @classmethod
    def group_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _FLAG_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "exclusive_group", "adaptive_score_invalid")
            raise ValueError("exclusive_group must match ^[a-z][a-z0-9_]{0,40}$")
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def priority_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="priority")
        return value

    @field_validator("intensity_min", "intensity_max", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @model_validator(mode="after")
    def intensity_window(self) -> AdaptiveLayerProposalV1:
        if self.intensity_max < self.intensity_min:
            log_adaptive_schema_failure(self.__class__.__name__, "intensity_max", "duration_bounds")
            raise ValueError("intensity_max must be >= intensity_min")
        return self


class AdaptiveLayerIntensityRequest(_Strict):
    expected_document_revision: int = Field(ge=1)
    state_id: str
    intensity: float | None = Field(default=None, ge=0, le=1)
    position_tick: int | None = Field(default=None, ge=0)
    previous_intensity: float | None = Field(default=None, ge=0, le=1)

    @field_validator("expected_document_revision", "position_tick", mode="before")
    @classmethod
    def request_int(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="position_tick")
        return value

    @field_validator("intensity", "previous_intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="intensity")
        return value

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="state_id")


class AdaptiveLayerPlanPreviewRequest(AdaptiveLayerIntensityRequest):
    proposals: list[AdaptiveLayerProposalV1] = Field(min_length=1, max_length=32)


class AdaptiveLayerIntensityLayerV1(_Strict):
    layer_id: str
    role: AdaptiveLayerRole
    material_kind: AdaptiveMaterialKind
    track_ids: list[str] = Field(default_factory=list, max_length=16)
    active: bool
    audible: bool
    target_gain: int = Field(ge=0, le=1)
    reason: AdaptiveLayerReason
    suppressed_by: str | None = None
    in_policy: AdaptiveLayerFadePolicy
    out_policy: AdaptiveLayerFadePolicy
    fade_ms: int = Field(ge=0, le=4000)
    fade_end_tick: int = Field(ge=0)

    @field_validator("target_gain", "fade_ms", "fade_end_tick", mode="before")
    @classmethod
    def row_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="target_gain")
        return value


class AdaptiveLayerIntensityV1(_Strict):
    schema_version: Literal["adaptive.layer.intensity.v1"] = ADAPTIVE_LAYER_INTENSITY_SCHEMA
    project_id: str
    score_id: str
    document_revision: int = Field(ge=1)
    state_id: str
    intensity: float = Field(ge=0, le=1)
    position_tick: int = Field(ge=0)
    layers: list[AdaptiveLayerIntensityLayerV1] = Field(default_factory=list, max_length=32)
    warnings: list[AdaptiveTransitionScheduleWarningV1] = Field(
        default_factory=list, max_length=32
    )

    @field_validator("position_tick", mode="before")
    @classmethod
    def position_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="position_tick")
        return value

    @field_validator("intensity", mode="before")
    @classmethod
    def intensity_number(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="intensity")
        return value


class EditLayerPayload(_Strict):
    layer_id: str
    name: str | None = None
    material: AdaptiveMaterialRefV1 | None = None
    state_id: str | None = None
    intensity_min: float | None = Field(default=None, ge=0, le=1)
    intensity_max: float | None = Field(default=None, ge=0, le=1)
    mix_hint: AdaptiveMixHint | None = None
    default_active: bool | None = None
    role: AdaptiveLayerRole | None = None
    exclusive_group: str | None = None
    priority: int | None = None
    fade: AdaptiveLayerFadeV1 | None = None

    @field_validator("layer_id")
    @classmethod
    def layer_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="layer_id")

    @field_validator("state_id")
    @classmethod
    def state_token(cls, value: str | None) -> str | None:
        return _optional_entity_token(value, model=cls.__name__, field="state_id")

    @field_validator("exclusive_group")
    @classmethod
    def group_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _FLAG_RE.fullmatch(value):
            log_adaptive_schema_failure(cls.__name__, "exclusive_group", "adaptive_score_invalid")
            raise ValueError("exclusive_group must match ^[a-z][a-z0-9_]{0,40}$")
        return value

    @field_validator("priority", mode="before")
    @classmethod
    def priority_int(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="priority")
        return value

    @field_validator("intensity_min", "intensity_max", mode="before")
    @classmethod
    def window_number(cls, value: object) -> object:
        if value is not None:
            _reject_bool(value, model=cls.__name__, field="intensity")
        return value


class DeleteLayerPayload(_Strict):
    layer_id: str

    @field_validator("layer_id")
    @classmethod
    def layer_token(cls, value: str) -> str:
        return _entity_token(value, model=cls.__name__, field="layer_id")


class CreateLayerCommand(_CommandEnvelope):
    op: Literal["create_layer"]
    payload: AdaptiveLayerProposalV1


class EditLayerCommand(_CommandEnvelope):
    op: Literal["edit_layer"]
    payload: EditLayerPayload


class DeleteLayerCommand(_CommandEnvelope):
    op: Literal["delete_layer"]
    payload: DeleteLayerPayload


AdaptiveScoreCommand = Annotated[
    CreateStateCommand
    | DeleteStateCommand
    | DuplicateStateCommand
    | AssignMaterialCommand
    | CreateTransitionCommand
    | EditTransitionCommand
    | AssignLoopCommand
    | AssignIntensityCommand
    | AssignBoundaryCommand
    | CreateLayerCommand
    | EditLayerCommand
    | DeleteLayerCommand,
    Field(discriminator="op"),
]


class AdaptiveScheduleRealizationCutV1(_Strict):
    kind: Literal["cut"] = "cut"


class AdaptiveScheduleRealizationCrossfadeV1(_Strict):
    kind: Literal["crossfade"]
    crossfade_ms: int = Field(ge=0, le=4000)
    fade_start_tick: int = Field(ge=0)

    @field_validator("crossfade_ms", "fade_start_tick", mode="before")
    @classmethod
    def timing_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="crossfade_ms")
        return value


class AdaptiveScheduleRealizationPhraseV1(_Strict):
    kind: Literal["phrase"]
    material_kind: AdaptiveMaterialKind
    section_id: str | None = None
    motif_id: str | None = None
    revision_id: str | None = None
    asset_id: str | None = None
    track_ids: list[str] = Field(default_factory=list)


class AdaptiveScheduleRealizationStingerV1(_Strict):
    kind: Literal["stinger"]
    stinger_id: str
    interrupt_policy: Literal["overlay", "duck_bed", "wait_for_exit"]


class AdaptiveScheduleRealizationOverlapV1(_Strict):
    kind: Literal["overlap"]
    overlap_bars: int = Field(ge=1, le=16)
    source_release_tick: int = Field(ge=0)

    @field_validator("overlap_bars", "source_release_tick", mode="before")
    @classmethod
    def overlap_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="overlap_bars")
        return value


AdaptiveScheduleRealizationV1 = Annotated[
    AdaptiveScheduleRealizationCutV1
    | AdaptiveScheduleRealizationCrossfadeV1
    | AdaptiveScheduleRealizationPhraseV1
    | AdaptiveScheduleRealizationStingerV1
    | AdaptiveScheduleRealizationOverlapV1,
    Field(discriminator="kind"),
]


class AdaptiveTransitionScheduleV1(_Strict):
    schema_version: Literal["adaptive.transition.schedule.v1"] = ADAPTIVE_TRANSITION_SCHEDULE_SCHEMA
    request_id: str = Field(pattern=r"^treq_[0-9a-f]{8}$")
    status: Literal["pending"] = "pending"
    project_id: str
    score_id: str
    document_revision: int = Field(ge=1)
    transition_id: str
    from_state_id: str
    to_state_id: str
    quantization: AdaptiveTransitionQuantization
    boundary_tick: int = Field(ge=0)
    boundary_bar: int = Field(ge=1)
    latency_ticks: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    tempo_bpm: int = Field(ge=1)
    time_signature: str = Field(min_length=3, max_length=16)
    aligned: bool
    realization: AdaptiveScheduleRealizationV1
    replaced_request_id: str | None = None
    warnings: list[AdaptiveTransitionScheduleWarningV1] = Field(default_factory=list, max_length=8)

    @field_validator("latency_ticks", "latency_ms", "boundary_tick", "tempo_bpm", mode="before")
    @classmethod
    def schedule_int(cls, value: int) -> int:
        _reject_bool(value, model=cls.__name__, field="latency_ms")
        return value


def _validation_field(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    if not loc:
        return "body"
    return str(loc[-1])


def _embedded_key_paths(payload: Any, *, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_path = f"{path}.{key}" if path else str(key)
            if str(key) in FORBIDDEN_NOTE_KEYS:
                found.append(key_path)
            found.extend(_embedded_key_paths(value, path=key_path))
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            found.extend(_embedded_key_paths(item, path=f"{path}[{index}]"))
    return found


def parse_adaptive_score(data: dict[str, Any]) -> AdaptiveScoreV1:
    """Validate a raw object into ``AdaptiveScoreV1`` with stable error codes."""
    if not isinstance(data, dict):
        log_adaptive_schema_failure("AdaptiveScoreV1", "body", "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            "Adaptive score body must be an object",
            http_status=422,
        )
    hits = _embedded_key_paths(data)
    if hits:
        log_adaptive_schema_failure("AdaptiveScoreV1", "body", "embedded_note_material")
        raise AdaptiveScoreError(
            "embedded_note_material",
            ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"],
            http_status=422,
            details={"hit_count": len(hits)},
        )
    version = data.get("schema_version", ADAPTIVE_SCORE_SCHEMA)
    if version != ADAPTIVE_SCORE_SCHEMA:
        log_adaptive_schema_failure("AdaptiveScoreV1", "schema_version", "unsupported_schema_version")
        raise AdaptiveScoreError(
            "unsupported_schema_version",
            ADAPTIVE_SCORE_ERROR_CODES["unsupported_schema_version"],
            http_status=422,
            details={"schema_version": str(version)[:40]},
        )
    try:
        return AdaptiveScoreV1.model_validate(data)
    except ValidationError as exc:
        text = str(exc)
        if "unsupported_schema_version" in text:
            code = "unsupported_schema_version"
        elif "identity_mismatch" in text:
            code = "identity_mismatch"
        elif "adaptive_score_too_large" in text:
            code = "adaptive_score_too_large"
        else:
            code = "adaptive_score_invalid"
        field = _validation_field(exc)
        log_adaptive_schema_failure("AdaptiveScoreV1", field, code)
        status = 422
        raise AdaptiveScoreError(
            code,
            ADAPTIVE_SCORE_ERROR_CODES.get(code, "Adaptive score failed schema validation."),
            http_status=status,
            details={"field": field},
        ) from exc


def _parse_model(data: dict[str, Any], model: type[BaseModel], label: str) -> BaseModel:
    if not isinstance(data, dict):
        log_adaptive_schema_failure(label, "body", "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            f"{label} must be an object",
            http_status=422,
        )
    hits = _embedded_key_paths(data)
    if hits:
        log_adaptive_schema_failure(label, "body", "embedded_note_material")
        raise AdaptiveScoreError(
            "embedded_note_material",
            ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"],
            http_status=422,
            details={"hit_count": len(hits)},
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        field = _validation_field(exc)
        log_adaptive_schema_failure(label, field, "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_invalid"],
            http_status=422,
            details={"field": field},
        ) from exc


def parse_adaptive_layer_intensity_request(data: dict[str, Any]) -> AdaptiveLayerIntensityRequest:
    """Validate a read-only intensity body. Does not log the document."""
    parsed = _parse_model(data, AdaptiveLayerIntensityRequest, "AdaptiveLayerIntensityRequest")
    assert isinstance(parsed, AdaptiveLayerIntensityRequest)
    return parsed


def parse_adaptive_layer_plan_preview_request(
    data: dict[str, Any],
) -> AdaptiveLayerPlanPreviewRequest:
    """Validate a layer-plan preview body. Does not log proposals."""
    parsed = _parse_model(data, AdaptiveLayerPlanPreviewRequest, "AdaptiveLayerPlanPreviewRequest")
    assert isinstance(parsed, AdaptiveLayerPlanPreviewRequest)
    return parsed


def parse_adaptive_score_command(data: dict[str, Any]) -> AdaptiveScoreCommand:
    """Validate one command envelope. Does not log the payload."""
    if not isinstance(data, dict):
        log_adaptive_schema_failure("AdaptiveScoreCommand", "body", "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            "Adaptive score command must be an object",
            http_status=422,
        )
    hits = _embedded_key_paths(data)
    if hits:
        log_adaptive_schema_failure("AdaptiveScoreCommand", "body", "embedded_note_material")
        raise AdaptiveScoreError(
            "embedded_note_material",
            ADAPTIVE_SCORE_ERROR_CODES["embedded_note_material"],
            http_status=422,
            details={"hit_count": len(hits)},
        )
    try:
        return TypeAdapter(AdaptiveScoreCommand).validate_python(data)
    except ValidationError as exc:
        field = _validation_field(exc)
        log_adaptive_schema_failure("AdaptiveScoreCommand", field, "adaptive_score_invalid")
        raise AdaptiveScoreError(
            "adaptive_score_invalid",
            ADAPTIVE_SCORE_ERROR_CODES["adaptive_score_invalid"],
            http_status=422,
            details={"field": field},
        ) from exc
