"""Non-playable ``asset.pack.*.v1`` contracts for soundtrack / production packs.

Asset packs own plan revisions and slot→project maps. Playable notes stay on
each asset project's ``composition.v2``. Never invent ``composition.v5``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import KEY_PATTERN
from app.mix_plan_schemas import MixPlanMasterTargetId
from app.musical_universe_schemas import MECHANICAL_OPERATIONS, MechanicalOperation

logger = logging.getLogger(__name__)

ASSET_PACK_BRIEF_SCHEMA: Literal["asset.pack.brief.v1"] = "asset.pack.brief.v1"
ASSET_PACK_PLAN_SCHEMA: Literal["asset.pack.plan.v1"] = "asset.pack.plan.v1"
ASSET_PACK_PRODUCTION_SCHEMA: Literal["asset.pack.production.v1"] = "asset.pack.production.v1"
ASSET_PACK_SCHEMA: Literal["asset.pack.v1"] = "asset.pack.v1"

PACK_ID_PREFIX = "apack_"
PACK_ID_RE = re.compile(r"^apack_[0-9a-f]{16}$")
SLOT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ASSET_PACK_MAX_SLOTS = 16
ASSET_PACK_TITLE_MAX = 120
ASSET_PACK_LABEL_MAX = 64
ASSET_PACK_NARRATIVE_MAX = 240
ASSET_PACK_MOTIF_LABEL_MAX = 64
ASSET_PACK_INSTRUMENT_MAX = 8
SEED_DURATION_SECONDS_DEFAULT = 90
NON_SEED_DURATION_SECONDS_DEFAULT = 60
SLOT_DURATION_SECONDS_MAX = 120
SLOT_DURATION_SECONDS_MIN = 30

PROFILE_STRENGTHS: tuple[str, ...] = ("off", "light", "normal", "strong")
ProfileStrength = Literal["off", "light", "normal", "strong"]

DensityBand = Literal["sparse", "moderate", "dense"]
AssetPackPresetId = Literal["game_soundtrack_v1"]
AssetPackStatus = Literal[
    "planned",
    "generating",
    "completed",
    "partial",
    "failed",
]
AssetPackSlotStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "skipped",
]

FORBIDDEN_PLAYABLE_TOP_LEVEL: frozenset[str] = frozenset(
    {
        "tracks",
        "events",
        "notes",
        "note_events",
        "musicxml",
        "midi",
        "wav",
        "composition",
        "harmony",
        "analysis",
    }
)

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

GAME_SOUNDTRACK_V1_SLOTS: tuple[dict[str, str], ...] = (
    {"slot_id": "main_theme", "label": "Main Theme", "adaptive_label": "main_theme", "density": "dense"},
    {"slot_id": "menu", "label": "Menu", "adaptive_label": "menu", "density": "sparse"},
    {"slot_id": "explore_forest", "label": "Exploration Forest", "adaptive_label": "explore_forest", "density": "moderate"},
    {"slot_id": "explore_city", "label": "Exploration City", "adaptive_label": "explore_city", "density": "moderate"},
    {"slot_id": "combat_low", "label": "Combat Low", "adaptive_label": "combat_low", "density": "moderate"},
    {"slot_id": "combat_high", "label": "Combat High", "adaptive_label": "combat_high", "density": "dense"},
    {"slot_id": "boss", "label": "Boss", "adaptive_label": "boss", "density": "dense"},
    {"slot_id": "victory", "label": "Victory", "adaptive_label": "victory", "density": "moderate"},
    {"slot_id": "defeat", "label": "Defeat", "adaptive_label": "defeat", "density": "sparse"},
    {"slot_id": "credits", "label": "Credits", "adaptive_label": "credits", "density": "moderate"},
)

# Locked default theme_policy.propagate for game_soundtrack_v1 (non-seed slots).
GAME_SOUNDTRACK_V1_PROPAGATE: dict[str, dict[str, Any]] = {
    "menu": {"operation": "repeat"},
    "explore_forest": {"operation": "transpose", "transpose_semitones": 2},
    "explore_city": {"operation": "transpose", "transpose_semitones": 5},
    "combat_low": {"operation": "transpose", "transpose_semitones": 3},
    "combat_high": {"operation": "transpose", "transpose_semitones": 7},
    "boss": {"operation": "transpose", "transpose_semitones": 12},
    "victory": {"operation": "transpose", "transpose_semitones": 4},
    "defeat": {"operation": "transpose", "transpose_semitones": -5},
    "credits": {"operation": "repeat"},
}

ASSET_PACK_ERROR_MESSAGES: dict[str, str] = {
    "asset_pack_invalid": "Asset pack document failed validation.",
    "asset_pack_conflict": "expected_revision does not match the stored pack.",
    "asset_pack_not_found": "Asset pack id was not found.",
    "asset_pack_slot_unknown": "One or more slot_ids are not on this pack plan.",
    "asset_pack_plan_mismatch": "expected_plan_digest does not match the stored plan.",
    "asset_pack_generate_failed": "Asset pack generate failed for one or more slots.",
    "asset_pack_seed_motif_missing": "Seed slot has no motif matching motif_label with an original occurrence.",
    "asset_pack_embedded_notes": "Asset pack documents cannot embed note events.",
    "asset_pack_profile_unknown": "composer_profile_id was not found.",
    "asset_pack_slot_limit": "Asset pack exceeds the maximum slot count.",
}

_HTTP_STATUS: dict[str, int] = {
    "asset_pack_not_found": 404,
    "asset_pack_conflict": 409,
    "asset_pack_plan_mismatch": 409,
    "asset_pack_seed_motif_missing": 409,
    "asset_pack_generate_failed": 503,
    "asset_pack_profile_unknown": 404,
    "asset_pack_slot_unknown": 422,
    "asset_pack_slot_limit": 422,
    "asset_pack_embedded_notes": 422,
    "asset_pack_invalid": 422,
}

AssetPackErrorCode = Literal[
    "asset_pack_invalid",
    "asset_pack_conflict",
    "asset_pack_not_found",
    "asset_pack_slot_unknown",
    "asset_pack_plan_mismatch",
    "asset_pack_generate_failed",
    "asset_pack_seed_motif_missing",
    "asset_pack_embedded_notes",
    "asset_pack_profile_unknown",
    "asset_pack_slot_limit",
]


class AssetPackError(Exception):
    """Domain error mapped to HTTP by the asset-pack router."""

    def __init__(self, code: str, message: str | None = None) -> None:
        self.code = code
        self.message = message or ASSET_PACK_ERROR_MESSAGES.get(code, code)
        self.http_status = _HTTP_STATUS.get(code, 422)
        super().__init__(self.message)


def log_asset_pack_schema_rejection(*, model: str, code: str, field_path: str | None = None) -> None:
    """DEBUG a schema rejection. Never include full brief/plan bodies."""
    logger.debug(
        "asset pack model rejected",
        extra={
            "model": model,
            "code": code,
            "field_path": field_path or "",
        },
    )


def _reject(model: str, code: str = "asset_pack_invalid", field_path: str | None = None) -> NoReturn:
    log_asset_pack_schema_rejection(model=model, code=code, field_path=field_path)
    raise AssetPackError(code)


def find_forbidden_note_keys(value: Any, *, path: str = "") -> list[str]:
    """Return dotted paths where forbidden note keys appear."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if key_text in FORBIDDEN_NOTE_KEYS:
                found.append(child_path)
            found.extend(find_forbidden_note_keys(child, path=child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_forbidden_note_keys(child, path=f"{path}[{index}]"))
    return found


def reject_embedded_note_keys(data: Any, *, model: str) -> None:
    """Raise when pack bodies embed forbidden note material anywhere."""
    if not isinstance(data, dict):
        return
    paths = find_forbidden_note_keys(data)
    if paths:
        log_asset_pack_schema_rejection(
            model=model,
            code="asset_pack_embedded_notes",
            field_path=paths[0],
        )
        raise AssetPackError("asset_pack_embedded_notes")


def reject_playable_top_level(data: Any, *, model: str) -> None:
    """Refuse playable score keys at the document root."""
    if not isinstance(data, dict):
        return
    forbidden = sorted(FORBIDDEN_PLAYABLE_TOP_LEVEL.intersection(data.keys()))
    if forbidden:
        log_asset_pack_schema_rejection(
            model=model,
            code="asset_pack_invalid",
            field_path=forbidden[0],
        )
        raise AssetPackError("asset_pack_invalid")


def _normalize_key_label(value: str) -> str:
    key = " ".join(value.strip().split())
    if not KEY_PATTERN.match(key):
        raise ValueError("Key must use format like 'C minor' or 'F# major'")
    return key


def _strip_text(value: Any) -> Any:
    if isinstance(value, str):
        stripped = " ".join(value.strip().split())
        return stripped or None
    return value


class AssetPackPropagateOp(BaseModel):
    """Mechanical theme reuse parameters for one non-seed slot."""

    model_config = ConfigDict(extra="forbid")

    operation: MechanicalOperation
    transpose_semitones: int | None = Field(default=None, ge=-24, le=24)
    destination_start_bar: int = Field(default=1, ge=1, le=512)

    @model_validator(mode="after")
    def parameters_match_operation(self) -> AssetPackPropagateOp:
        if self.operation == "transpose":
            if self.transpose_semitones is None:
                _reject(self.__class__.__name__, field_path="transpose_semitones")
        elif self.transpose_semitones is not None:
            _reject(self.__class__.__name__, field_path="transpose_semitones")
        if self.operation not in MECHANICAL_OPERATIONS:
            _reject(self.__class__.__name__, field_path="operation")
        return self


class AssetPackProductionV1(BaseModel):
    """Shared loudness / master target / palette / neural policy. Never PCM."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["asset.pack.production.v1"] = ASSET_PACK_PRODUCTION_SCHEMA
    master_target: MixPlanMasterTargetId = "cinematic"
    loudness_goal_lufs: float | None = Field(default=None, ge=-60.0, le=0.0)
    guarantee: Literal[False] = False
    catalog_instrument_ids: list[str] = Field(..., min_length=1, max_length=ASSET_PACK_INSTRUMENT_MAX)
    neural_adapter_kind: str | None = Field(default=None, min_length=1, max_length=64)
    neural_fidelity_class: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="before")
    @classmethod
    def reject_notes_and_playable(cls, data: Any) -> Any:
        reject_playable_top_level(data, model=cls.__name__)
        reject_embedded_note_keys(data, model=cls.__name__)
        return data

    @field_validator("catalog_instrument_ids")
    @classmethod
    def strip_instruments(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(item.strip().split()) for item in value]
        if any(not item or len(item) > 80 for item in cleaned):
            _reject(cls.__name__, field_path="catalog_instrument_ids")
        return cleaned

    @field_validator("guarantee", mode="before")
    @classmethod
    def force_guarantee_false(cls, value: Any) -> Any:
        if value is True:
            _reject(cls.__name__, field_path="guarantee")
        return False


class AssetPackBriefSlotV1(BaseModel):
    """Optional custom slot override on a brief."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(..., min_length=2, max_length=32)
    label: str | None = Field(default=None, max_length=ASSET_PACK_LABEL_MAX)
    adaptive_label: str | None = Field(default=None, max_length=ASSET_PACK_LABEL_MAX)
    density: DensityBand | None = None
    narrative_intent: str | None = Field(default=None, max_length=ASSET_PACK_NARRATIVE_MAX)
    duration_seconds: int | None = Field(
        default=None,
        ge=SLOT_DURATION_SECONDS_MIN,
        le=SLOT_DURATION_SECONDS_MAX,
    )

    @field_validator("slot_id")
    @classmethod
    def validate_slot_id(cls, value: str) -> str:
        token = value.strip()
        if not SLOT_ID_RE.match(token):
            _reject(cls.__name__, field_path="slot_id")
        return token

    @field_validator("label", "adaptive_label", "narrative_intent", mode="before")
    @classmethod
    def strip_optional(cls, value: Any) -> Any:
        return _strip_text(value)


class AssetPackBriefV1(BaseModel):
    """User goal for a soundtrack / production asset pack. Not a score."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["asset.pack.brief.v1"] = ASSET_PACK_BRIEF_SCHEMA
    title: str = Field(..., min_length=1, max_length=ASSET_PACK_TITLE_MAX)
    preset: AssetPackPresetId | None = "game_soundtrack_v1"
    slots: list[AssetPackBriefSlotV1] | None = Field(default=None, max_length=ASSET_PACK_MAX_SLOTS)
    composer_profile_id: str | None = Field(default=None, min_length=1, max_length=80)
    composer_profile_strength: ProfileStrength = "normal"
    opening_key: str = "C minor"
    time_signature: str = "4/4"
    tempo_min: int = Field(default=72, ge=1, le=400)
    tempo_max: int = Field(default=96, ge=1, le=400)
    motif_label: str = Field(default="Theme A", min_length=1, max_length=ASSET_PACK_MOTIF_LABEL_MAX)
    seed_slot_id: str = "main_theme"
    production: AssetPackProductionV1 | None = None
    include_rendering: bool = False
    include_adaptive_scaffolds: bool = True
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @model_validator(mode="before")
    @classmethod
    def reject_notes_and_playable(cls, data: Any) -> Any:
        reject_playable_top_level(data, model=cls.__name__)
        reject_embedded_note_keys(data, model=cls.__name__)
        return data

    @field_validator("title", "motif_label", mode="before")
    @classmethod
    def strip_required_text(cls, value: Any) -> Any:
        stripped = _strip_text(value)
        if stripped is None:
            _reject(cls.__name__, field_path="title")
        return stripped

    @field_validator("opening_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _normalize_key_label(value)

    @field_validator("time_signature")
    @classmethod
    def validate_meter(cls, value: str) -> str:
        from app.services.composition_timing import parse_time_signature

        meter = value.strip()
        parse_time_signature(meter)
        return meter

    @field_validator("seed_slot_id")
    @classmethod
    def validate_seed_slot(cls, value: str) -> str:
        token = value.strip()
        if not SLOT_ID_RE.match(token):
            _reject(cls.__name__, field_path="seed_slot_id")
        return token

    @model_validator(mode="after")
    def tempo_and_slots(self) -> AssetPackBriefV1:
        if self.tempo_min > self.tempo_max:
            _reject(self.__class__.__name__, field_path="tempo_min")
        if self.preset is None and not self.slots:
            _reject(self.__class__.__name__, field_path="slots")
        if self.slots is not None and len(self.slots) > ASSET_PACK_MAX_SLOTS:
            _reject(self.__class__.__name__, code="asset_pack_slot_limit", field_path="slots")
        return self


class AssetPackPlanSlotV1(BaseModel):
    """One locked soundtrack asset on an AssetPackPlan."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str = Field(..., min_length=2, max_length=32)
    label: str = Field(..., min_length=1, max_length=ASSET_PACK_LABEL_MAX)
    adaptive_label: str = Field(..., min_length=1, max_length=ASSET_PACK_LABEL_MAX)
    density: DensityBand
    duration_seconds: int = Field(
        ...,
        ge=SLOT_DURATION_SECONDS_MIN,
        le=SLOT_DURATION_SECONDS_MAX,
    )
    narrative_intent: str = Field(..., min_length=1, max_length=ASSET_PACK_NARRATIVE_MAX)

    @field_validator("slot_id")
    @classmethod
    def validate_slot_id(cls, value: str) -> str:
        token = value.strip()
        if not SLOT_ID_RE.match(token):
            _reject(cls.__name__, field_path="slot_id")
        return token


class AssetPackPlanConstraintsV1(BaseModel):
    """Hard constraints shared across all slot generates."""

    model_config = ConfigDict(extra="forbid")

    opening_key: str
    time_signature: str
    tempo_min: int = Field(..., ge=1, le=400)
    tempo_max: int = Field(..., ge=1, le=400)
    instrumentation: list[str] = Field(..., min_length=1, max_length=ASSET_PACK_INSTRUMENT_MAX)
    forbidden_instrument_families: list[str] = Field(default_factory=list, max_length=ASSET_PACK_INSTRUMENT_MAX)
    motif_label: str = Field(default="Theme A", min_length=1, max_length=ASSET_PACK_MOTIF_LABEL_MAX)

    @field_validator("opening_key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        return _normalize_key_label(value)

    @model_validator(mode="after")
    def tempo_order(self) -> AssetPackPlanConstraintsV1:
        if self.tempo_min > self.tempo_max:
            _reject(self.__class__.__name__, field_path="tempo_min")
        return self


class AssetPackThemePolicyV1(BaseModel):
    """Universe Theme A seed + per-slot mechanical propagate table."""

    model_config = ConfigDict(extra="forbid")

    seed_slot_id: str = "main_theme"
    propagate: dict[str, AssetPackPropagateOp] = Field(default_factory=dict)
    require_motif_pin: bool = True
    destination_start_bar: int = Field(default=1, ge=1, le=512)

    @field_validator("seed_slot_id")
    @classmethod
    def validate_seed(cls, value: str) -> str:
        token = value.strip()
        if not SLOT_ID_RE.match(token):
            _reject(cls.__name__, field_path="seed_slot_id")
        return token

    @model_validator(mode="after")
    def seed_not_in_propagate(self) -> AssetPackThemePolicyV1:
        if self.seed_slot_id in self.propagate:
            _reject(self.__class__.__name__, field_path="propagate")
        for slot_id in self.propagate:
            if not SLOT_ID_RE.match(slot_id):
                _reject(self.__class__.__name__, field_path="propagate")
        return self


class AssetPackPlanV1(BaseModel):
    """AssetPackPlan — non-playable structure required before generate."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["asset.pack.plan.v1"] = ASSET_PACK_PLAN_SCHEMA
    title: str = Field(..., min_length=1, max_length=ASSET_PACK_TITLE_MAX)
    preset: AssetPackPresetId | None = None
    slots: list[AssetPackPlanSlotV1] = Field(..., min_length=1, max_length=ASSET_PACK_MAX_SLOTS)
    constraints: AssetPackPlanConstraintsV1
    production: AssetPackProductionV1
    theme_policy: AssetPackThemePolicyV1
    composer_profile_id: str | None = Field(default=None, min_length=1, max_length=80)
    composer_profile_strength: ProfileStrength = "normal"
    include_rendering: bool = False
    include_adaptive_scaffolds: bool = True
    plan_digest: str = Field(..., min_length=64, max_length=64)
    seed: int | None = Field(default=None, ge=0, le=2_147_483_647)

    @model_validator(mode="before")
    @classmethod
    def reject_notes_and_playable(cls, data: Any) -> Any:
        reject_playable_top_level(data, model=cls.__name__)
        reject_embedded_note_keys(data, model=cls.__name__)
        return data

    @field_validator("plan_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        digest = value.strip().lower()
        if not SHA256_RE.match(digest):
            _reject(cls.__name__, field_path="plan_digest")
        return digest

    @model_validator(mode="after")
    def slots_and_theme_policy(self) -> AssetPackPlanV1:
        slot_ids = [slot.slot_id for slot in self.slots]
        if len(slot_ids) != len(set(slot_ids)):
            _reject(self.__class__.__name__, field_path="slots")
        if self.theme_policy.seed_slot_id not in slot_ids:
            _reject(self.__class__.__name__, field_path="theme_policy.seed_slot_id")
        for prop_id in self.theme_policy.propagate:
            if prop_id not in slot_ids:
                _reject(self.__class__.__name__, field_path="theme_policy.propagate")
        return self


class AssetPackSlotRecordV1(BaseModel):
    """Durable slot status row projection (no note events)."""

    model_config = ConfigDict(extra="forbid")

    slot_id: str
    label: str | None = None
    status: AssetPackSlotStatus = "pending"
    project_id: str | None = None
    autonomous_run_id: str | None = None
    adaptive_score_id: str | None = None
    head_revision_id: str | None = None
    warning_codes: list[str] = Field(default_factory=list)
    updated_at: str | None = None

    @field_validator("slot_id")
    @classmethod
    def validate_slot_id(cls, value: str) -> str:
        token = value.strip()
        if not SLOT_ID_RE.match(token):
            _reject(cls.__name__, field_path="slot_id")
        return token


class AssetPackV1(BaseModel):
    """Durable pack run document. Owns plan digest and slot→project map."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["asset.pack.v1"] = ASSET_PACK_SCHEMA
    id: str
    name: str = Field(..., min_length=1, max_length=ASSET_PACK_TITLE_MAX)
    status: AssetPackStatus = "planned"
    plan_digest: str = Field(..., min_length=64, max_length=64)
    universe_id: str | None = None
    composer_profile_id: str | None = None
    composer_profile_strength: ProfileStrength = "normal"
    document_revision: int = Field(..., ge=1)
    slots: list[AssetPackSlotRecordV1] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_notes_and_playable(cls, data: Any) -> Any:
        reject_playable_top_level(data, model=cls.__name__)
        reject_embedded_note_keys(data, model=cls.__name__)
        return data

    @field_validator("id")
    @classmethod
    def validate_pack_id(cls, value: str) -> str:
        token = value.strip()
        if not PACK_ID_RE.match(token):
            _reject(cls.__name__, field_path="id")
        return token

    @field_validator("plan_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        digest = value.strip().lower()
        if not SHA256_RE.match(digest):
            _reject(cls.__name__, field_path="plan_digest")
        return digest


class AssetPackPlanPreviewRequest(BaseModel):
    """POST /asset-packs/plan/preview body."""

    model_config = ConfigDict(extra="forbid")

    brief: AssetPackBriefV1


class AssetPackCreateRequest(BaseModel):
    """POST /asset-packs — persist planned pack without generate."""

    model_config = ConfigDict(extra="forbid")

    brief: AssetPackBriefV1 | None = None
    plan: AssetPackPlanV1 | None = None

    @model_validator(mode="after")
    def require_brief_or_plan(self) -> AssetPackCreateRequest:
        if self.brief is None and self.plan is None:
            _reject(self.__class__.__name__, field_path="brief")
        return self


class AssetPackGenerateRequest(BaseModel):
    """POST /asset-packs/{id}/generate — explicit sync generate."""

    model_config = ConfigDict(extra="forbid")

    expected_plan_digest: str = Field(..., min_length=64, max_length=64)
    expected_revision: int = Field(..., ge=1)

    @field_validator("expected_plan_digest")
    @classmethod
    def validate_digest(cls, value: str) -> str:
        digest = value.strip().lower()
        if not SHA256_RE.match(digest):
            _reject(cls.__name__, field_path="expected_plan_digest")
        return digest


class AssetPackRegenerateRequest(BaseModel):
    """POST /asset-packs/{id}/regenerate — partial slot rewrite."""

    model_config = ConfigDict(extra="forbid")

    slot_ids: list[str] = Field(..., min_length=1, max_length=ASSET_PACK_MAX_SLOTS)
    expected_revision: int = Field(..., ge=1)

    @field_validator("slot_ids")
    @classmethod
    def validate_slot_ids(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in value:
            token = str(raw).strip()
            if not SLOT_ID_RE.match(token):
                _reject(cls.__name__, code="asset_pack_slot_unknown", field_path="slot_ids")
            if token not in seen:
                seen.add(token)
                cleaned.append(token)
        if not cleaned:
            _reject(cls.__name__, code="asset_pack_slot_unknown", field_path="slot_ids")
        return cleaned


def game_soundtrack_propagate_complete() -> bool:
    """True when the locked propagate table covers every non-seed preset slot."""
    seed = "main_theme"
    preset_ids = {row["slot_id"] for row in GAME_SOUNDTRACK_V1_SLOTS}
    expected = preset_ids - {seed}
    return set(GAME_SOUNDTRACK_V1_PROPAGATE.keys()) == expected


__all__ = [
    "ASSET_PACK_BRIEF_SCHEMA",
    "ASSET_PACK_ERROR_MESSAGES",
    "ASSET_PACK_MAX_SLOTS",
    "ASSET_PACK_PLAN_SCHEMA",
    "ASSET_PACK_PRODUCTION_SCHEMA",
    "ASSET_PACK_SCHEMA",
    "FORBIDDEN_NOTE_KEYS",
    "FORBIDDEN_PLAYABLE_TOP_LEVEL",
    "GAME_SOUNDTRACK_V1_PROPAGATE",
    "GAME_SOUNDTRACK_V1_SLOTS",
    "NON_SEED_DURATION_SECONDS_DEFAULT",
    "PACK_ID_PREFIX",
    "PACK_ID_RE",
    "SEED_DURATION_SECONDS_DEFAULT",
    "SLOT_DURATION_SECONDS_MAX",
    "SLOT_ID_RE",
    "AssetPackBriefV1",
    "AssetPackCreateRequest",
    "AssetPackError",
    "AssetPackGenerateRequest",
    "AssetPackPlanPreviewRequest",
    "AssetPackPlanV1",
    "AssetPackProductionV1",
    "AssetPackPropagateOp",
    "AssetPackRegenerateRequest",
    "AssetPackSlotRecordV1",
    "AssetPackThemePolicyV1",
    "AssetPackV1",
    "find_forbidden_note_keys",
    "game_soundtrack_propagate_complete",
    "log_asset_pack_schema_rejection",
    "reject_embedded_note_keys",
    "reject_playable_top_level",
]
