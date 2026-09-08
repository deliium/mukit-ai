"""API and LLM request/response schemas.

Canonical composition document models live in ``composition_schemas``.
This module re-exports them for existing imports and defines LLM/edit contracts.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .composition_schemas import (
    COMPOSITION_SCHEMA_VERSION,
    COMPOSITION_SCHEMA_VERSION_V1,
    COMPOSITION_SCHEMA_VERSION_V2,
    Composition,
    CompositionDocument,
    CompositionHarmonyItem,
    CompositionSection,
    CompositionTrack,
    CompositionV1,
    CompositionV1NoteEvent,
    CompositionV1Section,
    CompositionV1Track,
    CompositionV2,
    CompositionV2AutomationLane,
    CompositionV2AutomationPoint,
    CompositionV2DynamicMark,
    CompositionV2HarmonyItem,
    CompositionV2KeyChange,
    CompositionV2Marker,
    CompositionV2NoteEvent,
    CompositionV2NoteTie,
    CompositionV2Section,
    CompositionV2SustainPedal,
    CompositionV2TempoChange,
    CompositionV2TimeSignatureChange,
    CompositionV2Track,
    KEY_PATTERN,
    LLMMusicHarmonyItem,
    NOTE_PITCH_PATTERN,
    NOTE_TO_SEMITONE,
    NoteEvent,
    SUPPORTED_SECTION_TYPES,
    SUPPORTED_TRACK_ROLES,
    TIME_SIGNATURE_PATTERN,
    UnsupportedSchemaVersionError,
    _bar_duration_ticks,
    _log_validation_failure,
    _midi_pitch_number,
    bar_duration_ticks,
    collect_ignored_v1_paths,
    compile_bar_boundaries,
    midi_pitch_number,
    normalize_key,
    normalize_time_signature,
    parse_composition_document,
)


logger = logging.getLogger(__name__)


class LLMMusicSection(BaseModel):
    type: str
    bars: int = Field(..., ge=1, le=128)

    @field_validator("type")
    @classmethod
    def validate_section_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_SECTION_TYPES:
            _log_validation_failure(
                cls.__name__,
                "type",
                value,
                f"supported values: {sorted(SUPPORTED_SECTION_TYPES)}",
            )
            raise ValueError(f"Unsupported section type: {value}")
        return normalized


class LLMMusicTrack(BaseModel):
    instrument: str = Field(..., min_length=1, max_length=80)
    role: str

    @field_validator("instrument")
    @classmethod
    def validate_instrument(cls, value: str) -> str:
        instrument = value.strip()
        if not instrument:
            _log_validation_failure(cls.__name__, "instrument", value, "instrument is empty")
            raise ValueError("Instrument must not be empty")
        return instrument

    @field_validator("role")
    @classmethod
    def validate_track_role(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            _log_validation_failure(
                cls.__name__,
                "role",
                value,
                f"supported values: {sorted(SUPPORTED_TRACK_ROLES)}",
            )
            raise ValueError(f"Unsupported track role: {value}")
        return normalized


class LLMMusicNoteItem(BaseModel):
    track: int = Field(..., ge=1, le=64)
    staff: Literal["treble", "bass"] = "treble"
    bar: int = Field(..., ge=1, le=512)
    beat: float = Field(default=1, ge=1)
    pitch: str = Field(..., min_length=2, max_length=5)
    duration: float = Field(..., gt=0, le=32)

    @field_validator("pitch")
    @classmethod
    def validate_pitch(cls, value: str) -> str:
        pitch = value.strip()
        if not NOTE_PITCH_PATTERN.match(pitch):
            _log_validation_failure(cls.__name__, "pitch", value, "expected scientific notation like C4 or F#3")
            raise ValueError("Pitch must use scientific notation like C4 or F#3")
        return pitch


class LLMMusicJson(BaseModel):
    tempo: int = Field(..., ge=40, le=240)
    key: str
    time_signature: str
    sections: list[LLMMusicSection] = Field(..., min_length=1)
    tracks: list[LLMMusicTrack] = Field(..., min_length=1)
    harmony: list[LLMMusicHarmonyItem] = Field(default_factory=list)
    notes: list[LLMMusicNoteItem] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            _log_validation_failure(cls.__name__, "key", value, "expected format like 'C minor'")
            raise ValueError("Key must use format like 'C minor' or 'F# major'")
        return key

    @field_validator("time_signature")
    @classmethod
    def validate_time_signature(cls, value: str) -> str:
        time_signature = value.strip()
        if not TIME_SIGNATURE_PATTERN.match(time_signature):
            _log_validation_failure(
                cls.__name__,
                "time_signature",
                value,
                "expected numeric meter like '4/4'",
            )
            raise ValueError("Time signature must use format like '4/4'")

        numerator, denominator = (int(part) for part in time_signature.split("/"))
        if numerator < 1 or numerator > 32 or denominator not in {1, 2, 4, 8, 16, 32}:
            _log_validation_failure(
                cls.__name__,
                "time_signature",
                value,
                "unsupported numerator or denominator",
            )
            raise ValueError("Time signature has unsupported numerator or denominator")
        return time_signature

    @model_validator(mode="after")
    def validate_harmony_bars(self) -> "LLMMusicJson":
        total_bars = sum(section.bars for section in self.sections)
        invalid_bars = [item.bar for item in self.harmony if item.bar > total_bars]
        if invalid_bars:
            _log_validation_failure(
                self.__class__.__name__,
                "harmony",
                invalid_bars,
                f"harmony bars exceed total section bars ({total_bars})",
            )
            raise ValueError("Harmony bars must fit within the total section length")

        measure_quarter_length = _measure_quarter_length(self.time_signature)
        invalid_notes = [item for item in self.notes if item.bar > total_bars or item.track > len(self.tracks)]
        if invalid_notes:
            _log_validation_failure(
                self.__class__.__name__,
                "notes",
                [item.model_dump() for item in invalid_notes[:5]],
                "notes reference a missing bar or track",
            )
            raise ValueError("Notes must reference existing bars and tracks")

        overflowing_notes = [
            item for item in self.notes if item.beat - 1 + item.duration > measure_quarter_length
        ]
        if overflowing_notes:
            _log_validation_failure(
                self.__class__.__name__,
                "notes",
                [item.model_dump() for item in overflowing_notes[:5]],
                "notes exceed the measure duration",
            )
            raise ValueError("Notes must fit within their measure")
        return self


class LLMPromptParameters(BaseModel):
    mood: str = Field(default="cinematic", min_length=1, max_length=120)
    genre: str = Field(default="ambient", min_length=1, max_length=120)
    tempo_min: int = Field(default=80, ge=40, le=240)
    tempo_max: int = Field(default=120, ge=40, le=240)
    key: str | None = None
    time_signature: str = "4/4"
    instruments: list[str] = Field(default_factory=lambda: ["piano"], min_length=1, max_length=16)
    sections: list[LLMMusicSection] = Field(default_factory=list)
    complexity: Literal["simple", "moderate", "complex"] = "moderate"
    duration_bars: int = Field(default=16, ge=1, le=512)
    instructions: str | None = Field(default=None, max_length=2000)

    @field_validator("key")
    @classmethod
    def validate_optional_key(cls, value: str | None) -> str | None:
        if value is None:
            return value
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            _log_validation_failure(cls.__name__, "key", value, "expected format like 'C minor'")
            raise ValueError("Key must use format like 'C minor' or 'F# major'")
        return key

    @field_validator("time_signature")
    @classmethod
    def validate_prompt_time_signature(cls, value: str) -> str:
        return LLMMusicJson.validate_time_signature(value)

    @field_validator("instruments")
    @classmethod
    def validate_instruments(cls, value: list[str]) -> list[str]:
        instruments = [instrument.strip() for instrument in value if instrument.strip()]
        if not instruments:
            _log_validation_failure(cls.__name__, "instruments", value, "no non-empty instruments")
            raise ValueError("At least one instrument is required")
        return instruments

    @model_validator(mode="after")
    def validate_tempo_range(self) -> "LLMPromptParameters":
        if self.tempo_min > self.tempo_max:
            _log_validation_failure(
                self.__class__.__name__,
                "tempo_range",
                {"tempo_min": self.tempo_min, "tempo_max": self.tempo_max},
                "tempo_min exceeds tempo_max",
            )
            raise ValueError("tempo_min must be less than or equal to tempo_max")
        return self

    @model_validator(mode="after")
    def validate_sections_match_duration(self) -> "LLMPromptParameters":
        if not self.sections:
            return self
        total_bars = sum(section.bars for section in self.sections)
        if total_bars != self.duration_bars:
            _log_validation_failure(
                self.__class__.__name__,
                "sections",
                {"section_bars": total_bars, "duration_bars": self.duration_bars},
                "explicit sections must sum to duration_bars",
            )
            raise ValueError(
                "Explicitly supplied sections must sum to duration_bars "
                f"(got {total_bars}, expected {self.duration_bars})"
            )
        return self


class LLMModelSelection(BaseModel):
    provider: Literal["openai", "deepseek", "fake"] | None = None
    model: str | None = Field(default=None, max_length=120)


class LLMGenerationOptions(BaseModel):
    temperature: float | None = Field(default=None, ge=0, le=2)
    timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    max_retries: int = Field(default=1, ge=0, le=5)


class LLMMusicGenerationRequest(BaseModel):
    prompt: LLMPromptParameters
    selection: LLMModelSelection = Field(default_factory=LLMModelSelection)
    options: LLMGenerationOptions = Field(default_factory=LLMGenerationOptions)


class LLMProviderModel(BaseModel):
    provider: Literal["openai", "deepseek", "fake"]
    model: str
    display_name: str
    is_default: bool = False


class LLMModelsResponse(BaseModel):
    models: list[LLMProviderModel]
    default_provider: Literal["openai", "deepseek", "fake"] | None = None
    default_model: str | None = None
    warnings: list[str] = Field(default_factory=list)


class LLMMusicGenerationResponse(BaseModel):
    music: CompositionV2
    provider: Literal["openai", "deepseek", "fake"]
    model: str
    musicxml: str | None = None
    musicxml_filename: str | None = None
    warnings: list[str] = Field(default_factory=list)
    validation: "GenerationValidationReport | None" = None


class GenerationValidationIssue(BaseModel):
    """Response-safe structured diagnostic for generation constraint checks."""

    code: str = Field(..., min_length=1, max_length=80)
    message: str = Field(..., min_length=1, max_length=500)
    severity: Literal["error", "warning"] = "error"
    expected: Any | None = None
    actual: Any | None = None
    stage: str | None = Field(default=None, max_length=80)
    track_id: str | None = Field(default=None, max_length=80)
    context: dict[str, Any] = Field(default_factory=dict)


class InstrumentSatisfactionEntry(BaseModel):
    """One requested sound-source requirement and its satisfaction state."""

    key: str = Field(..., min_length=1, max_length=80)
    identity: str = Field(..., min_length=1, max_length=80)
    family: str = Field(..., min_length=1, max_length=80)
    status: Literal["satisfied", "missing"] = "satisfied"
    track_ids: list[str] = Field(default_factory=list)
    raw_labels: list[str] = Field(default_factory=list)


class SuspiciousDuplicateGroupReport(BaseModel):
    """Suspicious same-identity/same-role track group with event evidence."""

    identity: str = Field(..., min_length=1, max_length=80)
    role: str = Field(..., min_length=1, max_length=80)
    track_ids: list[str] = Field(..., min_length=2)
    event_counts: list[int] = Field(default_factory=list)
    content_relationship: Literal["exact", "high_overlap", "distinct"]
    actionable: bool = False


class GenerationRepairAction(BaseModel):
    """Ordered repair action taken or requested during staged generation."""

    target: str = Field(..., min_length=1, max_length=80)
    attempt: int = Field(..., ge=0)
    diagnostic_codes: list[str] = Field(default_factory=list)
    affected_requirements: list[str] = Field(default_factory=list)
    affected_track_ids: list[str] = Field(default_factory=list)
    detail: str | None = Field(default=None, max_length=500)


class InstrumentationReport(BaseModel):
    """Deterministic instrumentation section of a generation validation report."""

    satisfied: list[InstrumentSatisfactionEntry] = Field(default_factory=list)
    missing: list[InstrumentSatisfactionEntry] = Field(default_factory=list)
    present_identities: list[str] = Field(default_factory=list)
    unexpected_identities: list[str] = Field(default_factory=list)
    suspicious_duplicates: list[SuspiciousDuplicateGroupReport] = Field(default_factory=list)


class GenerationValidationReport(BaseModel):
    """Structured generation constraint validation outcome."""

    status: Literal["passed", "failed", "repaired"] = "passed"
    constraints_checked: list[str] = Field(default_factory=list)
    errors: list[GenerationValidationIssue] = Field(default_factory=list)
    warnings: list[GenerationValidationIssue] = Field(default_factory=list)
    repair_attempts: int = Field(default=0, ge=0)
    tonality: dict[str, Any] | None = None
    instrumentation: InstrumentationReport | None = None
    repair_actions: list[GenerationRepairAction] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {"passed", "repaired"} and not self.errors



class CompositionEditSelection(BaseModel):
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)
    track_ids: list[str] | None = None
    section_id: str | None = Field(default=None, min_length=1, max_length=80)
    section_type: str | None = None

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            _log_validation_failure(cls.__name__, "section_id", value, "section_id cannot be empty")
            raise ValueError("section_id must not be empty when provided")
        return normalized

    @field_validator("section_type")
    @classmethod
    def validate_optional_section_type(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_SECTION_TYPES:
            _log_validation_failure(
                cls.__name__,
                "section_type",
                value,
                f"supported values: {sorted(SUPPORTED_SECTION_TYPES)}",
            )
            raise ValueError(f"Unsupported section type: {value}")
        return normalized

    @field_validator("track_ids")
    @classmethod
    def validate_track_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        normalized = [track_id.strip() for track_id in value if track_id and track_id.strip()]
        if not normalized:
            _log_validation_failure(cls.__name__, "track_ids", value, "track_ids cannot be empty when provided")
            raise ValueError("track_ids must contain at least one non-empty track id when provided")
        duplicates = sorted({track_id for track_id in normalized if normalized.count(track_id) > 1})
        if duplicates:
            _log_validation_failure(cls.__name__, "track_ids", duplicates, "duplicate track IDs are not allowed")
            raise ValueError("track_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_bar_range(self) -> "CompositionEditSelection":
        if self.end_bar < self.start_bar:
            _log_validation_failure(
                self.__class__.__name__,
                "bar_range",
                {"start_bar": self.start_bar, "end_bar": self.end_bar},
                "end_bar must be greater than or equal to start_bar",
            )
            raise ValueError("end_bar must be greater than or equal to start_bar")
        return self


class CompositionEditInstruction(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=2000)
    selection: CompositionEditSelection
    allow_harmony_changes: bool = False
    allow_added_tracks: bool = False

    @field_validator("instruction")
    @classmethod
    def validate_instruction(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            _log_validation_failure(cls.__name__, "instruction", value, "instruction cannot be empty")
            raise ValueError("instruction must not be empty")
        return normalized


class CompositionRegionTrackReplacement(BaseModel):
    track_id: str = Field(..., min_length=1, max_length=80)
    events: list[CompositionV2NoteEvent] = Field(default_factory=list)

    @field_validator("track_id")
    @classmethod
    def validate_track_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            _log_validation_failure(cls.__name__, "track_id", value, "track_id cannot be empty")
            raise ValueError("track_id must not be empty")
        return normalized

    @field_validator("events", mode="before")
    @classmethod
    def coerce_events_to_v2(cls, value: Any) -> Any:
        if value is None:
            return []
        coerced: list[Any] = []
        for item in value:
            if isinstance(item, CompositionV2NoteEvent):
                coerced.append(item)
                continue
            if hasattr(item, "model_dump"):
                data = item.model_dump(mode="json")
            elif isinstance(item, dict):
                data = dict(item)
            else:
                raise TypeError("replace_tracks events must be note objects or mappings")
            data.setdefault("articulations", [])
            data.setdefault("tie", None)
            coerced.append(data)
        return coerced


class CompositionRegionReplacementPatch(BaseModel):
    schema_version: Literal["composition.v1", "composition.v2"] = COMPOSITION_SCHEMA_VERSION_V2
    operation: Literal["replace_region"] = "replace_region"
    start_bar: int = Field(..., ge=1)
    end_bar: int = Field(..., ge=1)
    target_track_ids: list[str] | None = None
    replace_tracks: list[CompositionRegionTrackReplacement] = Field(default_factory=list)
    added_tracks: list[CompositionTrack | CompositionV2Track] = Field(default_factory=list)
    harmony_patch: list[LLMMusicHarmonyItem | CompositionV2HarmonyItem] | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("target_track_ids")
    @classmethod
    def validate_target_track_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        normalized = [track_id.strip() for track_id in value if track_id and track_id.strip()]
        if not normalized:
            _log_validation_failure(
                cls.__name__,
                "target_track_ids",
                value,
                "target_track_ids cannot be empty when provided",
            )
            raise ValueError("target_track_ids must contain at least one non-empty track id when provided")
        duplicates = sorted({track_id for track_id in normalized if normalized.count(track_id) > 1})
        if duplicates:
            _log_validation_failure(
                cls.__name__,
                "target_track_ids",
                duplicates,
                "duplicate target track IDs are not allowed",
            )
            raise ValueError("target_track_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_patch_shape(self) -> "CompositionRegionReplacementPatch":
        if self.end_bar < self.start_bar:
            _log_validation_failure(
                self.__class__.__name__,
                "bar_range",
                {"start_bar": self.start_bar, "end_bar": self.end_bar},
                "end_bar must be greater than or equal to start_bar",
            )
            raise ValueError("end_bar must be greater than or equal to start_bar")

        replace_ids = [track.track_id for track in self.replace_tracks]
        duplicate_replace_ids = sorted({track_id for track_id in replace_ids if replace_ids.count(track_id) > 1})
        if duplicate_replace_ids:
            _log_validation_failure(
                self.__class__.__name__,
                "replace_tracks",
                duplicate_replace_ids,
                "duplicate replace_tracks track IDs are not allowed",
            )
            raise ValueError("replace_tracks track IDs must be unique")

        if self.target_track_ids is not None:
            unknown_replace_ids = sorted(set(replace_ids) - set(self.target_track_ids))
            if unknown_replace_ids:
                _log_validation_failure(
                    self.__class__.__name__,
                    "replace_tracks",
                    unknown_replace_ids,
                    "replace_tracks must only target declared target_track_ids",
                )
                raise ValueError("replace_tracks must only include target_track_ids")

        added_ids = [track.id for track in self.added_tracks]
        duplicate_added_ids = sorted({track_id for track_id in added_ids if added_ids.count(track_id) > 1})
        if duplicate_added_ids:
            _log_validation_failure(
                self.__class__.__name__,
                "added_tracks",
                duplicate_added_ids,
                "duplicate added_tracks IDs are not allowed",
            )
            raise ValueError("added_tracks IDs must be unique")

        overlapping_ids = sorted(set(replace_ids) & set(added_ids))
        if overlapping_ids:
            _log_validation_failure(
                self.__class__.__name__,
                "added_tracks",
                overlapping_ids,
                "added_tracks cannot reuse replace_tracks IDs",
            )
            raise ValueError("added_tracks cannot reuse replace_tracks IDs")

        return self


class LLMCompositionEditRequest(BaseModel):
    composition: CompositionV1 | CompositionV2
    edit: CompositionEditInstruction
    selection: LLMModelSelection = Field(default_factory=LLMModelSelection)
    options: LLMGenerationOptions = Field(default_factory=LLMGenerationOptions)

    @field_validator("composition", mode="before")
    @classmethod
    def coerce_composition_document(cls, value: Any) -> CompositionV1 | CompositionV2:
        if isinstance(value, (CompositionV1, CompositionV2)):
            return value
        if isinstance(value, dict):
            schema_version = value.get("schema_version")
            if schema_version == COMPOSITION_SCHEMA_VERSION_V2:
                return CompositionV2.model_validate(value)
            if schema_version == COMPOSITION_SCHEMA_VERSION_V1 or schema_version is None:
                # Legacy unversioned payloads still enter the V1 parser for edit input;
                # services normalize to V2 before persistence/response.
                return CompositionV1.model_validate(value)
            raise UnsupportedSchemaVersionError(str(schema_version))
        raise TypeError("composition must be a mapping or Composition model")

    @model_validator(mode="after")
    def validate_edit_against_composition(self) -> "LLMCompositionEditRequest":
        edit_selection = self.edit.selection
        if edit_selection.end_bar > self.composition.bar_count:
            _log_validation_failure(
                self.__class__.__name__,
                "edit.selection.end_bar",
                edit_selection.end_bar,
                f"end_bar exceeds composition bar_count {self.composition.bar_count}",
            )
            raise ValueError("edit selection end_bar must be within composition bar_count")

        if edit_selection.track_ids is not None:
            known_track_ids = {track.id for track in self.composition.tracks}
            unknown = sorted(set(edit_selection.track_ids) - known_track_ids)
            if unknown:
                _log_validation_failure(
                    self.__class__.__name__,
                    "edit.selection.track_ids",
                    unknown,
                    "track_ids must exist in composition",
                )
                raise ValueError("edit selection track_ids must exist in the composition")

        if edit_selection.section_type is not None:
            matching = [
                section
                for section in self.composition.sections
                if section.type == edit_selection.section_type
            ]
            if not matching:
                _log_validation_failure(
                    self.__class__.__name__,
                    "edit.selection.section_type",
                    edit_selection.section_type,
                    "section_type not present in composition",
                )
                raise ValueError("edit selection section_type must exist in the composition")

        return self


class LLMCompositionEditResponse(BaseModel):
    composition: CompositionV2
    patch: CompositionRegionReplacementPatch
    provider: Literal["openai", "deepseek", "fake"]
    model: str
    musicxml: str | None = None
    musicxml_filename: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _measure_quarter_length(time_signature: str) -> float:
    numerator, denominator = (int(part) for part in time_signature.split("/"))
    return numerator * (4 / denominator)
