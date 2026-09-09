"""Canonical composition.v1 / composition.v2 document contracts.

V1 remains an accepted migration input with ignored-extra compatibility.
V2 is the strict operational document (`extra=\"forbid\"` on persisted models).
Playable pitches live only in ``tracks[].events[]``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


logger = logging.getLogger(__name__)

COMPOSITION_SCHEMA_VERSION_V1 = "composition.v1"
COMPOSITION_SCHEMA_VERSION_V2 = "composition.v2"
COMPOSITION_SCHEMA_VERSION = COMPOSITION_SCHEMA_VERSION_V1

SUPPORTED_SECTION_TYPES = {
    "intro",
    "verse",
    "pre_chorus",
    "chorus",
    "bridge",
    "solo",
    "breakdown",
    "outro",
    # Neutral import fallback — generation prompts keep the authored form list.
    "unsectioned",
}

SUPPORTED_TRACK_ROLES = {
    "melody",
    "harmony",
    "bass",
    "drums",
    "percussion",
    "countermelody",
    "pad",
    "lead",
    "rhythm",
    # Neutral import fallback when source metadata is insufficient.
    "other",
}

KEY_PATTERN = re.compile(r"^[A-G](?:#|b)?\s+(?:major|minor)$")
TIME_SIGNATURE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}$")
NOTE_PITCH_PATTERN = re.compile(r"^[A-G](?:#|b)?\d$")
COMPOSITION_PITCH_PATTERN = re.compile(r"^([A-G])([#b]?)(-?\d+)$")

NOTE_TO_SEMITONE = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}

SUPPORTED_TIME_SIGNATURE_DENOMINATORS = {1, 2, 4, 8, 16, 32}

ARTICULATION_VALUES = ("staccato", "staccatissimo", "tenuto", "accent", "marcato")
ArticulationName = Literal["staccato", "staccatissimo", "tenuto", "accent", "marcato"]
TieType = Literal["start", "continue", "stop"]
MarkerKind = Literal["rehearsal", "text"]
DynamicLevel = Literal["ppp", "pp", "p", "mp", "mf", "f", "ff", "fff"]
AutomationParameter = Literal["volume", "pan", "expression"]
AutomationInterpolation = Literal["step", "linear"]

MOTIF_RELATIONSHIP_VALUES = (
    "original",
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
)
MotifRelationshipKind = Literal[
    "original",
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
MOTIF_MIN_EVENT_REFS = 3
MOTIF_MAX_EVENT_REFS = 32
MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED = "motif_occurrence_pruned"
MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED = "motif_definition_removed"

DYNAMIC_LEVEL_TO_EXPRESSION: dict[str, int] = {
    "ppp": 32,
    "pp": 48,
    "p": 64,
    "mp": 80,
    "mf": 96,
    "f": 112,
    "ff": 120,
    "fff": 127,
}

ARTICULATION_GATE_FACTOR: dict[str, float] = {
    "staccato": 0.50,
    "staccatissimo": 0.25,
    "tenuto": 1.00,
    "marcato": 0.75,
}

ARTICULATION_VELOCITY_DELTA: dict[str, int] = {
    "accent": 12,
    "marcato": 20,
}

GATE_SHORTENING_ARTICULATIONS = frozenset({"staccato", "staccatissimo", "marcato"})
ATTACK_ARTICULATIONS = frozenset({"accent", "marcato"})


class UnsupportedSchemaVersionError(ValueError):
    """Raised when an explicit schema_version is not composition.v1 or composition.v2."""

    code = "unsupported_schema_version"

    def __init__(self, schema_version: str | None):
        self.schema_version = schema_version
        super().__init__(f"Unsupported schema_version: {schema_version!r}")


def log_validation_failure(model_name: str, field_name: str, value: Any, reason: str) -> None:
    logger.debug(
        "Schema validation failure",
        extra={
            "model": model_name,
            "field": field_name,
            "value_type": type(value).__name__,
            "value_preview": str(value)[:80],
            "reason": reason,
        },
    )


# Back-compat private alias used by existing imports via schemas re-export.
_log_validation_failure = log_validation_failure


def midi_pitch_number(pitch: str) -> int:
    match = COMPOSITION_PITCH_PATTERN.match(pitch)
    if not match:
        raise ValueError("Pitch must use scientific notation like C4, F#3, or Bb2")

    note_name = f"{match.group(1)}{match.group(2)}"
    octave = int(match.group(3))
    midi_number = (octave + 1) * 12 + NOTE_TO_SEMITONE[note_name]
    if midi_number < 0 or midi_number > 127:
        raise ValueError("Pitch must be within MIDI range C-1 through G9")
    return midi_number


_midi_pitch_number = midi_pitch_number


def normalize_key(value: str, *, model_name: str) -> str:
    key = " ".join(value.strip().split())
    if not KEY_PATTERN.match(key):
        log_validation_failure(model_name, "key", value, "expected format like 'C minor'")
        raise ValueError("Key must use format like 'C minor' or 'F# major'")
    return key


def normalize_time_signature(value: str, *, model_name: str) -> str:
    time_signature = value.strip()
    if not TIME_SIGNATURE_PATTERN.match(time_signature):
        log_validation_failure(
            model_name,
            "time_signature",
            value,
            "expected numeric meter like '4/4'",
        )
        raise ValueError("Time signature must use format like '4/4'")

    numerator, denominator = (int(part) for part in time_signature.split("/"))
    if numerator < 1 or numerator > 32 or denominator not in SUPPORTED_TIME_SIGNATURE_DENOMINATORS:
        log_validation_failure(
            model_name,
            "time_signature",
            value,
            "unsupported numerator or denominator",
        )
        raise ValueError("Time signature has unsupported numerator or denominator")
    return time_signature


def bar_duration_ticks(time_signature: str, ticks_per_quarter: int) -> int:
    numerator, denominator = (int(part) for part in time_signature.split("/"))
    numerator_ticks = numerator * 4 * ticks_per_quarter
    if numerator_ticks % denominator != 0:
        log_validation_failure(
            "Composition",
            "time_signature",
            time_signature,
            "bar duration is not exactly representable as integer ticks",
        )
        raise ValueError("Time signature does not produce an integer tick duration")
    return numerator_ticks // denominator


_bar_duration_ticks = bar_duration_ticks


def compile_bar_boundaries(
    *,
    time_signature: str,
    ticks_per_quarter: int,
    bar_count: int,
    duration_ticks: int,
    time_signature_changes: list[Any],
) -> list[int]:
    """Return inclusive start ticks for bars ``1..bar_count`` plus the final end tick.

    Result length is ``bar_count + 1``. The last value must equal ``duration_ticks``.
    """
    change_by_tick: dict[int, str] = {}
    for change in time_signature_changes:
        if hasattr(change, "tick"):
            tick = int(change.tick)
            meter = str(change.time_signature)
        else:
            tick = int(change["tick"])
            meter = str(change["time_signature"])
        if tick in change_by_tick:
            raise ValueError("Duplicate time_signature_changes tick is not allowed")
        if tick <= 0 or tick >= duration_ticks:
            raise ValueError("time_signature_changes ticks must be in (0, duration_ticks)")
        change_by_tick[tick] = meter

    boundaries = [0]
    active_meter = time_signature
    for _bar_index in range(bar_count):
        start = boundaries[-1]
        if start in change_by_tick and start != 0:
            active_meter = change_by_tick[start]
        bar_ticks = bar_duration_ticks(active_meter, ticks_per_quarter)
        boundaries.append(start + bar_ticks)

    if boundaries[-1] != duration_ticks:
        raise ValueError("duration_ticks must end on a complete bar under the meter map")

    leftover_changes = sorted(tick for tick in change_by_tick if tick not in boundaries[:-1])
    if leftover_changes:
        raise ValueError("time_signature_changes must occur on derived bar boundaries")

    return boundaries


def round_half_away_from_zero(value: float) -> int:
    """Shared golden rounding: exact .5 rounds away from zero."""
    if value >= 0:
        return int(value + 0.5)
    return int(value - 0.5)


def articulation_gate_ticks(notated_duration_ticks: int, articulations: list[str] | tuple[str, ...]) -> int:
    factor = 1.0
    for name in articulations:
        if name in ARTICULATION_GATE_FACTOR:
            factor = min(factor, ARTICULATION_GATE_FACTOR[name])
    gated = round_half_away_from_zero(notated_duration_ticks * factor)
    return max(1, min(notated_duration_ticks, gated))


def articulation_velocity(base_velocity: int, articulations: list[str] | tuple[str, ...]) -> int:
    velocity = base_velocity
    for name in articulations:
        velocity += ARTICULATION_VELOCITY_DELTA.get(name, 0)
    return max(1, min(127, velocity))


def combined_expression(lane_value: int, dynamic_value: int) -> int:
    """Multiply expression lane by dynamic factor / 127 with shared half rounding."""
    return max(0, min(127, round_half_away_from_zero((lane_value * dynamic_value) / 127.0)))


class CompositionHarmonyItem(BaseModel):
    """Chord-symbol / analysis metadata; never invents audible notes."""

    model_config = ConfigDict(extra="ignore")

    bar: int = Field(..., ge=1, le=512)
    chord: str = Field(..., min_length=1, max_length=32)

    @field_validator("chord")
    @classmethod
    def validate_chord(cls, value: str) -> str:
        chord = value.strip()
        if not chord:
            log_validation_failure(cls.__name__, "chord", value, "chord is empty")
            raise ValueError("Chord must not be empty")
        return chord


class CompositionV2HarmonyItem(BaseModel):
    """V2 harmony metadata with strict extras policy."""

    model_config = ConfigDict(extra="forbid")

    bar: int = Field(..., ge=1, le=512)
    chord: str = Field(..., min_length=1, max_length=32)

    @field_validator("chord")
    @classmethod
    def validate_chord(cls, value: str) -> str:
        chord = value.strip()
        if not chord:
            log_validation_failure(cls.__name__, "chord", value, "chord is empty")
            raise ValueError("Chord must not be empty")
        return chord


# ---------------------------------------------------------------------------
# Composition V1 (migration input; ignored extras frozen)
# ---------------------------------------------------------------------------


class CompositionV1NoteEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["note"] = "note"
    pitch: str = Field(..., min_length=2, max_length=5)
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    velocity: int = Field(..., ge=1, le=127)
    id: str | None = Field(default=None, min_length=1, max_length=120)
    staff: Literal["treble", "bass"] | None = None
    voice: int | None = Field(default=None, ge=1, le=16)

    @field_validator("pitch")
    @classmethod
    def validate_event_pitch(cls, value: str) -> str:
        pitch = value.strip()
        try:
            midi_pitch_number(pitch)
        except ValueError as exc:
            log_validation_failure(cls.__name__, "pitch", value, str(exc))
            raise
        return pitch

    @field_validator("start_tick")
    @classmethod
    def validate_start_tick(cls, value: int) -> int:
        if value < 0:
            log_validation_failure(cls.__name__, "start_tick", value, "start tick cannot be negative")
            raise ValueError("Start tick cannot be negative")
        return value

    @field_validator("duration_ticks")
    @classmethod
    def validate_duration_ticks(cls, value: int) -> int:
        if value <= 0:
            log_validation_failure(cls.__name__, "duration_ticks", value, "duration must be positive")
            raise ValueError("Duration ticks must be positive")
        return value

    @field_validator("velocity")
    @classmethod
    def validate_velocity(cls, value: int) -> int:
        if value < 1 or value > 127:
            log_validation_failure(cls.__name__, "velocity", value, "velocity must be in MIDI range 1-127")
            raise ValueError("Velocity must be in MIDI range 1-127")
        return value


class CompositionV1Section(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1)
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)

    @field_validator("type")
    @classmethod
    def validate_composition_section_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_SECTION_TYPES:
            log_validation_failure(
                cls.__name__,
                "type",
                value,
                f"supported values: {sorted(SUPPORTED_SECTION_TYPES)}",
            )
            raise ValueError(f"Unsupported section type: {value}")
        return normalized


class CompositionV1Track(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    instrument: str = Field(..., min_length=1, max_length=80)
    role: str
    midi_program: int = Field(..., ge=0, le=127)
    channel: int = Field(..., ge=1, le=16)
    is_drum: bool = False
    volume: int = Field(default=100, ge=0, le=127)
    pan: int = Field(default=0, ge=-64, le=63)
    staff: Literal["treble", "bass", "grand"] | None = None
    events: list[CompositionV1NoteEvent] = Field(default_factory=list)

    @field_validator("id", "name", "instrument")
    @classmethod
    def validate_non_empty_track_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            log_validation_failure(cls.__name__, "track_metadata", value, "track metadata cannot be empty")
            raise ValueError("Track metadata fields must not be empty")
        return normalized

    @field_validator("role")
    @classmethod
    def validate_composition_track_role(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            log_validation_failure(
                cls.__name__,
                "role",
                value,
                f"supported values: {sorted(SUPPORTED_TRACK_ROLES)}",
            )
            raise ValueError(f"Unsupported track role: {value}")
        return normalized

    @model_validator(mode="after")
    def validate_track_metadata(self) -> CompositionV1Track:
        if self.is_drum and self.channel != 10:
            logger.debug(
                "Drum track uses non-standard MIDI channel",
                extra={"track_id": self.id, "channel": self.channel},
            )
        return self


class CompositionV1(BaseModel):
    """Accepted migration input. Extra fields are ignored (frozen V1 compatibility)."""

    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["composition.v1"] = COMPOSITION_SCHEMA_VERSION_V1
    tempo: int = Field(..., ge=40, le=240)
    key: str
    time_signature: str
    ticks_per_quarter: int = Field(default=480, gt=0)
    duration_ticks: int = Field(..., gt=0)
    bar_count: int = Field(..., ge=1)
    sections: list[CompositionV1Section] = Field(..., min_length=1)
    tracks: list[CompositionV1Track] = Field(..., min_length=1)
    harmony: list[CompositionHarmonyItem] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def validate_composition_key(cls, value: str) -> str:
        return normalize_key(value, model_name=cls.__name__)

    @field_validator("time_signature")
    @classmethod
    def validate_composition_time_signature(cls, value: str) -> str:
        return normalize_time_signature(value, model_name=cls.__name__)

    @model_validator(mode="after")
    def validate_composition_boundaries(self) -> CompositionV1:
        bar_ticks = bar_duration_ticks(self.time_signature, self.ticks_per_quarter)
        expected_duration_ticks = self.bar_count * bar_ticks
        if self.duration_ticks != expected_duration_ticks:
            log_validation_failure(
                self.__class__.__name__,
                "duration_ticks",
                self.duration_ticks,
                f"expected {expected_duration_ticks} from {self.bar_count} bars at {self.time_signature}",
            )
            raise ValueError("Composition duration_ticks must match bar_count and time_signature")

        expected_start_bar = 1
        expected_start_tick = 0
        total_section_bars = 0
        for section in self.sections:
            expected_section_ticks = section.bar_count * bar_ticks
            if section.start_bar != expected_start_bar or section.start_tick != expected_start_tick:
                log_validation_failure(
                    self.__class__.__name__,
                    "sections",
                    section.model_dump(),
                    "sections must be contiguous and non-overlapping",
                )
                raise ValueError("Sections must be contiguous and non-overlapping")
            if section.duration_ticks != expected_section_ticks:
                log_validation_failure(
                    self.__class__.__name__,
                    "sections",
                    section.model_dump(),
                    f"expected duration_ticks {expected_section_ticks}",
                )
                raise ValueError("Section duration_ticks must match bar_count and meter")
            expected_start_bar += section.bar_count
            expected_start_tick += section.duration_ticks
            total_section_bars += section.bar_count

        if total_section_bars != self.bar_count or expected_start_tick != self.duration_ticks:
            log_validation_failure(
                self.__class__.__name__,
                "sections",
                {"section_bars": total_section_bars, "section_ticks": expected_start_tick},
                "sections must exactly cover composition duration",
            )
            raise ValueError("Sections must exactly cover the composition duration")

        track_ids = [track.id for track in self.tracks]
        duplicate_track_ids = sorted({track_id for track_id in track_ids if track_ids.count(track_id) > 1})
        if duplicate_track_ids:
            log_validation_failure(
                self.__class__.__name__,
                "tracks",
                duplicate_track_ids,
                "duplicate track IDs are not allowed",
            )
            raise ValueError("Track IDs must be unique")

        event_count = 0
        invalid_events: list[dict[str, Any]] = []
        for track in self.tracks:
            for event in track.events:
                event_count += 1
                if event.start_tick + event.duration_ticks > self.duration_ticks:
                    invalid_events.append({"track_id": track.id, "event": event.model_dump()})
        if invalid_events:
            log_validation_failure(
                self.__class__.__name__,
                "tracks.events",
                invalid_events[:5],
                "events must fit within composition duration",
            )
            raise ValueError("Track events must fit within the composition duration")

        logger.info(
            "Composition validation completed",
            extra={
                "schema_version": self.schema_version,
                "bar_count": self.bar_count,
                "track_count": len(self.tracks),
                "event_count": event_count,
                "duration_ticks": self.duration_ticks,
            },
        )
        return self


# ---------------------------------------------------------------------------
# Composition V2 (strict operational document)
# ---------------------------------------------------------------------------


class CompositionV2TempoChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(..., gt=0)
    bpm: int = Field(..., ge=40, le=240)


class CompositionV2TimeSignatureChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(..., gt=0)
    time_signature: str

    @field_validator("time_signature")
    @classmethod
    def validate_time_signature(cls, value: str) -> str:
        return normalize_time_signature(value, model_name=cls.__name__)


class CompositionV2KeyChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(..., gt=0)
    key: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return normalize_key(value, model_name=cls.__name__)


class CompositionV2Marker(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=120)
    tick: int = Field(..., ge=0)
    kind: MarkerKind
    label: str = Field(..., min_length=1, max_length=200)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            log_validation_failure(cls.__name__, "label", value, "label cannot be empty")
            raise ValueError("Marker label must not be empty")
        return normalized


class CompositionV2NoteTie(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(..., min_length=1, max_length=120)
    type: TieType


class CompositionV2NoteEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["note"] = "note"
    pitch: str = Field(..., min_length=2, max_length=5)
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)
    velocity: int = Field(..., ge=1, le=127)
    id: str | None = Field(default=None, min_length=1, max_length=120)
    staff: Literal["treble", "bass"] | None = None
    voice: int | None = Field(default=None, ge=1, le=16)
    articulations: list[ArticulationName] = Field(default_factory=list)
    tie: CompositionV2NoteTie | None = None

    @field_validator("pitch")
    @classmethod
    def validate_event_pitch(cls, value: str) -> str:
        pitch = value.strip()
        try:
            midi_pitch_number(pitch)
        except ValueError as exc:
            log_validation_failure(cls.__name__, "pitch", value, str(exc))
            raise
        return pitch

    @field_validator("articulations")
    @classmethod
    def validate_articulations(cls, value: list[ArticulationName]) -> list[ArticulationName]:
        if len(value) != len(set(value)):
            log_validation_failure(cls.__name__, "articulations", value, "duplicate articulations")
            raise ValueError("articulations must be duplicate-free")
        names = set(value)
        if "staccato" in names and "staccatissimo" in names:
            raise ValueError("staccato and staccatissimo cannot combine")
        if ("staccato" in names or "staccatissimo" in names) and "tenuto" in names:
            raise ValueError("short articulations cannot combine with tenuto")
        if "accent" in names and "marcato" in names:
            raise ValueError("accent and marcato cannot combine")
        return value


class CompositionV2Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, min_length=1, max_length=120)
    type: str
    label: str | None = Field(default=None, min_length=1, max_length=200)
    start_bar: int = Field(..., ge=1)
    bar_count: int = Field(..., ge=1)
    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)

    @field_validator("type")
    @classmethod
    def validate_composition_section_type(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_SECTION_TYPES:
            log_validation_failure(
                cls.__name__,
                "type",
                value,
                f"supported values: {sorted(SUPPORTED_SECTION_TYPES)}",
            )
            raise ValueError(f"Unsupported section type: {value}")
        return normalized

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("Section label must not be empty when provided")
        return normalized


class CompositionV2DynamicMark(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(..., ge=0)
    level: DynamicLevel


class CompositionV2SustainPedal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_tick: int = Field(..., ge=0)
    duration_ticks: int = Field(..., gt=0)


class CompositionV2AutomationPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(..., gt=0)
    value: int


class CompositionV2AutomationLane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parameter: AutomationParameter
    interpolation: AutomationInterpolation
    points: list[CompositionV2AutomationPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_points(self) -> CompositionV2AutomationLane:
        previous_tick: int | None = None
        for point in self.points:
            if previous_tick is not None and point.tick <= previous_tick:
                raise ValueError("automation point ticks must be strictly increasing")
            previous_tick = point.tick
            if self.parameter in {"volume", "expression"}:
                if point.value < 0 or point.value > 127:
                    raise ValueError(f"{self.parameter} automation values must be in 0..127")
            elif point.value < -64 or point.value > 63:
                raise ValueError("pan automation values must be in -64..63")
        return self


class CompositionV2Track(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=80)
    name: str = Field(..., min_length=1, max_length=120)
    instrument: str = Field(..., min_length=1, max_length=80)
    role: str
    midi_program: int = Field(..., ge=0, le=127)
    channel: int = Field(..., ge=1, le=16)
    is_drum: bool = False
    volume: int = Field(default=100, ge=0, le=127)
    pan: int = Field(default=0, ge=-64, le=63)
    expression: int = Field(default=127, ge=0, le=127)
    staff: Literal["treble", "bass", "grand"] | None = None
    events: list[CompositionV2NoteEvent] = Field(default_factory=list)
    dynamic_marks: list[CompositionV2DynamicMark] = Field(default_factory=list)
    sustain_pedals: list[CompositionV2SustainPedal] = Field(default_factory=list)
    automation: list[CompositionV2AutomationLane] = Field(default_factory=list)

    @field_validator("id", "name", "instrument")
    @classmethod
    def validate_non_empty_track_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            log_validation_failure(cls.__name__, "track_metadata", value, "track metadata cannot be empty")
            raise ValueError("Track metadata fields must not be empty")
        return normalized

    @field_validator("role")
    @classmethod
    def validate_composition_track_role(cls, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        if normalized not in SUPPORTED_TRACK_ROLES:
            log_validation_failure(
                cls.__name__,
                "role",
                value,
                f"supported values: {sorted(SUPPORTED_TRACK_ROLES)}",
            )
            raise ValueError(f"Unsupported track role: {value}")
        return normalized

    @model_validator(mode="after")
    def validate_track_expression(self) -> CompositionV2Track:
        if self.is_drum and self.channel != 10:
            logger.debug(
                "Drum track uses non-standard MIDI channel",
                extra={"track_id": self.id, "channel": self.channel},
            )

        dynamic_ticks = [mark.tick for mark in self.dynamic_marks]
        if len(dynamic_ticks) != len(set(dynamic_ticks)):
            raise ValueError("dynamic_marks ticks must be unique within a track")
        if dynamic_ticks != sorted(dynamic_ticks):
            raise ValueError("dynamic_marks must be in ascending tick order")

        pedals = sorted(self.sustain_pedals, key=lambda pedal: pedal.start_tick)
        if [p.start_tick for p in pedals] != [p.start_tick for p in self.sustain_pedals]:
            raise ValueError("sustain_pedals must be in ascending start_tick order")
        previous_end: int | None = None
        for pedal in pedals:
            end = pedal.start_tick + pedal.duration_ticks
            if previous_end is not None and pedal.start_tick <= previous_end:
                raise ValueError("sustain_pedals must be non-overlapping (adjacent spans are rejected)")
            previous_end = end

        parameters = [lane.parameter for lane in self.automation]
        if len(parameters) != len(set(parameters)):
            raise ValueError("automation may have at most one lane per parameter")

        return self


def _validate_tie_chains(track: CompositionV2Track) -> None:
    groups: dict[str, list[CompositionV2NoteEvent]] = {}
    for event in track.events:
        if event.tie is None:
            continue
        groups.setdefault(event.tie.group_id, []).append(event)

    for group_id, members in groups.items():
        ordered = sorted(members, key=lambda event: (event.start_tick, event.duration_ticks))
        types = [event.tie.type for event in ordered if event.tie is not None]
        if types.count("start") != 1 or types.count("stop") != 1:
            raise ValueError(f"Tie group {group_id} must have exactly one start and one stop")
        if types[0] != "start" or types[-1] != "stop":
            raise ValueError(f"Tie group {group_id} must start with start and end with stop")
        if any(kind not in {"start", "continue", "stop"} for kind in types):
            raise ValueError(f"Tie group {group_id} has invalid tie types")
        if any(kind == "continue" for kind in (types[0], types[-1])):
            raise ValueError(f"Tie group {group_id} continue placement is invalid")

        head = ordered[0]
        for event in ordered:
            if event.pitch != head.pitch or event.staff != head.staff or event.voice != head.voice:
                raise ValueError(f"Tie group {group_id} members must share pitch/staff/voice")
            if event is not head and event.articulations:
                if any(name in GATE_SHORTENING_ARTICULATIONS for name in event.articulations):
                    raise ValueError("Gate-shortening articulations are not allowed on non-head tied notes")
                if any(name in ATTACK_ARTICULATIONS for name in event.articulations):
                    raise ValueError("Attack articulations are only allowed on the tie chain head")
            if event is head and any(name in GATE_SHORTENING_ARTICULATIONS for name in event.articulations):
                raise ValueError("Gate-shortening articulations are not allowed anywhere in a tie chain")

        for index in range(1, len(ordered)):
            previous = ordered[index - 1]
            current = ordered[index]
            previous_end = previous.start_tick + previous.duration_ticks
            if current.start_tick != previous_end:
                raise ValueError(f"Tie group {group_id} members must be contiguous without gap or overlap")


class CompositionV2MotifTransformProvenance(BaseModel):
    """Bounded authored transform metadata — never a playable note payload."""

    model_config = ConfigDict(extra="forbid")

    operation: MotifRelationshipKind
    variation_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    source_occurrence_id: str | None = Field(default=None, min_length=1, max_length=120)
    transpose_semitones: int | None = Field(default=None, ge=-48, le=48)
    inversion_axis_pitch: str | None = Field(default=None, min_length=2, max_length=5)
    time_scale_numerator: int | None = Field(default=None, ge=1, le=8)
    time_scale_denominator: int | None = Field(default=None, ge=1, le=8)
    sequence_steps: int | None = Field(default=None, ge=1, le=16)
    sequence_interval_semitones: int | None = Field(default=None, ge=-24, le=24)
    sequence_step_ticks: int | None = Field(default=None, gt=0)

    @field_validator("inversion_axis_pitch")
    @classmethod
    def validate_inversion_axis_pitch(cls, value: str | None) -> str | None:
        if value is None:
            return value
        pitch = value.strip()
        try:
            midi_pitch_number(pitch)
        except ValueError as exc:
            log_validation_failure(cls.__name__, "inversion_axis_pitch", value, str(exc))
            raise
        return pitch

    @model_validator(mode="after")
    def validate_provenance_bounds(self) -> CompositionV2MotifTransformProvenance:
        if self.operation == "original":
            raise ValueError("transform provenance must not use operation=original")
        if self.variation_strength is not None and (
            self.variation_strength != self.variation_strength  # NaN
            or self.variation_strength in (float("inf"), float("-inf"))
        ):
            raise ValueError("variation_strength must be a finite float in 0..1")
        has_num = self.time_scale_numerator is not None
        has_den = self.time_scale_denominator is not None
        if has_num != has_den:
            raise ValueError("time_scale_numerator and time_scale_denominator must be set together")
        return self


class CompositionV2MotifOccurrence(BaseModel):
    """Reference to existing canonical events — consumers derive span/notes from IDs."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=120)
    track_id: str = Field(..., min_length=1, max_length=80)
    event_ids: list[str] = Field(
        ...,
        min_length=MOTIF_MIN_EVENT_REFS,
        max_length=MOTIF_MAX_EVENT_REFS,
    )
    relationship: MotifRelationshipKind
    transform: CompositionV2MotifTransformProvenance | None = None

    @field_validator("id", "track_id")
    @classmethod
    def validate_non_empty_ids(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Motif occurrence ids must not be empty")
        return normalized

    @field_validator("event_ids")
    @classmethod
    def validate_event_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Motif occurrence event_ids must not be empty")
        normalized: list[str] = []
        for event_id in value:
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("Motif occurrence event_ids must be non-empty strings")
            normalized.append(event_id.strip())
        if len(normalized) != len(set(normalized)):
            raise ValueError("Motif occurrence event_ids must be duplicate-free")
        return normalized

    @model_validator(mode="after")
    def validate_relationship_transform(self) -> CompositionV2MotifOccurrence:
        if self.relationship == "original":
            if self.transform is not None:
                raise ValueError("original motif occurrence must not carry transform provenance")
        elif self.transform is not None and self.transform.operation != self.relationship:
            raise ValueError("transform.operation must match occurrence relationship")
        return self


class CompositionV2MotifDefinition(BaseModel):
    """Authored motif identity — playable pitches remain only in tracks[].events[]."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=120)
    label: str = Field(..., min_length=1, max_length=120)
    occurrences: list[CompositionV2MotifOccurrence] = Field(..., min_length=1)

    @field_validator("id", "label")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Motif id and label must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_one_original(self) -> CompositionV2MotifDefinition:
        originals = [occ for occ in self.occurrences if occ.relationship == "original"]
        if len(originals) != 1:
            raise ValueError("Each motif definition requires exactly one original occurrence")
        occurrence_ids = [occ.id for occ in self.occurrences]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("Motif occurrence ids must be unique within a definition")
        return self


class CompositionV2(BaseModel):
    """Strict latest operational composition document."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["composition.v2"] = COMPOSITION_SCHEMA_VERSION_V2
    tempo: int = Field(..., ge=40, le=240)
    key: str
    time_signature: str
    ticks_per_quarter: int = Field(default=480, gt=0)
    duration_ticks: int = Field(..., gt=0)
    bar_count: int = Field(..., ge=1)
    sections: list[CompositionV2Section] = Field(..., min_length=1)
    tracks: list[CompositionV2Track] = Field(..., min_length=1)
    harmony: list[CompositionV2HarmonyItem] = Field(default_factory=list)
    tempo_changes: list[CompositionV2TempoChange] = Field(default_factory=list)
    time_signature_changes: list[CompositionV2TimeSignatureChange] = Field(default_factory=list)
    key_changes: list[CompositionV2KeyChange] = Field(default_factory=list)
    markers: list[CompositionV2Marker] = Field(default_factory=list)
    motifs: list[CompositionV2MotifDefinition] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def validate_composition_key(cls, value: str) -> str:
        return normalize_key(value, model_name=cls.__name__)

    @field_validator("time_signature")
    @classmethod
    def validate_composition_time_signature(cls, value: str) -> str:
        return normalize_time_signature(value, model_name=cls.__name__)

    @model_validator(mode="after")
    def validate_composition_v2(self) -> CompositionV2:
        logger.debug(
            "Composition V2 schema dispatch",
            extra={
                "schema_version": self.schema_version,
                "tempo_change_count": len(self.tempo_changes),
                "meter_change_count": len(self.time_signature_changes),
                "key_change_count": len(self.key_changes),
                "marker_count": len(self.markers),
                "motif_count": len(self.motifs),
                "motif_occurrence_count": sum(len(motif.occurrences) for motif in self.motifs),
            },
        )

        try:
            boundaries = compile_bar_boundaries(
                time_signature=self.time_signature,
                ticks_per_quarter=self.ticks_per_quarter,
                bar_count=self.bar_count,
                duration_ticks=self.duration_ticks,
                time_signature_changes=self.time_signature_changes,
            )
        except ValueError as exc:
            log_validation_failure(self.__class__.__name__, "meter_map", self.duration_ticks, str(exc))
            raise

        boundary_set = set(boundaries[:-1])

        tempo_ticks = [change.tick for change in self.tempo_changes]
        if tempo_ticks != sorted(tempo_ticks):
            raise ValueError("tempo_changes must be in ascending tick order")
        if len(tempo_ticks) != len(set(tempo_ticks)):
            raise ValueError("tempo_changes ticks must be unique")
        for change in self.tempo_changes:
            if change.tick >= self.duration_ticks:
                raise ValueError("tempo_changes ticks must be in (0, duration_ticks)")

        meter_ticks = [change.tick for change in self.time_signature_changes]
        if meter_ticks != sorted(meter_ticks):
            raise ValueError("time_signature_changes must be in ascending tick order")
        for change in self.time_signature_changes:
            if change.tick not in boundary_set:
                raise ValueError("time_signature_changes must occur on derived bar boundaries")

        key_ticks = [change.tick for change in self.key_changes]
        if key_ticks != sorted(key_ticks):
            raise ValueError("key_changes must be in ascending tick order")
        if len(key_ticks) != len(set(key_ticks)):
            raise ValueError("key_changes ticks must be unique")
        for change in self.key_changes:
            if change.tick >= self.duration_ticks:
                raise ValueError("key_changes ticks must be in (0, duration_ticks)")
            if change.tick not in boundary_set:
                raise ValueError("key_changes must occur on derived bar boundaries")

        marker_ids = [marker.id for marker in self.markers if marker.id is not None]
        if len(marker_ids) != len(set(marker_ids)):
            raise ValueError("marker ids must be unique when present")
        marker_fingerprints = [(marker.tick, marker.kind, marker.label) for marker in self.markers]
        if len(marker_fingerprints) != len(set(marker_fingerprints)):
            raise ValueError("exact duplicate markers are not allowed")
        for marker in self.markers:
            if marker.tick > self.duration_ticks:
                raise ValueError("marker ticks must be within composition duration")

        expected_start_bar = 1
        expected_start_tick = 0
        total_section_bars = 0
        section_ids = [section.id for section in self.sections if section.id is not None]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section ids must be unique when present")

        for section in self.sections:
            end_bar = section.start_bar + section.bar_count - 1
            if section.start_bar != expected_start_bar:
                raise ValueError("Sections must be contiguous and non-overlapping")
            if section.start_bar < 1 or end_bar > self.bar_count:
                raise ValueError("Sections must fit within composition bar_count")
            start_tick = boundaries[section.start_bar - 1]
            end_tick = boundaries[end_bar]
            expected_section_ticks = end_tick - start_tick
            if section.start_tick != expected_start_tick or section.start_tick != start_tick:
                raise ValueError("Sections must be contiguous and non-overlapping")
            if section.duration_ticks != expected_section_ticks:
                raise ValueError("Section duration_ticks must match bar_count and meter map")
            expected_start_bar += section.bar_count
            expected_start_tick += section.duration_ticks
            total_section_bars += section.bar_count

        if total_section_bars != self.bar_count or expected_start_tick != self.duration_ticks:
            raise ValueError("Sections must exactly cover the composition duration")

        track_ids = [track.id for track in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("Track IDs must be unique")

        event_ids: list[str] = []
        event_count = 0
        for track in self.tracks:
            for mark in track.dynamic_marks:
                if mark.tick > self.duration_ticks:
                    raise ValueError("dynamic_marks must fit within composition duration")
            for pedal in track.sustain_pedals:
                if pedal.start_tick + pedal.duration_ticks > self.duration_ticks:
                    raise ValueError("sustain_pedals must fit within composition duration")
            for lane in track.automation:
                for point in lane.points:
                    if point.tick >= self.duration_ticks:
                        raise ValueError("automation points must be in (0, duration_ticks)")

            _validate_tie_chains(track)

            for event in track.events:
                event_count += 1
                if event.id is not None:
                    event_ids.append(event.id)
                if event.start_tick + event.duration_ticks > self.duration_ticks:
                    raise ValueError("Track events must fit within the composition duration")

        if len(event_ids) != len(set(event_ids)):
            raise ValueError("Note event ids must be unique when present")

        _validate_composition_motifs(self)

        logger.info(
            "Composition V2 validation completed",
            extra={
                "schema_version": self.schema_version,
                "bar_count": self.bar_count,
                "track_count": len(self.tracks),
                "event_count": event_count,
                "tempo_change_count": len(self.tempo_changes),
                "meter_change_count": len(self.time_signature_changes),
                "key_change_count": len(self.key_changes),
                "marker_count": len(self.markers),
                "motif_count": len(self.motifs),
                "motif_occurrence_count": sum(len(motif.occurrences) for motif in self.motifs),
                "duration_ticks": self.duration_ticks,
            },
        )
        return self


def _index_events_by_id(
    composition: CompositionV2,
) -> dict[str, tuple[CompositionV2Track, CompositionV2NoteEvent]]:
    indexed: dict[str, tuple[CompositionV2Track, CompositionV2NoteEvent]] = {}
    for track in composition.tracks:
        for event in track.events:
            if event.id is None:
                continue
            indexed[event.id] = (track, event)
    return indexed


def _tie_group_members(
    track: CompositionV2Track,
    event: CompositionV2NoteEvent,
) -> list[CompositionV2NoteEvent]:
    if event.tie is None:
        return [event]
    group_id = event.tie.group_id
    members = [item for item in track.events if item.tie is not None and item.tie.group_id == group_id]
    return sorted(members, key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""))


def derive_motif_occurrence_span(
    composition: CompositionV2,
    occurrence: CompositionV2MotifOccurrence,
) -> tuple[int, int]:
    """Derive [start_tick, end_tick) exclusively from referenced canonical events."""
    indexed = _index_events_by_id(composition)
    starts: list[int] = []
    ends: list[int] = []
    for event_id in occurrence.event_ids:
        track, event = indexed[event_id]
        if track.id != occurrence.track_id:
            raise ValueError(f"Event {event_id} is not on track {occurrence.track_id}")
        starts.append(event.start_tick)
        ends.append(event.start_tick + event.duration_ticks)
    return min(starts), max(ends)


def _validate_composition_motifs(composition: CompositionV2) -> None:
    if not composition.motifs:
        logger.debug(
            "Composition V2 motif validation skipped (empty)",
            extra={"motif_count": 0},
        )
        return

    motif_ids = [motif.id for motif in composition.motifs]
    if len(motif_ids) != len(set(motif_ids)):
        raise ValueError("Motif ids must be unique")
    motif_labels = [motif.label for motif in composition.motifs]
    if len(motif_labels) != len(set(motif_labels)):
        raise ValueError("Motif labels must be unique")

    occurrence_ids = [
        occurrence.id for motif in composition.motifs for occurrence in motif.occurrences
    ]
    if len(occurrence_ids) != len(set(occurrence_ids)):
        raise ValueError("Motif occurrence ids must be unique across the composition")

    tracks_by_id = {track.id: track for track in composition.tracks}
    indexed = _index_events_by_id(composition)

    for motif in composition.motifs:
        logger.debug(
            "Validating motif definition references",
            extra={
                "motif_id": motif.id,
                "occurrence_count": len(motif.occurrences),
            },
        )
        for occurrence in motif.occurrences:
            track = tracks_by_id.get(occurrence.track_id)
            if track is None:
                raise ValueError(f"Motif occurrence {occurrence.id} references unknown track")
            if track.is_drum or track.role in {"drums", "percussion"}:
                raise ValueError(
                    f"Motif occurrence {occurrence.id} must reference a non-percussion pitched track"
                )

            resolved: list[CompositionV2NoteEvent] = []
            for event_id in occurrence.event_ids:
                located = indexed.get(event_id)
                if located is None:
                    raise ValueError(
                        f"Motif occurrence {occurrence.id} references unresolved event id"
                    )
                event_track, event = located
                if event_track.id != occurrence.track_id:
                    raise ValueError(
                        f"Motif occurrence {occurrence.id} event {event_id} is not on track "
                        f"{occurrence.track_id}"
                    )
                resolved.append(event)

            ordered = sorted(
                resolved,
                key=lambda event: (event.start_tick, event.duration_ticks, event.id or ""),
            )
            ordered_ids = [event.id for event in ordered if event.id is not None]
            if ordered_ids != occurrence.event_ids:
                raise ValueError(
                    f"Motif occurrence {occurrence.id} event_ids must be chronological"
                )

            referenced_ids = set(occurrence.event_ids)
            for event in resolved:
                if event.tie is None:
                    continue
                members = _tie_group_members(track, event)
                member_ids = [member.id for member in members]
                if any(member_id is None for member_id in member_ids):
                    raise ValueError(
                        f"Motif occurrence {occurrence.id} references a tie chain with missing ids"
                    )
                if not set(member_ids).issubset(referenced_ids):
                    raise ValueError(
                        f"Motif occurrence {occurrence.id} must include complete tie chains"
                    )

    logger.debug(
        "Composition V2 motif validation completed",
        extra={
            "motif_count": len(composition.motifs),
            "motif_occurrence_count": len(occurrence_ids),
        },
    )


@dataclass(frozen=True)
class MotifReconcileWarning:
    code: str
    motif_id: str
    occurrence_id: str | None = None


@dataclass(frozen=True)
class MotifReconcileResult:
    motifs: list[CompositionV2MotifDefinition]
    warnings: tuple[MotifReconcileWarning, ...]


def reconcile_motifs_for_removed_event_ids(
    motifs: list[CompositionV2MotifDefinition],
    removed_event_ids: set[str] | frozenset[str],
) -> MotifReconcileResult:
    """Controlled reconciliation after explicit event deletion/replacement.

    Removes affected occurrences atomically. Deletes a definition only when its
    original occurrence is invalidated. Direct malformed JSON remains a schema error.
    """
    if not motifs or not removed_event_ids:
        return MotifReconcileResult(motifs=list(motifs), warnings=())

    logger.debug(
        "Reconciling motif references after event removal",
        extra={
            "motif_count": len(motifs),
            "removed_event_id_count": len(removed_event_ids),
        },
    )

    next_motifs: list[CompositionV2MotifDefinition] = []
    warnings: list[MotifReconcileWarning] = []

    for motif in motifs:
        surviving: list[CompositionV2MotifOccurrence] = []
        original_removed = False
        for occurrence in motif.occurrences:
            if removed_event_ids.intersection(occurrence.event_ids):
                warnings.append(
                    MotifReconcileWarning(
                        code=MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
                        motif_id=motif.id,
                        occurrence_id=occurrence.id,
                    )
                )
                logger.warning(
                    "Pruned motif occurrence after event removal",
                    extra={
                        "code": MOTIF_RECONCILE_WARNING_OCCURRENCE_PRUNED,
                        "motif_id": motif.id,
                        "occurrence_id": occurrence.id,
                    },
                )
                if occurrence.relationship == "original":
                    original_removed = True
                continue
            surviving.append(occurrence)

        if original_removed or not surviving:
            warnings.append(
                MotifReconcileWarning(
                    code=MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
                    motif_id=motif.id,
                )
            )
            logger.warning(
                "Removed motif definition after original occurrence invalidation",
                extra={
                    "code": MOTIF_RECONCILE_WARNING_DEFINITION_REMOVED,
                    "motif_id": motif.id,
                },
            )
            continue

        next_motifs.append(motif.model_copy(update={"occurrences": surviving}))

    logger.info(
        "Motif reference reconciliation completed",
        extra={
            "motif_count_before": len(motifs),
            "motif_count_after": len(next_motifs),
            "warning_count": len(warnings),
            "warning_codes": [item.code for item in warnings],
        },
    )
    return MotifReconcileResult(motifs=next_motifs, warnings=tuple(warnings))


CompositionDocument = Annotated[
    CompositionV1 | CompositionV2,
    Field(discriminator="schema_version"),
]


def collect_ignored_v1_paths(raw: dict[str, Any]) -> list[str]:
    """Return sanitized dotted paths for V1 extras that validation ignores."""
    if not isinstance(raw, dict):
        return []

    ignored: list[str] = []
    known_root = {
        "schema_version",
        "tempo",
        "key",
        "time_signature",
        "ticks_per_quarter",
        "duration_ticks",
        "bar_count",
        "sections",
        "tracks",
        "harmony",
    }
    for key in raw:
        if key not in known_root:
            ignored.append(key)

    known_section = {"type", "start_bar", "bar_count", "start_tick", "duration_ticks"}
    for index, section in enumerate(raw.get("sections") or []):
        if not isinstance(section, dict):
            continue
        for key in section:
            if key not in known_section:
                ignored.append(f"sections[{index}].{key}")

    known_track = {
        "id",
        "name",
        "instrument",
        "role",
        "midi_program",
        "channel",
        "is_drum",
        "volume",
        "pan",
        "staff",
        "events",
    }
    known_event = {
        "type",
        "pitch",
        "start_tick",
        "duration_ticks",
        "velocity",
        "id",
        "staff",
        "voice",
    }
    for track_index, track in enumerate(raw.get("tracks") or []):
        if not isinstance(track, dict):
            continue
        for key in track:
            if key not in known_track:
                ignored.append(f"tracks[{track_index}].{key}")
        for event_index, event in enumerate(track.get("events") or []):
            if not isinstance(event, dict):
                continue
            for key in event:
                if key not in known_event:
                    ignored.append(f"tracks[{track_index}].events[{event_index}].{key}")

    return ignored


def parse_composition_document(raw: Any) -> CompositionV1 | CompositionV2:
    """Dispatch by exact schema_version. Unknown explicit versions never enter legacy migration."""
    if isinstance(raw, (CompositionV1, CompositionV2)):
        logger.debug(
            "Composition schema dispatch identity",
            extra={"schema_version": raw.schema_version},
        )
        return raw

    if not isinstance(raw, dict):
        raise TypeError("Composition document must be a mapping or Composition model")

    schema_version = raw.get("schema_version")
    logger.debug(
        "Composition schema dispatch",
        extra={"schema_version": schema_version},
    )
    if schema_version == COMPOSITION_SCHEMA_VERSION_V2:
        return CompositionV2.model_validate(raw)
    if schema_version == COMPOSITION_SCHEMA_VERSION_V1:
        return CompositionV1.model_validate(raw)
    if schema_version is None:
        # Unversioned legacy is handled by the normalizer, not here.
        raise UnsupportedSchemaVersionError(schema_version)
    raise UnsupportedSchemaVersionError(str(schema_version))


# Compatibility aliases — existing services still import V1 under historical names.
NoteEvent = CompositionV1NoteEvent
CompositionSection = CompositionV1Section
CompositionTrack = CompositionV1Track
Composition = CompositionV1
LLMMusicHarmonyItem = CompositionHarmonyItem
