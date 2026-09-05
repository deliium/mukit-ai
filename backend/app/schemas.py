import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


logger = logging.getLogger(__name__)


SUPPORTED_SECTION_TYPES = {
    "intro",
    "verse",
    "pre_chorus",
    "chorus",
    "bridge",
    "solo",
    "breakdown",
    "outro",
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
}

KEY_PATTERN = re.compile(r"^[A-G](?:#|b)?\s+(?:major|minor)$")
TIME_SIGNATURE_PATTERN = re.compile(r"^\d{1,2}/\d{1,2}$")
NOTE_PITCH_PATTERN = re.compile(r"^[A-G](?:#|b)?\d$")
COMPOSITION_SCHEMA_VERSION = "composition.v1"
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


def _log_validation_failure(model_name: str, field_name: str, value: Any, reason: str) -> None:
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


def _midi_pitch_number(pitch: str) -> int:
    match = COMPOSITION_PITCH_PATTERN.match(pitch)
    if not match:
        raise ValueError("Pitch must use scientific notation like C4, F#3, or Bb2")

    note_name = f"{match.group(1)}{match.group(2)}"
    octave = int(match.group(3))
    midi_number = (octave + 1) * 12 + NOTE_TO_SEMITONE[note_name]
    if midi_number < 0 or midi_number > 127:
        raise ValueError("Pitch must be within MIDI range C-1 through G9")
    return midi_number


def _bar_duration_ticks(time_signature: str, ticks_per_quarter: int) -> int:
    numerator, denominator = (int(part) for part in time_signature.split("/"))
    numerator_ticks = numerator * 4 * ticks_per_quarter
    if numerator_ticks % denominator != 0:
        _log_validation_failure(
            "Composition",
            "time_signature",
            time_signature,
            "bar duration is not exactly representable as integer ticks",
        )
        raise ValueError("Time signature does not produce an integer tick duration")
    return numerator_ticks // denominator


class NoteEvent(BaseModel):
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
            _midi_pitch_number(pitch)
        except ValueError as exc:
            _log_validation_failure(cls.__name__, "pitch", value, str(exc))
            raise
        return pitch

    @field_validator("start_tick")
    @classmethod
    def validate_start_tick(cls, value: int) -> int:
        if value < 0:
            _log_validation_failure(cls.__name__, "start_tick", value, "start tick cannot be negative")
            raise ValueError("Start tick cannot be negative")
        return value

    @field_validator("duration_ticks")
    @classmethod
    def validate_duration_ticks(cls, value: int) -> int:
        if value <= 0:
            _log_validation_failure(cls.__name__, "duration_ticks", value, "duration must be positive")
            raise ValueError("Duration ticks must be positive")
        return value

    @field_validator("velocity")
    @classmethod
    def validate_velocity(cls, value: int) -> int:
        if value < 1 or value > 127:
            _log_validation_failure(cls.__name__, "velocity", value, "velocity must be in MIDI range 1-127")
            raise ValueError("Velocity must be in MIDI range 1-127")
        return value


class CompositionSection(BaseModel):
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
            _log_validation_failure(
                cls.__name__,
                "type",
                value,
                f"supported values: {sorted(SUPPORTED_SECTION_TYPES)}",
            )
            raise ValueError(f"Unsupported section type: {value}")
        return normalized


class CompositionTrack(BaseModel):
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
    events: list[NoteEvent] = Field(default_factory=list)

    @field_validator("id", "name", "instrument")
    @classmethod
    def validate_non_empty_track_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            _log_validation_failure(cls.__name__, "track_metadata", value, "track metadata cannot be empty")
            raise ValueError("Track metadata fields must not be empty")
        return normalized

    @field_validator("role")
    @classmethod
    def validate_composition_track_role(cls, value: str) -> str:
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

    @model_validator(mode="after")
    def validate_track_metadata(self) -> "CompositionTrack":
        if self.is_drum and self.channel != 10:
            logger.debug(
                "Drum track uses non-standard MIDI channel",
                extra={"track_id": self.id, "channel": self.channel},
            )
        return self


class Composition(BaseModel):
    schema_version: Literal["composition.v1"] = COMPOSITION_SCHEMA_VERSION
    tempo: int = Field(..., ge=40, le=240)
    key: str
    time_signature: str
    ticks_per_quarter: int = Field(default=480, gt=0)
    duration_ticks: int = Field(..., gt=0)
    bar_count: int = Field(..., ge=1)
    sections: list[CompositionSection] = Field(..., min_length=1)
    tracks: list[CompositionTrack] = Field(..., min_length=1)
    harmony: list["LLMMusicHarmonyItem"] = Field(default_factory=list)

    @field_validator("key")
    @classmethod
    def validate_composition_key(cls, value: str) -> str:
        key = " ".join(value.strip().split())
        if not KEY_PATTERN.match(key):
            _log_validation_failure(cls.__name__, "key", value, "expected format like 'C minor'")
            raise ValueError("Key must use format like 'C minor' or 'F# major'")
        return key

    @field_validator("time_signature")
    @classmethod
    def validate_composition_time_signature(cls, value: str) -> str:
        return LLMMusicJson.validate_time_signature(value)

    @model_validator(mode="after")
    def validate_composition_boundaries(self) -> "Composition":
        bar_ticks = _bar_duration_ticks(self.time_signature, self.ticks_per_quarter)
        expected_duration_ticks = self.bar_count * bar_ticks
        if self.duration_ticks != expected_duration_ticks:
            _log_validation_failure(
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
                _log_validation_failure(
                    self.__class__.__name__,
                    "sections",
                    section.model_dump(),
                    "sections must be contiguous and non-overlapping",
                )
                raise ValueError("Sections must be contiguous and non-overlapping")
            if section.duration_ticks != expected_section_ticks:
                _log_validation_failure(
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
            _log_validation_failure(
                self.__class__.__name__,
                "sections",
                {"section_bars": total_section_bars, "section_ticks": expected_start_tick},
                "sections must exactly cover composition duration",
            )
            raise ValueError("Sections must exactly cover the composition duration")

        track_ids = [track.id for track in self.tracks]
        duplicate_track_ids = sorted({track_id for track_id in track_ids if track_ids.count(track_id) > 1})
        if duplicate_track_ids:
            _log_validation_failure(
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
            _log_validation_failure(
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


class LLMMusicHarmonyItem(BaseModel):
    bar: int = Field(..., ge=1, le=512)
    chord: str = Field(..., min_length=1, max_length=32)

    @field_validator("chord")
    @classmethod
    def validate_chord(cls, value: str) -> str:
        chord = value.strip()
        if not chord:
            _log_validation_failure(cls.__name__, "chord", value, "chord is empty")
            raise ValueError("Chord must not be empty")
        return chord


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


class LLMModelSelection(BaseModel):
    provider: Literal["openai", "deepseek"] | None = None
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
    provider: Literal["openai", "deepseek"]
    model: str
    display_name: str
    is_default: bool = False


class LLMModelsResponse(BaseModel):
    models: list[LLMProviderModel]
    default_provider: Literal["openai", "deepseek"] | None = None
    default_model: str | None = None
    warnings: list[str] = Field(default_factory=list)


class LLMMusicGenerationResponse(BaseModel):
    music: LLMMusicJson
    provider: Literal["openai", "deepseek"]
    model: str
    musicxml: str | None = None
    musicxml_filename: str | None = None
    warnings: list[str] = Field(default_factory=list)


def _measure_quarter_length(time_signature: str) -> float:
    numerator, denominator = (int(part) for part in time_signature.split("/"))
    return numerator * (4 / denominator)
