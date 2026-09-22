"""Strict ``composer.profile.v1`` preference contracts.

Composer Profiles are durable named preference documents — never playable scores,
never full analysis reports, never embedding vectors, never dataset rows.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

logger = logging.getLogger(__name__)

COMPOSER_PROFILE_SCHEMA: Literal["composer.profile.v1"] = "composer.profile.v1"
COMPOSER_PROFILE_EXPORT_SCHEMA: Literal["composer.profile.export.v1"] = (
    "composer.profile.export.v1"
)

ProfileStrength = Literal["off", "light", "normal", "strong"]
PROFILE_STRENGTHS: frozenset[str] = frozenset({"off", "light", "normal", "strong"})

# Bounded preference bands (coarse enums — never raw note events).
PreferenceBand = Literal["very_low", "low", "moderate", "high", "very_high"]
TensionShapeCode = Literal[
    "flat",
    "rising",
    "falling",
    "arch",
    "valley",
    "plateau_high",
    "plateau_low",
    "irregular",
]

COMPOSER_PROFILE_NAME_MAX = 120
COMPOSER_PROFILE_NOTES_MAX = 2_000
COMPOSER_PROFILE_ID_MAX = 64
COMPOSER_PROFILE_LABEL_MAX = 80
COMPOSER_PROFILE_HIST_BINS_MAX = 25
COMPOSER_PROFILE_LIST_DEFAULT_MAX = 16
COMPOSER_PROFILE_SOURCE_PROJECTS_HARD_MAX = 64

# Reject playable / private / identity payloads at any profile nest level.
FORBIDDEN_PROFILE_KEYS: frozenset[str] = frozenset(
    {
        "tracks",
        "musicxml",
        "midi",
        "wav",
        "analysis",
        "analysis_report",
        "composition",
        "music",
        "events",
        "note_events",
        "embedding",
        "vector",
        "artist",
        "artist_id",
        "composer",
        "composer_id",
        "artist_name",
        "composer_name",
    }
)

COMPOSER_PROFILE_ERROR_CODES: dict[str, str] = {
    "composer_profile_not_found": "Composer profile id was not found.",
    "composer_profile_name_conflict": "A profile with this normalized name already exists.",
    "composer_profile_conflict": "CAS conflict: expected_updated_at does not match stored profile.",
    "composer_profile_invalid": "Profile document failed schema or business validation.",
    "composer_profile_derive_empty": "No usable source projects remained after derive.",
    "composer_profile_source_missing": "One or more source projects could not be loaded.",
    "composer_profile_forbidden_payload": "Profile payload contains forbidden playable or identity fields.",
    "composer_profile_import_invalid": "Import envelope failed validation.",
    "composer_profile_cap_exceeded": "A configured composer-profile cap was exceeded.",
}

ComposerProfileErrorCode = Literal[
    "composer_profile_not_found",
    "composer_profile_name_conflict",
    "composer_profile_conflict",
    "composer_profile_invalid",
    "composer_profile_derive_empty",
    "composer_profile_source_missing",
    "composer_profile_forbidden_payload",
    "composer_profile_import_invalid",
    "composer_profile_cap_exceeded",
]

_NAME_RE = re.compile(r"^[\w \-.'()+/]+$", re.UNICODE)
_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


class ComposerProfileError(Exception):
    """Domain error for composer profile operations mapped to structured HTTP."""

    def __init__(
        self,
        code: ComposerProfileErrorCode,
        message: str,
        *,
        http_status: int = 422,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        logger.error(
            "Composer profile domain error",
            extra={
                "error_code": code,
                "http_status": http_status,
                "detail_keys": sorted(self.details.keys()),
            },
        )


def _reject_forbidden_keys(data: Any, *, path: str = "") -> None:
    if isinstance(data, dict):
        forbidden = sorted(FORBIDDEN_PROFILE_KEYS.intersection(data.keys()))
        if forbidden:
            logger.debug(
                "Composer profile payload rejected",
                extra={
                    "code": "composer_profile_forbidden_payload",
                    "path": path or "<root>",
                    "forbidden_keys": forbidden,
                },
            )
            raise ValueError(
                "forbidden profile fields: " + ", ".join(forbidden)
            )
        for key, value in data.items():
            child = f"{path}.{key}" if path else str(key)
            _reject_forbidden_keys(value, path=child)
    elif isinstance(data, list):
        for index, item in enumerate(data):
            _reject_forbidden_keys(item, path=f"{path}[{index}]")


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def reject_forbidden_profile_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            _reject_forbidden_keys(data)
        return data


class IntervalHistogram(_StrictBase):
    """Coarse interval tendency bins (``-12..+12`` or collapsed)."""

    bins: list[float] = Field(default_factory=list, max_length=COMPOSER_PROFILE_HIST_BINS_MAX)

    @field_validator("bins")
    @classmethod
    def _finite_non_negative(cls, value: list[float]) -> list[float]:
        cleaned: list[float] = []
        for item in value:
            number = float(item)
            if number < 0 or number != number:  # NaN check
                raise ValueError("interval histogram bins must be finite and >= 0")
            cleaned.append(round(number, 6))
        return cleaned


class SectionTypeHistogram(_StrictBase):
    """Bounded section-type preference counts / weights."""

    counts: dict[str, float] = Field(default_factory=dict)

    @field_validator("counts")
    @classmethod
    def _bounded_counts(cls, value: dict[str, float]) -> dict[str, float]:
        if len(value) > COMPOSER_PROFILE_LIST_DEFAULT_MAX:
            raise ValueError("section type histogram exceeds max keys")
        out: dict[str, float] = {}
        for key, raw in value.items():
            label = str(key).strip()[:COMPOSER_PROFILE_LABEL_MAX]
            if not label:
                continue
            number = float(raw)
            if number < 0 or number != number:
                raise ValueError("section type weights must be finite and >= 0")
            out[label] = round(number, 6)
        return out


class PreferenceFields(_StrictBase):
    """Abstract preference families — bands / histograms / bounded label lists only."""

    # Melodic range
    midi_min_band: PreferenceBand | None = None
    midi_max_band: PreferenceBand | None = None
    midi_mean_band: PreferenceBand | None = None
    range_semitones_band: PreferenceBand | None = None

    # Interval tendencies
    interval_histogram: IntervalHistogram | None = None

    # Harmonic complexity / vocabulary / modulation
    chord_change_rate_band: PreferenceBand | None = None
    extension_density_band: PreferenceBand | None = None
    chord_vocabulary: list[str] = Field(default_factory=list, max_length=COMPOSER_PROFILE_LIST_DEFAULT_MAX)
    modulation_frequency_band: PreferenceBand | None = None

    # Rhythm / syncopation
    rhythmic_density_band: PreferenceBand | None = None
    syncopation_band: PreferenceBand | None = None

    # Instrumentation / arrangement
    preferred_instruments: list[str] = Field(
        default_factory=list, max_length=COMPOSER_PROFILE_LIST_DEFAULT_MAX
    )
    arrangement_density_band: PreferenceBand | None = None
    track_count_band: PreferenceBand | None = None

    # Repetition / motif / form / tension / dynamics
    repetition_amount_band: PreferenceBand | None = None
    motif_development_band: PreferenceBand | None = None
    preferred_forms: SectionTypeHistogram | None = None
    tension_shape: TensionShapeCode | None = None
    dynamics_band: PreferenceBand | None = None
    velocity_tendency_band: PreferenceBand | None = None

    @field_validator("chord_vocabulary", "preferred_instruments")
    @classmethod
    def _trim_labels(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            label = str(raw).strip()[:COMPOSER_PROFILE_LABEL_MAX]
            if not label:
                continue
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(label)
        return cleaned


class DerivedPreferenceFields(PreferenceFields):
    """Derived aggregates plus optional stats metadata (advisory until promoted)."""

    stats_meta: dict[str, Any] | None = None

    @field_validator("stats_meta")
    @classmethod
    def _bounded_stats_meta(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        _reject_forbidden_keys(value, path="stats_meta")
        # Cap nested size loosely via key count only (store enforces byte caps).
        if len(value) > 32:
            raise ValueError("stats_meta exceeds max keys")
        return value


class ComposerProfileSourceProject(_StrictBase):
    project_id: str = Field(..., min_length=1, max_length=COMPOSER_PROFILE_ID_MAX)
    revision_id: str | None = Field(default=None, max_length=COMPOSER_PROFILE_ID_MAX)
    fingerprint_prefix: str = Field(..., min_length=1, max_length=32)
    included_at: str = Field(..., min_length=1, max_length=40)

    @field_validator("project_id", "revision_id")
    @classmethod
    def _id_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not _ID_RE.match(cleaned):
            raise ValueError("invalid id shape")
        return cleaned


class ComposerProfileV1(_StrictBase):
    """Durable composer preference document."""

    schema_version: Literal["composer.profile.v1"] = COMPOSER_PROFILE_SCHEMA
    id: str = Field(..., min_length=1, max_length=COMPOSER_PROFILE_ID_MAX)
    name: str = Field(..., min_length=1, max_length=COMPOSER_PROFILE_NAME_MAX)
    created_at: str = Field(..., min_length=1, max_length=40)
    updated_at: str = Field(..., min_length=1, max_length=40)
    source_projects: list[ComposerProfileSourceProject] = Field(
        default_factory=list,
        max_length=COMPOSER_PROFILE_SOURCE_PROJECTS_HARD_MAX,
    )
    explicit: PreferenceFields = Field(default_factory=PreferenceFields)
    derived: DerivedPreferenceFields = Field(default_factory=DerivedPreferenceFields)
    notes: str | None = Field(default=None, max_length=COMPOSER_PROFILE_NOTES_MAX)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not _ID_RE.match(cleaned):
            raise ValueError("invalid profile id")
        return cleaned

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        cleaned = " ".join(value.strip().split())
        if not cleaned:
            raise ValueError("name must be non-empty")
        if len(cleaned) > COMPOSER_PROFILE_NAME_MAX:
            raise ValueError("name too long")
        if not _NAME_RE.match(cleaned):
            raise ValueError("name contains unsupported characters")
        return cleaned

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class ComposerProfileExportV1(_StrictBase):
    """Portable export envelope — profile document only (no compositions)."""

    schema_version: Literal["composer.profile.export.v1"] = COMPOSER_PROFILE_EXPORT_SCHEMA
    exported_at: str = Field(..., min_length=1, max_length=40)
    profile: ComposerProfileV1


class ComposerProfileListItem(_StrictBase):
    id: str
    name: str
    updated_at: str
    source_count: int = Field(..., ge=0)


class ComposerProfileCreateRequest(_StrictBase):
    name: str = Field(..., min_length=1, max_length=COMPOSER_PROFILE_NAME_MAX)
    notes: str | None = Field(default=None, max_length=COMPOSER_PROFILE_NOTES_MAX)
    explicit: PreferenceFields | None = None
    derived: DerivedPreferenceFields | None = None
    source_projects: list[ComposerProfileSourceProject] | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return ComposerProfileV1._validate_name(value)


class ComposerProfileUpdateRequest(_StrictBase):
    expected_updated_at: str = Field(..., min_length=1, max_length=40)
    name: str | None = Field(default=None, min_length=1, max_length=COMPOSER_PROFILE_NAME_MAX)
    notes: str | None = Field(default=None, max_length=COMPOSER_PROFILE_NOTES_MAX)
    explicit: PreferenceFields | None = None
    derived: DerivedPreferenceFields | None = None
    source_projects: list[ComposerProfileSourceProject] | None = None
    replace_body: bool = False

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ComposerProfileV1._validate_name(value)


class ComposerProfileResetRequest(_StrictBase):
    expected_updated_at: str = Field(..., min_length=1, max_length=40)
    clear_explicit: bool = True
    clear_derived: bool = False
    clear_sources: bool = False


class ComposerProfilePromoteRequest(_StrictBase):
    expected_updated_at: str = Field(..., min_length=1, max_length=40)
    # Empty / omitted → promote all derived preference keys that are set.
    fields: list[str] | None = None


class ComposerProfileDeriveSource(_StrictBase):
    project_id: str = Field(..., min_length=1, max_length=COMPOSER_PROFILE_ID_MAX)
    revision_id: str | None = Field(default=None, max_length=COMPOSER_PROFILE_ID_MAX)

    @field_validator("project_id", "revision_id")
    @classmethod
    def _id_shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not _ID_RE.match(cleaned):
            raise ValueError("invalid id shape")
        return cleaned


class ComposerProfileDeriveRequest(_StrictBase):
    sources: list[ComposerProfileDeriveSource] = Field(..., min_length=1)
    save_as: str | None = Field(default=None, max_length=COMPOSER_PROFILE_NAME_MAX)
    # When saving into an existing profile id (optional).
    target_profile_id: str | None = Field(default=None, max_length=COMPOSER_PROFILE_ID_MAX)
    expected_updated_at: str | None = Field(default=None, max_length=40)

    @field_validator("save_as")
    @classmethod
    def _validate_save_as(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ComposerProfileV1._validate_name(value)


class ComposerProfileDeriveResponse(_StrictBase):
    profile: ComposerProfileV1
    persisted: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=64)


class ComposerProfilePreviewRequest(_StrictBase):
    strength: ProfileStrength = "normal"


class ComposerProfilePreviewResponse(_StrictBase):
    profile_id: str
    strength: ProfileStrength
    applied_field_count: int = Field(..., ge=0)
    fragment_chars: int = Field(..., ge=0)
    soft_fragment: str = Field(..., max_length=8_192)
    tokenizer_labels: dict[str, str] = Field(default_factory=dict)
    resolved_keys: list[str] = Field(default_factory=list, max_length=64)


class ComposerProfileCompareRequest(_StrictBase):
    left_id: str | None = Field(default=None, max_length=COMPOSER_PROFILE_ID_MAX)
    right_id: str | None = Field(default=None, max_length=COMPOSER_PROFILE_ID_MAX)
    left: ComposerProfileV1 | None = None
    right: ComposerProfileV1 | None = None

    @model_validator(mode="after")
    def _require_pair(self) -> ComposerProfileCompareRequest:
        left_ok = self.left_id is not None or self.left is not None
        right_ok = self.right_id is not None or self.right is not None
        if not left_ok or not right_ok:
            raise ValueError("compare requires left and right profile id or body")
        return self


class ComposerProfileFieldDiff(_StrictBase):
    path: str
    left: Any = None
    right: Any = None


class ComposerProfileCompareResponse(_StrictBase):
    equal: bool
    diffs: list[ComposerProfileFieldDiff] = Field(default_factory=list, max_length=256)


class ComposerProfileImportRequest(_StrictBase):
    envelope: ComposerProfileExportV1
    # When set, replace existing profile (requires CAS).
    replace_profile_id: str | None = Field(default=None, max_length=COMPOSER_PROFILE_ID_MAX)
    expected_updated_at: str | None = Field(default=None, max_length=40)
    # Optional rename on import.
    name: str | None = Field(default=None, max_length=COMPOSER_PROFILE_NAME_MAX)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ComposerProfileV1._validate_name(value)


class ComposerProfileImportResponse(_StrictBase):
    profile: ComposerProfileV1
    warnings: list[str] = Field(default_factory=list, max_length=64)


# Preference field names eligible for promote (exclude stats_meta).
PROMOTABLE_PREFERENCE_KEYS: frozenset[str] = frozenset(
    {
        "midi_min_band",
        "midi_max_band",
        "midi_mean_band",
        "range_semitones_band",
        "interval_histogram",
        "chord_change_rate_band",
        "extension_density_band",
        "chord_vocabulary",
        "modulation_frequency_band",
        "rhythmic_density_band",
        "syncopation_band",
        "preferred_instruments",
        "arrangement_density_band",
        "track_count_band",
        "repetition_amount_band",
        "motif_development_band",
        "preferred_forms",
        "tension_shape",
        "dynamics_band",
        "velocity_tendency_band",
    }
)


def preference_fields_to_dict(fields: PreferenceFields) -> dict[str, Any]:
    """Serialize preference fields omitting empty defaults."""
    payload = fields.model_dump(exclude_none=True)
    # Drop empty collections so resolve/compare stay sparse.
    for key in ("chord_vocabulary", "preferred_instruments"):
        if key in payload and not payload[key]:
            del payload[key]
    if "interval_histogram" in payload:
        bins = payload["interval_histogram"].get("bins") if isinstance(
            payload["interval_histogram"], dict
        ) else None
        if not bins:
            del payload["interval_histogram"]
    if "preferred_forms" in payload:
        counts = payload["preferred_forms"].get("counts") if isinstance(
            payload["preferred_forms"], dict
        ) else None
        if not counts:
            del payload["preferred_forms"]
    if "stats_meta" in payload and not payload["stats_meta"]:
        del payload["stats_meta"]
    return payload


def empty_composer_profile(
    *,
    profile_id: str,
    name: str,
    created_at: str,
    updated_at: str | None = None,
) -> ComposerProfileV1:
    """Build an empty explicit/derived profile shell."""
    stamp = updated_at or created_at
    return ComposerProfileV1(
        id=profile_id,
        name=name,
        created_at=created_at,
        updated_at=stamp,
        source_projects=[],
        explicit=PreferenceFields(),
        derived=DerivedPreferenceFields(),
    )


def map_composer_profile_error_to_http(
    exc: ComposerProfileError,
) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    status = int(exc.http_status)
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.details:
        detail["details"] = exc.details
    return status, detail
