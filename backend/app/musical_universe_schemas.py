"""Strict ``musical.universe.v1`` contracts.

A musical universe names story identities and points at motif occurrences on
member ``composition.v2`` scores. It is not a playable score and it is not
``composition.v5``. Note events never belong here.
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

from app.composition_schemas import (
    MotifRelationshipKind,
    midi_pitch_number,
    normalize_key,
    normalize_time_signature,
)

logger = logging.getLogger(__name__)

MUSICAL_UNIVERSE_SCHEMA: Literal["musical.universe.v1"] = "musical.universe.v1"

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

_SCORE_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"tracks", "tempo", "harmony"})

UNIVERSE_ID_RE = re.compile(r"^muniv_[0-9a-f]{16}$")
ENTITY_ID_RE = re.compile(r"^ent_[0-9a-f]{8}$")
THEME_ID_RE = re.compile(r"^theme_[0-9a-f]{8}$")
VARIANT_ID_RE = re.compile(r"^var_[0-9a-f]{8}$")
USAGE_ID_RE = re.compile(r"^use_[0-9a-f]{8}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EntityKind = Literal[
    "character",
    "location",
    "faction",
    "concept",
    "relationship",
    "narrative_theme",
]
TextureKind = Literal["sparse", "moderate", "dense"]
MechanicalOperation = Literal[
    "repeat",
    "transpose",
    "inversion",
    "augmentation",
    "diminution",
    "sequence",
]
MECHANICAL_OPERATIONS: frozenset[str] = frozenset(
    {"repeat", "transpose", "inversion", "augmentation", "diminution", "sequence"}
)

_PARAMETER_FIELDS: tuple[str, ...] = (
    "transpose_semitones",
    "inversion_axis_pitch",
    "time_scale_numerator",
    "time_scale_denominator",
    "sequence_steps",
    "sequence_interval_semitones",
    "sequence_step_ticks",
)
_OPERATION_FIELDS: dict[str, frozenset[str]] = {
    "repeat": frozenset(),
    "transpose": frozenset({"transpose_semitones"}),
    "inversion": frozenset({"inversion_axis_pitch"}),
    "augmentation": frozenset({"time_scale_numerator", "time_scale_denominator"}),
    "diminution": frozenset({"time_scale_numerator", "time_scale_denominator"}),
    "sequence": frozenset(
        {"sequence_steps", "sequence_interval_semitones", "sequence_step_ticks"}
    ),
}
_REQUIRED_PARAMETER_FIELDS: dict[str, frozenset[str]] = {
    "repeat": frozenset(),
    "transpose": frozenset({"transpose_semitones"}),
    "inversion": frozenset(),
    "augmentation": frozenset({"time_scale_numerator", "time_scale_denominator"}),
    "diminution": frozenset({"time_scale_numerator", "time_scale_denominator"}),
    "sequence": frozenset(
        {"sequence_steps", "sequence_interval_semitones", "sequence_step_ticks"}
    ),
}

MUSICAL_UNIVERSE_ERROR_CODES: dict[str, str] = {
    "embedded_note_material": "Musical universes cannot embed note events.",
    "musical_universe_invalid": "Musical universe failed schema validation.",
    "universe_operation_parameters": "Transform parameters do not match the operation.",
    "universe_operation_unsupported": "Reuse accepts only mechanical motif operations.",
    "musical_universe_conflict": "expected_universe_revision does not match the stored universe.",
    "musical_universe_name_conflict": "A musical universe with this name already exists.",
    "musical_universe_not_found": "Musical universe id was not found.",
    "universe_not_linked": "This project is not a member of a musical universe.",
    "universe_project_busy": "This project already belongs to a musical universe.",
    "universe_project_not_member": "Project is not a member of this universe.",
    "universe_source_missing": "The theme source occurrence does not resolve.",
    "universe_theme_source_not_original": "Theme source occurrence must be original.",
    "universe_entity_in_use": "A theme still points at this entity.",
    "universe_variant_in_use": "A usage still points at this variant.",
    "universe_harmony_missing": "Harmony start_tick does not match a member score.",
    "universe_track_missing": "Track id does not exist on that project.",
    "universe_instrument_unknown": "Catalog instrument id is not in the arrangement catalog.",
    "universe_destination_overflow": "The transformed motif does not fit the destination.",
    "universe_destination_overlap": "The transformed motif overlaps destination notes.",
    "universe_pitch_out_of_range": "A transformed pitch is outside the destination range.",
    "universe_identity_failed": "The transformed motif failed the identity check.",
    "universe_transform_too_long": "The transform created more than 32 events.",
    "universe_destination_conflict": "Destination branch fingerprint does not match.",
    "universe_motif_identity_conflict": "Destination motif pins name another universe or theme.",
    "universe_destination_track_rejected": "Destination track cannot receive a pitched theme.",
    "persistence_secret_rejected": "Musical universe payload contains a secret field or value.",
    "project_not_found": "Project id was not found.",
    "musical_universe_store_failed": "Musical universe persistence failed.",
}


def log_universe_schema_failure(model: str, code: str) -> None:
    """DEBUG schema rejection without labels or note fields."""
    logger.debug(
        "Musical universe schema rejected",
        extra={"model": model, "code": code},
    )


def normalize_universe_name(name: str) -> str:
    """NFKC, collapse whitespace, then casefold."""
    cleaned = " ".join(unicodedata.normalize("NFKC", name).strip().split())
    return unicodedata.normalize("NFKC", cleaned).casefold()


class MusicalUniverseError(Exception):
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


def map_musical_universe_error_to_http(exc: MusicalUniverseError) -> tuple[int, dict[str, Any]]:
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
        log_universe_schema_failure(model, "musical_universe_invalid")
        raise ValueError(f"{field} must not be a boolean")


def is_mechanical_relationship(value: MotifRelationshipKind) -> bool:
    """True for the six reuse operations. ``original`` and creative kinds are false."""
    return value in MECHANICAL_OPERATIONS


def _scan_embedded(payload: Any) -> list[str]:
    """Walk dicts and lists. List branch enumerates correctly."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_NOTE_KEYS:
                found.append(key_text)
            found.extend(_scan_embedded(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_scan_embedded(item))
    return found


class UniverseMotifRefV1(_Strict):
    """Pointer at a motif occurrence. Pitches stay on the member score."""

    project_id: str = Field(..., min_length=1, max_length=64)
    motif_id: str = Field(..., min_length=1, max_length=120)
    occurrence_id: str = Field(..., min_length=1, max_length=120)

    @field_validator("project_id", "motif_id", "occurrence_id")
    @classmethod
    def strip_ids(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("motif reference ids must not be empty")
        return cleaned


class UniverseHarmonyRefV1(_Strict):
    project_id: str = Field(..., min_length=1, max_length=64)
    start_tick: int = Field(..., ge=0)

    @field_validator("start_tick", mode="before")
    @classmethod
    def start_tick_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="start_tick")
        return value

    @field_validator("project_id")
    @classmethod
    def strip_project_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("project_id must not be empty")
        return cleaned


class UniverseTrackRefV1(_Strict):
    project_id: str = Field(..., min_length=1, max_length=64)
    track_id: str = Field(..., min_length=1, max_length=80)

    @field_validator("project_id", "track_id")
    @classmethod
    def strip_ids(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("track reference ids must not be empty")
        return cleaned


class UniverseTransformParameters(_Strict):
    """Parameter bag for one mechanical operation. Unset fields stay absent."""

    transpose_semitones: int | None = Field(default=None, ge=-48, le=48)
    inversion_axis_pitch: str | None = None
    time_scale_numerator: int | None = Field(default=None, ge=1, le=8)
    time_scale_denominator: int | None = Field(default=None, ge=1, le=8)
    sequence_steps: int | None = Field(default=None, ge=1, le=16)
    sequence_interval_semitones: int | None = Field(default=None, ge=-24, le=24)
    sequence_step_ticks: int | None = Field(default=None, gt=0)

    @field_validator(
        "transpose_semitones",
        "time_scale_numerator",
        "time_scale_denominator",
        "sequence_steps",
        "sequence_interval_semitones",
        "sequence_step_ticks",
        mode="before",
    )
    @classmethod
    def reject_bool_parameters(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="parameters")
        return value

    @field_validator("inversion_axis_pitch")
    @classmethod
    def validate_axis(cls, value: str | None) -> str | None:
        if value is None:
            return None
        pitch = value.strip()
        try:
            midi_pitch_number(pitch)
        except ValueError as exc:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("inversion_axis_pitch must be scientific pitch notation") from exc
        return pitch


def assert_operation_parameters(
    operation: str,
    parameters: UniverseTransformParameters,
) -> None:
    """Require only the fields for ``operation``. Other set fields are rejected."""
    if operation not in MECHANICAL_OPERATIONS:
        log_universe_schema_failure("UniverseTransformParameters", "universe_operation_unsupported")
        raise MusicalUniverseError(
            "universe_operation_unsupported",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_operation_unsupported"],
            http_status=422,
        )
    allowed = _OPERATION_FIELDS[operation]
    required = _REQUIRED_PARAMETER_FIELDS[operation]
    present = {
        name
        for name in _PARAMETER_FIELDS
        if getattr(parameters, name) is not None
    }
    if present - allowed or required - present:
        log_universe_schema_failure("UniverseTransformParameters", "universe_operation_parameters")
        raise MusicalUniverseError(
            "universe_operation_parameters",
            MUSICAL_UNIVERSE_ERROR_CODES["universe_operation_parameters"],
            http_status=422,
            details={"operation": operation},
        )


class MusicalUniverseEntityV1(_Strict):
    id: str = Field(..., pattern=ENTITY_ID_RE.pattern)
    kind: EntityKind
    label: str = Field(..., min_length=1, max_length=120)
    subject_entity_id: str | None = Field(default=None, pattern=ENTITY_ID_RE.pattern)
    object_entity_id: str | None = Field(default=None, pattern=ENTITY_ID_RE.pattern)
    motif_refs: list[UniverseMotifRefV1] = Field(default_factory=list, max_length=16)
    harmony_refs: list[UniverseHarmonyRefV1] = Field(default_factory=list, max_length=16)
    track_refs: list[UniverseTrackRefV1] = Field(default_factory=list, max_length=16)
    keys: list[str] = Field(default_factory=list, max_length=8)
    time_signature: str | None = None
    texture: TextureKind | None = None
    catalog_instrument_ids: list[str] = Field(default_factory=list, max_length=8)
    known_operations: list[MechanicalOperation] = Field(default_factory=list, max_length=8)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("label must not be empty")
        return cleaned

    @field_validator("keys")
    @classmethod
    def normalize_keys(cls, value: list[str]) -> list[str]:
        return [normalize_key(item, model_name=cls.__name__) for item in value]

    @field_validator("time_signature")
    @classmethod
    def normalize_meter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_time_signature(value, model_name=cls.__name__)

    @field_validator("catalog_instrument_ids")
    @classmethod
    def strip_instruments(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            if not text or len(text) > 80:
                log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
                raise ValueError("catalog_instrument_ids entries must be 1..80 characters")
            cleaned.append(text)
        return cleaned

    @field_validator("known_operations")
    @classmethod
    def unique_operations(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("known_operations must be unique")
        if "original" in value:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("known_operations must not include original")
        return value

    @model_validator(mode="after")
    def relationship_endpoints(self) -> MusicalUniverseEntityV1:
        subject = self.subject_entity_id
        object_id = self.object_entity_id
        if self.kind == "relationship":
            if subject is None or object_id is None:
                log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
                raise ValueError("relationship entities require subject_entity_id and object_entity_id")
            if subject == object_id or subject == self.id or object_id == self.id:
                log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
                raise ValueError("relationship endpoints must differ and must not be this entity")
        elif subject is not None or object_id is not None:
            log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
            raise ValueError("subject_entity_id and object_entity_id are only valid on relationships")
        return self


class MusicalUniverseVariantV1(_Strict):
    id: str = Field(..., pattern=VARIANT_ID_RE.pattern)
    label: str = Field(..., min_length=1, max_length=120)
    operation: MechanicalOperation
    parameters: UniverseTransformParameters
    source_project_id: str = Field(..., min_length=1, max_length=64)
    source_motif_id: str = Field(..., min_length=1, max_length=120)
    source_occurrence_id: str = Field(..., min_length=1, max_length=120)

    @field_validator("label", "source_project_id", "source_motif_id", "source_occurrence_id")
    @classmethod
    def strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("variant text fields must not be empty")
        return cleaned

    @model_validator(mode="after")
    def parameters_match_operation(self) -> MusicalUniverseVariantV1:
        try:
            assert_operation_parameters(self.operation, self.parameters)
        except MusicalUniverseError as exc:
            raise ValueError(exc.code) from exc
        return self


class MusicalUniverseUsageV1(_Strict):
    id: str = Field(..., pattern=USAGE_ID_RE.pattern)
    variant_id: str = Field(..., pattern=VARIANT_ID_RE.pattern)
    destination_project_id: str = Field(..., min_length=1, max_length=64)
    destination_motif_id: str = Field(..., min_length=1, max_length=120)
    destination_occurrence_id: str = Field(..., min_length=1, max_length=120)
    source_occurrence_id: str = Field(..., min_length=1, max_length=120)
    destination_revision_id: str = Field(..., min_length=1, max_length=80)
    operation: MechanicalOperation
    parameters: UniverseTransformParameters
    source_fingerprint: str = Field(..., pattern=SHA256_RE.pattern)
    warning_codes: list[str] = Field(default_factory=list, max_length=8)

    @field_validator(
        "destination_project_id",
        "destination_motif_id",
        "destination_occurrence_id",
        "source_occurrence_id",
        "destination_revision_id",
    )
    @classmethod
    def strip_destination(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("usage ids must not be empty")
        return cleaned

    @field_validator("warning_codes")
    @classmethod
    def strip_warnings(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = item.strip()
            if not text or len(text) > 80:
                log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
                raise ValueError("warning_codes entries must be 1..80 characters")
            cleaned.append(text)
        return cleaned

    @model_validator(mode="after")
    def parameters_match_operation(self) -> MusicalUniverseUsageV1:
        try:
            assert_operation_parameters(self.operation, self.parameters)
        except MusicalUniverseError as exc:
            raise ValueError(exc.code) from exc
        return self


class MusicalUniverseThemeV1(_Strict):
    id: str = Field(..., pattern=THEME_ID_RE.pattern)
    entity_id: str = Field(..., pattern=ENTITY_ID_RE.pattern)
    label: str = Field(..., min_length=1, max_length=120)
    source: UniverseMotifRefV1
    source_fingerprint: str = Field(..., pattern=SHA256_RE.pattern)
    variants: list[MusicalUniverseVariantV1] = Field(default_factory=list, max_length=32)
    usages: list[MusicalUniverseUsageV1] = Field(default_factory=list, max_length=128)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("label must not be empty")
        return cleaned

    @model_validator(mode="after")
    def unique_children(self) -> MusicalUniverseThemeV1:
        variant_ids = [item.id for item in self.variants]
        if len(variant_ids) != len(set(variant_ids)):
            log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
            raise ValueError("variant ids must be unique within a theme")
        usage_ids = [item.id for item in self.usages]
        if len(usage_ids) != len(set(usage_ids)):
            log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
            raise ValueError("usage ids must be unique within a theme")
        known = set(variant_ids)
        for usage in self.usages:
            if usage.variant_id not in known:
                log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
                raise ValueError("usage variant_id must name a variant on this theme")
        return self


class MusicalUniverseV1(_Strict):
    schema_version: Literal["musical.universe.v1"] = MUSICAL_UNIVERSE_SCHEMA
    id: str = Field(..., pattern=UNIVERSE_ID_RE.pattern)
    name: str = Field(..., min_length=1, max_length=120)
    entities: list[MusicalUniverseEntityV1] = Field(default_factory=list, max_length=64)
    themes: list[MusicalUniverseThemeV1] = Field(default_factory=list, max_length=64)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("name must not be empty")
        return cleaned

    @model_validator(mode="after")
    def document_links(self) -> MusicalUniverseV1:
        entity_ids = [item.id for item in self.entities]
        if len(entity_ids) != len(set(entity_ids)):
            log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
            raise ValueError("entity ids must be unique")
        known_entities = set(entity_ids)
        for entity in self.entities:
            if entity.kind != "relationship":
                continue
            subject = entity.subject_entity_id
            object_id = entity.object_entity_id
            if subject not in known_entities or object_id not in known_entities:
                log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
                raise ValueError("relationship endpoints must name entities on this document")
        theme_ids = [item.id for item in self.themes]
        if len(theme_ids) != len(set(theme_ids)):
            log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
            raise ValueError("theme ids must be unique")
        for theme in self.themes:
            if theme.entity_id not in known_entities:
                log_universe_schema_failure(self.__class__.__name__, "musical_universe_invalid")
                raise ValueError("theme entity_id must name an entity on this document")
        return self


class MusicalUniverseFindingV1(_Strict):
    code: str = Field(..., min_length=1, max_length=80)
    severity: Literal["warning", "error"]
    target_id: str | None = Field(default=None, max_length=120)
    message: str = Field(default="", max_length=200)


class CreateEntityCommand(_Strict):
    op: Literal["create_entity"]
    kind: EntityKind
    label: str = Field(..., min_length=1, max_length=120)
    subject_entity_id: str | None = Field(default=None, pattern=ENTITY_ID_RE.pattern)
    object_entity_id: str | None = Field(default=None, pattern=ENTITY_ID_RE.pattern)
    motif_refs: list[UniverseMotifRefV1] = Field(default_factory=list, max_length=16)
    harmony_refs: list[UniverseHarmonyRefV1] = Field(default_factory=list, max_length=16)
    track_refs: list[UniverseTrackRefV1] = Field(default_factory=list, max_length=16)
    keys: list[str] = Field(default_factory=list, max_length=8)
    time_signature: str | None = None
    texture: TextureKind | None = None
    catalog_instrument_ids: list[str] = Field(default_factory=list, max_length=8)
    known_operations: list[MechanicalOperation] = Field(default_factory=list, max_length=8)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("label must not be empty")
        return cleaned

    @field_validator("keys")
    @classmethod
    def normalize_keys(cls, value: list[str]) -> list[str]:
        return [normalize_key(item, model_name=cls.__name__) for item in value]

    @field_validator("time_signature")
    @classmethod
    def normalize_meter(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_time_signature(value, model_name=cls.__name__)


class EditEntityCommand(_Strict):
    op: Literal["edit_entity"]
    entity_id: str = Field(..., pattern=ENTITY_ID_RE.pattern)
    label: str = Field(..., min_length=1, max_length=120)
    subject_entity_id: str | None = Field(default=None, pattern=ENTITY_ID_RE.pattern)
    object_entity_id: str | None = Field(default=None, pattern=ENTITY_ID_RE.pattern)
    motif_refs: list[UniverseMotifRefV1] = Field(default_factory=list, max_length=16)
    harmony_refs: list[UniverseHarmonyRefV1] = Field(default_factory=list, max_length=16)
    track_refs: list[UniverseTrackRefV1] = Field(default_factory=list, max_length=16)
    keys: list[str] = Field(default_factory=list, max_length=8)
    time_signature: str | None = None
    texture: TextureKind | None = None
    catalog_instrument_ids: list[str] = Field(default_factory=list, max_length=8)
    known_operations: list[MechanicalOperation] = Field(default_factory=list, max_length=8)

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("label must not be empty")
        return cleaned


class DeleteEntityCommand(_Strict):
    op: Literal["delete_entity"]
    entity_id: str = Field(..., pattern=ENTITY_ID_RE.pattern)


class CreateThemeCommand(_Strict):
    """Bind a theme. ``source_checked`` is set only after the caller verified the occurrence."""

    op: Literal["create_theme"]
    entity_id: str = Field(..., pattern=ENTITY_ID_RE.pattern)
    label: str = Field(..., min_length=1, max_length=120)
    source: UniverseMotifRefV1
    source_fingerprint: str = Field(..., pattern=SHA256_RE.pattern)
    source_checked: Literal[True]

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("label must not be empty")
        return cleaned


class AddVariantCommand(_Strict):
    op: Literal["add_variant"]
    theme_id: str = Field(..., pattern=THEME_ID_RE.pattern)
    label: str = Field(..., min_length=1, max_length=120)
    operation: MechanicalOperation
    parameters: UniverseTransformParameters

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("label must not be empty")
        return cleaned

    @model_validator(mode="after")
    def parameters_match_operation(self) -> AddVariantCommand:
        try:
            assert_operation_parameters(self.operation, self.parameters)
        except MusicalUniverseError as exc:
            raise ValueError(exc.code) from exc
        return self


class DeleteVariantCommand(_Strict):
    op: Literal["delete_variant"]
    theme_id: str = Field(..., pattern=THEME_ID_RE.pattern)
    variant_id: str = Field(..., pattern=VARIANT_ID_RE.pattern)


MusicalUniverseCommand = Annotated[
    CreateEntityCommand
    | EditEntityCommand
    | DeleteEntityCommand
    | CreateThemeCommand
    | AddVariantCommand
    | DeleteVariantCommand,
    Field(discriminator="op"),
]


class MusicalUniverseCommandRequest(_Strict):
    expected_document_revision: int = Field(..., ge=1)
    command: MusicalUniverseCommand

    @field_validator("expected_document_revision", mode="before")
    @classmethod
    def revision_int(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="expected_document_revision")
        return value


class MusicalUniverseSummaryV1(_Strict):
    id: str = Field(..., pattern=UNIVERSE_ID_RE.pattern)
    name: str = Field(..., min_length=1, max_length=120)
    member_count: int = Field(..., ge=0)
    document_revision: int = Field(..., ge=1)


class MusicalUniverseListResponse(_Strict):
    universes: list[MusicalUniverseSummaryV1] = Field(default_factory=list, max_length=256)


class MusicalUniverseGetResponse(_Strict):
    universe: MusicalUniverseV1
    member_project_ids: list[str] = Field(default_factory=list, max_length=256)
    document_revision: int = Field(..., ge=1)


class MusicalUniverseCreateRequest(_Strict):
    name: str = Field(..., min_length=1, max_length=120)
    project_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("name", "project_id")
    @classmethod
    def strip_create(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split()) if " " in value else value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("create fields must not be empty")
        return cleaned


class MusicalUniverseMemberRequest(_Strict):
    project_id: str = Field(..., min_length=1, max_length=64)

    @field_validator("project_id")
    @classmethod
    def strip_project_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            log_universe_schema_failure(cls.__name__, "musical_universe_invalid")
            raise ValueError("project_id must not be empty")
        return cleaned


class MusicalUniverseValidateResponse(_Strict):
    findings: list[MusicalUniverseFindingV1] = Field(default_factory=list, max_length=256)


class MusicalUniverseReuseRequest(_Strict):
    destination_project_id: str = Field(..., min_length=1, max_length=64)
    destination_track_id: str = Field(..., min_length=1, max_length=80)
    destination_start_bar: int = Field(..., ge=1)
    operation: str | None = Field(default=None, min_length=1, max_length=40)
    parameters: UniverseTransformParameters | None = None
    variant_id: str | None = Field(default=None, pattern=VARIANT_ID_RE.pattern)
    expected_universe_revision: int = Field(..., ge=1)
    branch_id: str = Field(..., min_length=1, max_length=80)
    expected_active_branch_id: str = Field(..., min_length=1, max_length=80)
    expected_working_version: int = Field(..., ge=0)
    expected_head_revision_id: str = Field(..., min_length=1, max_length=80)
    expected_source_fingerprint: str = Field(..., min_length=16, max_length=128)

    @field_validator("destination_start_bar", "expected_universe_revision", "expected_working_version", mode="before")
    @classmethod
    def reuse_ints(cls, value: object) -> object:
        _reject_bool(value, model=cls.__name__, field="reuse")
        return value


def _validation_field(exc: ValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "body"
    loc = errors[0].get("loc") or ()
    if not loc:
        return "body"
    return str(loc[-1])


def reject_embedded_note_material(payload: Any) -> None:
    """Raise when a raw tree contains note-event keys. Does not log the tree."""
    _raise_embedded("MusicalUniverseV1", payload)


def _raise_embedded(model: str, payload: Any) -> None:
    hits = _scan_embedded(payload)
    if not hits:
        return
    log_universe_schema_failure(model, "embedded_note_material")
    raise MusicalUniverseError(
        "embedded_note_material",
        MUSICAL_UNIVERSE_ERROR_CODES["embedded_note_material"],
        http_status=422,
        details={"hit_count": len(hits)},
    )


def _reject_score_shaped(data: dict[str, Any]) -> None:
    present = sorted(key for key in _SCORE_TOP_LEVEL_KEYS if key in data)
    if not present:
        return
    log_universe_schema_failure("MusicalUniverseV1", "musical_universe_invalid")
    raise MusicalUniverseError(
        "musical_universe_invalid",
        MUSICAL_UNIVERSE_ERROR_CODES["musical_universe_invalid"],
        http_status=422,
        details={"field": present[0]},
    )


def _map_validation(model: str, exc: ValidationError) -> MusicalUniverseError:
    text = str(exc)
    if "universe_operation_parameters" in text:
        code = "universe_operation_parameters"
    elif "universe_operation_unsupported" in text:
        code = "universe_operation_unsupported"
    else:
        code = "musical_universe_invalid"
    field = _validation_field(exc)
    log_universe_schema_failure(model, code)
    return MusicalUniverseError(
        code,
        MUSICAL_UNIVERSE_ERROR_CODES.get(code, MUSICAL_UNIVERSE_ERROR_CODES["musical_universe_invalid"]),
        http_status=422,
        details={"field": field},
    )


def parse_musical_universe(data: dict[str, Any]) -> MusicalUniverseV1:
    """Validate a raw object into ``MusicalUniverseV1`` with stable error codes."""
    if not isinstance(data, dict):
        log_universe_schema_failure("MusicalUniverseV1", "musical_universe_invalid")
        raise MusicalUniverseError(
            "musical_universe_invalid",
            "Musical universe body must be an object",
            http_status=422,
        )
    _raise_embedded("MusicalUniverseV1", data)
    _reject_score_shaped(data)
    version = data.get("schema_version", MUSICAL_UNIVERSE_SCHEMA)
    if version != MUSICAL_UNIVERSE_SCHEMA:
        log_universe_schema_failure("MusicalUniverseV1", "musical_universe_invalid")
        raise MusicalUniverseError(
            "musical_universe_invalid",
            MUSICAL_UNIVERSE_ERROR_CODES["musical_universe_invalid"],
            http_status=422,
            details={"field": "schema_version"},
        )
    try:
        return MusicalUniverseV1.model_validate(data)
    except MusicalUniverseError:
        raise
    except ValidationError as exc:
        raise _map_validation("MusicalUniverseV1", exc) from exc


def parse_musical_universe_command(data: dict[str, Any]) -> MusicalUniverseCommand:
    """Validate one command. A ``notes`` key is rejected before a document is built."""
    if not isinstance(data, dict):
        log_universe_schema_failure("MusicalUniverseCommand", "musical_universe_invalid")
        raise MusicalUniverseError(
            "musical_universe_invalid",
            "Musical universe command must be an object",
            http_status=422,
        )
    _raise_embedded("MusicalUniverseCommand", data)
    try:
        return TypeAdapter(MusicalUniverseCommand).validate_python(data)
    except MusicalUniverseError:
        raise
    except ValidationError as exc:
        raise _map_validation("MusicalUniverseCommand", exc) from exc


def parse_musical_universe_reuse_request(data: dict[str, Any]) -> MusicalUniverseReuseRequest:
    if not isinstance(data, dict):
        log_universe_schema_failure("MusicalUniverseReuseRequest", "musical_universe_invalid")
        raise MusicalUniverseError(
            "musical_universe_invalid",
            "Musical universe reuse request must be an object",
            http_status=422,
        )
    _raise_embedded("MusicalUniverseReuseRequest", data)
    if any(key in data for key in ("composition", "tracks", "tempo", "harmony")):
        log_universe_schema_failure("MusicalUniverseReuseRequest", "musical_universe_invalid")
        raise MusicalUniverseError(
            "musical_universe_invalid",
            MUSICAL_UNIVERSE_ERROR_CODES["musical_universe_invalid"],
            http_status=422,
            details={"field": "composition"},
        )
    try:
        return MusicalUniverseReuseRequest.model_validate(data)
    except MusicalUniverseError:
        raise
    except ValidationError as exc:
        raise _map_validation("MusicalUniverseReuseRequest", exc) from exc


def parse_musical_universe_command_request(data: dict[str, Any]) -> MusicalUniverseCommandRequest:
    if not isinstance(data, dict):
        log_universe_schema_failure("MusicalUniverseCommandRequest", "musical_universe_invalid")
        raise MusicalUniverseError(
            "musical_universe_invalid",
            "Musical universe command request must be an object",
            http_status=422,
        )
    _raise_embedded("MusicalUniverseCommandRequest", data)
    try:
        return MusicalUniverseCommandRequest.model_validate(data)
    except MusicalUniverseError:
        raise
    except ValidationError as exc:
        raise _map_validation("MusicalUniverseCommandRequest", exc) from exc


# Re-exported so motif pins and commands share one relationship vocabulary.
__all__ = [
    "FORBIDDEN_NOTE_KEYS",
    "MECHANICAL_OPERATIONS",
    "MUSICAL_UNIVERSE_SCHEMA",
    "MechanicalOperation",
    "MotifRelationshipKind",
    "MusicalUniverseCommand",
    "MusicalUniverseCommandRequest",
    "MusicalUniverseEntityV1",
    "MusicalUniverseError",
    "MusicalUniverseFindingV1",
    "MusicalUniverseGetResponse",
    "MusicalUniverseSummaryV1",
    "MusicalUniverseThemeV1",
    "MusicalUniverseUsageV1",
    "MusicalUniverseV1",
    "MusicalUniverseVariantV1",
    "UniverseMotifRefV1",
    "UniverseTransformParameters",
    "assert_operation_parameters",
    "log_universe_schema_failure",
    "is_mechanical_relationship",
    "map_musical_universe_error_to_http",
    "normalize_universe_name",
    "parse_musical_universe",
    "parse_musical_universe_command",
    "parse_musical_universe_command_request",
    "reject_embedded_note_material",
]
