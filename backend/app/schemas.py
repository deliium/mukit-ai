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
