"""Strict Pydantic contracts for the Composition V2 tokenizer.

Locked REMI-style scheme
------------------------
* Time unit: grid steps from ``ticks_per_quarter / grid_subdivisions_per_quarter``.
* Onset: snap nearest grid (half-up / away from zero via ``round_half_away_from_zero``).
* Duration: snap nearest ≥ 1 grid step; clamp to ``max_dur_steps``.
* Pitch: MIDI 0–127; spelling may change on decode.
* Velocity: ``velocity_bins`` bins; decode restores bin centers clamped 1..127.
* Emission order: ``BOS`` + conditioning + track headers → per bar
  ``BAR`` [``METER``/``TEMPO``/``SECTION``] → ``POS`` → notes
  (``TRACK_SLOT``? ``PITCH`` ``VEL`` ``DUR``) → ``EOS``.
* Profile ``core`` omits articulations/ties/automation/motifs/harmony.
* Profile ``core_harmony`` adds coarse ``HARM_*`` context tokens only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.tokenizer.settings import TOKENIZER_VERSION, load_tokenizer_settings


logger = logging.getLogger(__name__)

TOKENIZER_CONFIG_SCHEMA = "tokenizer.config.v1"
TOKENIZER_VOCAB_SCHEMA = "tokenizer.vocab.v1"
TOKENIZER_SEQUENCE_SCHEMA = "tokenizer.sequence.v1"
TOKENIZER_ENCODE_REPORT_SCHEMA = "tokenizer.encode_report.v1"
TOKENIZER_DECODE_REPORT_SCHEMA = "tokenizer.decode_report.v1"
TOKENIZER_MANIFEST_SCHEMA = "tokenizer.manifest.v1"
TOKENIZER_STATS_SCHEMA = "tokenizer.stats.v1"
TOKENIZER_MODEL_EXPECTATION_SCHEMA = "tokenizer.model_expectation.v1"

TokenizerProfile = Literal["core", "core_harmony"]
TokenizerResultCode = Literal["ok", "repaired", "rejected"]
OnInvalidPolicy = Literal["repair", "reject"]

TokenizerIssueCode = Literal[
    "missing_bos",
    "missing_eos",
    "tokens_before_bos",
    "tokens_after_eos",
    "pad_stripped",
    "pos_out_of_range",
    "dur_out_of_range",
    "orphan_velocity",
    "orphan_duration",
    "unknown_track_slot",
    "unknown_token",
    "empty_note_payload",
    "conflicting_meter",
    "meter_not_at_bar",
    "harmony_disabled",
    "duplicate_bar",
    "garbage_rate_exceeded",
    "clamped_field",
    "invalid_pitch",
    "invalid_velocity",
    "snapped_onset",
    "snapped_duration",
    "omitted_expressive",
    "unknown_conditioning",
    "version_mismatch",
    "vocab_hash_mismatch",
    "unsupported_meter",
    "track_limit_exceeded",
    "ppq_mismatch",
]

COND_GENRES = (
    "classical",
    "jazz",
    "pop",
    "rock",
    "electronic",
    "folk",
    "blues",
    "soundtrack",
    "other",
)
COND_MOODS = (
    "happy",
    "sad",
    "tense",
    "calm",
    "energetic",
    "dark",
    "bright",
    "other",
)
COND_INSTSETS = (
    "piano_solo",
    "piano_trio",
    "string_quartet",
    "orchestra",
    "band",
    "electronic",
    "drums_bass",
    "mixed",
    "other",
)
HARM_CLASSES = (
    "maj",
    "min",
    "dim",
    "aug",
    "sus",
    "dom7",
    "maj7",
    "min7",
    "other",
)

# Practical closed meters: common numerators × supported denominators.
_METER_NUMERATORS = tuple(range(1, 17))
_METER_DENOMINATORS = (1, 2, 4, 8, 16, 32)


def supported_meter_strings() -> tuple[str, ...]:
    meters: list[str] = []
    for num in _METER_NUMERATORS:
        for den in _METER_DENOMINATORS:
            meters.append(f"{num}/{den}")
    return tuple(meters)


def supported_key_strings() -> tuple[str, ...]:
    roots = ("C", "C#", "Db", "D", "D#", "Eb", "E", "F", "F#", "Gb", "G", "G#", "Ab", "A", "A#", "Bb", "B")
    modes = ("major", "minor")
    return tuple(f"{root} {mode}" for root in roots for mode in modes)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TokenizerConfigV1(StrictModel):
    """``tokenizer.config.v1`` — quantization + feature flags."""

    schema_version: Literal["tokenizer.config.v1"] = TOKENIZER_CONFIG_SCHEMA
    tokenizer_version: str = TOKENIZER_VERSION
    profile: TokenizerProfile = "core"
    ticks_per_quarter: int = Field(default=480, ge=24, le=9600)
    grid_subdivisions_per_quarter: int = Field(default=4, ge=1, le=64)
    velocity_bins: int = Field(default=32, ge=1, le=127)
    max_dur_steps: int = Field(default=64, ge=1, le=512)
    max_tracks: int = Field(default=32, ge=1, le=128)
    max_vocab_size: int = Field(default=8192, ge=256, le=100_000)
    require_bos_eos: bool = True
    include_sections: bool = True
    include_tempo: bool = True
    include_meter: bool = True
    include_key_context: bool = True
    include_track_program: bool = True
    conditioning_enabled: bool = True
    emit_harmony: bool = False
    on_invalid: OnInvalidPolicy = "repair"
    max_garbage_rate: float = Field(default=0.25, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _align_profile_harmony(self) -> TokenizerConfigV1:
        if self.profile == "core_harmony":
            object.__setattr__(self, "emit_harmony", True)
        elif self.profile == "core" and self.emit_harmony:
            logger.debug(
                "Config validation: core profile forces emit_harmony=false",
                extra={"field_names": ["emit_harmony", "profile"]},
            )
            object.__setattr__(self, "emit_harmony", False)
        if self.ticks_per_quarter % self.grid_subdivisions_per_quarter != 0:
            logger.debug(
                "Config validation failed",
                extra={"field_names": ["ticks_per_quarter", "grid_subdivisions_per_quarter"]},
            )
            raise ValueError(
                "ticks_per_quarter must be divisible by grid_subdivisions_per_quarter"
            )
        return self

    @property
    def grid_ticks(self) -> int:
        return self.ticks_per_quarter // self.grid_subdivisions_per_quarter

    @property
    def max_bar_steps(self) -> int:
        """Longest supported meter (16/1) at current grid."""
        # 16/1 → 16 * 4 * ppq / 1 = 64 * ppq ticks
        longest_bar_ticks = 16 * 4 * self.ticks_per_quarter
        return longest_bar_ticks // self.grid_ticks

    def config_digest(self) -> str:
        payload = self.model_dump(mode="json")
        # Wall-clock excluded by construction (no created_at on config).
        material = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        logger.info(
            "Tokenizer config digest computed",
            extra={"config_digest_prefix": digest[:12], "profile": self.profile},
        )
        return digest


def default_tokenizer_config(env: dict[str, str] | None = None) -> TokenizerConfigV1:
    settings = load_tokenizer_settings(env)
    profile: TokenizerProfile = "core"
    if settings.default_profile in ("core", "core_harmony"):
        profile = settings.default_profile  # type: ignore[assignment]
    else:
        logger.warning(
            "Unknown TOKENIZER_PROFILE; defaults applied",
            extra={"default_profile": settings.default_profile, "fallback": "core"},
        )
    return TokenizerConfigV1(
        tokenizer_version=settings.tokenizer_version,
        profile=profile,
        ticks_per_quarter=settings.ticks_per_quarter,
        grid_subdivisions_per_quarter=settings.grid_subdivisions_per_quarter,
        velocity_bins=settings.velocity_bins,
        max_dur_steps=settings.max_dur_steps,
        max_tracks=settings.max_tracks,
        max_vocab_size=settings.max_vocab_size,
        require_bos_eos=settings.require_bos_eos,
        emit_harmony=profile == "core_harmony",
    )


class TokenizerConditioningV1(StrictModel):
    key: str | None = None
    genre: str | None = None
    mood: str | None = None
    instrument_set: str | None = None
    section_type: str | None = None


class TokenizerEncodeReportV1(StrictModel):
    schema_version: Literal["tokenizer.encode_report.v1"] = TOKENIZER_ENCODE_REPORT_SCHEMA
    tokenizer_version: str = TOKENIZER_VERSION
    profile: TokenizerProfile
    notes_in: int = 0
    notes_emitted: int = 0
    bars_emitted: int = 0
    tokens_emitted: int = 0
    snapped_onset: int = 0
    snapped_duration: int = 0
    dropped_invalid: int = 0
    omitted_expressive: int = 0
    unknown_conditioning: int = 0
    issue_codes: list[TokenizerIssueCode] = Field(default_factory=list)


class TokenizerDecodeReportV1(StrictModel):
    schema_version: Literal["tokenizer.decode_report.v1"] = TOKENIZER_DECODE_REPORT_SCHEMA
    tokenizer_version: str = TOKENIZER_VERSION
    profile: TokenizerProfile
    result: TokenizerResultCode = "ok"
    notes_out: int = 0
    bars_out: int = 0
    tokens_in: int = 0
    tokens_after_repair: int = 0
    issue_codes: list[TokenizerIssueCode] = Field(default_factory=list)
    conditioning: TokenizerConditioningV1 | None = None
    clamped_fields: list[str] = Field(default_factory=list)


class TokenizerSequenceV1(StrictModel):
    schema_version: Literal["tokenizer.sequence.v1"] = TOKENIZER_SEQUENCE_SCHEMA
    tokenizer_version: str = TOKENIZER_VERSION
    vocab_hash: str
    config_digest: str
    profile: TokenizerProfile
    token_ids: list[int] = Field(default_factory=list)
    token_strs: list[str] | None = None
    encode_report: TokenizerEncodeReportV1 | None = None
    decode_report: TokenizerDecodeReportV1 | None = None


class TokenizerVocabFamilyCounts(StrictModel):
    special: int = 0
    conditioning: int = 0
    structure: int = 0
    track: int = 0
    time: int = 0
    note: int = 0
    harmony: int = 0


class TokenizerVocabV1(StrictModel):
    schema_version: Literal["tokenizer.vocab.v1"] = TOKENIZER_VOCAB_SCHEMA
    tokenizer_version: str = TOKENIZER_VERSION
    vocab_hash: str
    token_to_id: dict[str, int]
    family_counts: TokenizerVocabFamilyCounts = Field(default_factory=TokenizerVocabFamilyCounts)
    special_token_ids: dict[str, int] = Field(default_factory=dict)
    size: int = 0

    @field_validator("token_to_id")
    @classmethod
    def _non_empty(cls, value: dict[str, int]) -> dict[str, int]:
        if not value:
            raise ValueError("token_to_id must not be empty")
        return value


class TokenizerManifestV1(StrictModel):
    schema_version: Literal["tokenizer.manifest.v1"] = TOKENIZER_MANIFEST_SCHEMA
    tokenizer_version: str
    vocab_hash: str
    config_digest: str
    special_token_ids: dict[str, int]
    grid_subdivisions_per_quarter: int
    velocity_bins: int
    profile: TokenizerProfile
    vocab_size: int
    created_at: datetime | None = None


class TokenizerModelExpectationV1(StrictModel):
    """Sidecar fields trainers must record for encode/decode compatibility."""

    schema_version: Literal["tokenizer.model_expectation.v1"] = TOKENIZER_MODEL_EXPECTATION_SCHEMA
    expected_tokenizer_version: str
    vocab_hash: str
    config_digest: str | None = None
    profile: TokenizerProfile | None = None


class TokenizerStatsV1(StrictModel):
    schema_version: Literal["tokenizer.stats.v1"] = TOKENIZER_STATS_SCHEMA
    tokenizer_version: str = TOKENIZER_VERSION
    profile: TokenizerProfile
    vocab_size: int
    sequences: int = 0
    avg_tokens_per_bar: float = 0.0
    max_sequence_length: int = 0
    unknown_or_invalid_event_rate: float = 0.0
    tokens_per_bar_histogram: dict[str, int] = Field(default_factory=dict)
    notes_per_bar_histogram: dict[str, int] = Field(default_factory=dict)
    snapped_onset_total: int = 0
    snapped_duration_total: int = 0
    dropped_invalid_total: int = 0
    omitted_expressive_total: int = 0


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
