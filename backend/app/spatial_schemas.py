"""Strict ``spatial.scene.v1`` and ``spatial.preview.v1`` contracts.

Scenes never embed note events, harmony arrays, or PCM/base64 audio.
Previews never carry PCM.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.spatial_constants import (
    AZIMUTH_DEG_MAX,
    AZIMUTH_DEG_MIN,
    CATALOG_PRESET_IDS,
    D_MAX,
    ELEVATION_DEG_MAX,
    ELEVATION_DEG_MIN,
    ENGINE_VERSION,
    GAIN_MAX,
    GAIN_MIN,
    MAX_MOTION_KEYFRAMES,
    MAX_SOURCES,
    PREVIEW_SCHEMA_VERSION,
    SCENE_SCHEMA_VERSION,
    SOURCE_KINDS,
    SPREAD_MAX,
    SPREAD_MIN,
    STEM_SET_FINGERPRINT_HEX_LEN,
)

logger = logging.getLogger(__name__)

SpatialSceneSchema = Literal["spatial.scene.v1"]
SpatialPreviewSchema = Literal["spatial.preview.v1"]
SpatialSourceKind = Literal["track", "stem"]
SpatialPresetId = Literal["front_stereo", "circle_ensemble", "close_intimate"]

SCENE_ID_RE = re.compile(r"^sscene_[0-9a-f]{16}$")
SOURCE_ID_RE = re.compile(r"^ssrc_[0-9a-f]{8,32}$")

SPATIAL_ERROR_CODES: dict[str, str] = {
    "unsupported_schema_version": "schema_version must be spatial.scene.v1.",
    "scene_embeds_events": "Spatial scenes cannot embed note events.",
    "scene_embeds_harmony": "Spatial scenes cannot embed harmony.",
    "scene_embeds_pcm": "Spatial scenes cannot embed PCM or base64 audio.",
    "scene_embeds_forbidden": "Spatial scenes cannot embed playable score material.",
    "spatial_scene_invalid": "Spatial scene failed schema validation.",
    "spatial_scene_not_found": "Spatial scene id was not found.",
    "spatial_scene_conflict": "expected_document_revision does not match.",
    "spatial_scene_stale": "Scene fingerprint does not match request composition/stem-set.",
    "identity_mismatch": "Scene id or project id does not match the route.",
    "project_not_found": "Project id was not found.",
    "persistence_secret_rejected": "Payload contains a secret field or value.",
    "composition_required": "Compile requires a composition body when any track source exists.",
    "composition_invalid": "Request composition failed composition.v2 validation.",
    "stem_set_required": "Compile requires stem-set metadata when any stem source exists.",
    "stem_id_required": "Stem sources require stem_id (stem_role alone is refused).",
    "preset_unknown": "Unknown preset_id for catalog clone.",
    "preview_invalid": "Spatial preview failed schema validation.",
    "source_unresolved": "Spatial source does not resolve on the request composition/stem-set.",
    "spatial_scene_store_failed": "Spatial scene persistence failed.",
    "too_many_sources": f"Spatial scene exceeds MAX_SOURCES={MAX_SOURCES}.",
    "too_many_motion_keyframes": (
        f"Motion exceeds MAX_MOTION_KEYFRAMES={MAX_MOTION_KEYFRAMES}."
    ),
}


class SpatialSceneError(Exception):
    """Domain error for spatial scene / preview paths."""

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
        self.message = message
        self.http_status = http_status
        self.details = details or {}


def map_spatial_error_to_http(exc: SpatialSceneError) -> tuple[int, dict[str, Any]]:
    return exc.http_status, {
        "code": exc.code,
        "message": exc.message,
        "details": exc.details,
    }


def _log_validation(code: str, **extra: Any) -> None:
    logger.debug(
        "Spatial validation failed",
        extra={"code": code, **extra},
    )


_PCM_KEY_HINTS: frozenset[str] = frozenset(
    {
        "pcm",
        "audio_base64",
        "wav_base64",
        "audio_bytes",
        "raw_audio",
        "base64_audio",
        "pcm_base64",
    }
)


def reject_scene_embedded_material(payload: dict[str, Any], *, path: str = "") -> None:
    """Refuse scene bodies that embed events, harmony arrays, or PCM/base64 audio."""
    for key, value in payload.items():
        child_path = f"{path}.{key}" if path else key
        if key in ("events", "notes", "midi_events", "note_performances"):
            code = "scene_embeds_events"
            _log_validation(code, path=child_path)
            raise SpatialSceneError(
                code,
                SPATIAL_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if key == "harmony" and isinstance(value, list):
            code = "scene_embeds_harmony"
            _log_validation(code, path=child_path)
            raise SpatialSceneError(
                code,
                SPATIAL_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if key in _PCM_KEY_HINTS or (
            key.endswith("_pcm")
            or key.endswith("_base64")
            and "audio" in key.lower()
        ):
            code = "scene_embeds_pcm"
            _log_validation(code, path=child_path)
            raise SpatialSceneError(
                code,
                SPATIAL_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if key in ("composition", "composition_json"):
            code = "scene_embeds_forbidden"
            _log_validation(code, path=child_path)
            raise SpatialSceneError(
                code,
                SPATIAL_ERROR_CODES[code],
                http_status=422,
                details={"path": child_path},
            )
        if isinstance(value, dict):
            reject_scene_embedded_material(value, path=child_path)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    reject_scene_embedded_material(item, path=f"{child_path}[{index}]")


def stem_set_fingerprint(sha256_prefixes: list[str]) -> str:
    """``sha256(":".join(sorted(prefixes)))`` hex, length STEM_SET_FINGERPRINT_HEX_LEN."""
    joined = ":".join(sorted(str(p) for p in sha256_prefixes if p))
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    return digest[:STEM_SET_FINGERPRINT_HEX_LEN]


class SpatialListenerV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    yaw_deg: float = Field(0.0, ge=-180.0, le=180.0)


class SpatialMotionKeyframeV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int | None = Field(None, ge=0)
    time_seconds: float | None = Field(None, ge=0.0)
    azimuth_deg: float = Field(..., ge=AZIMUTH_DEG_MIN, le=AZIMUTH_DEG_MAX)
    elevation_deg: float = Field(..., ge=ELEVATION_DEG_MIN, le=ELEVATION_DEG_MAX)
    distance: float = Field(..., ge=0.0, le=D_MAX)
    spread: float = Field(..., ge=SPREAD_MIN, le=SPREAD_MAX)

    @model_validator(mode="after")
    def require_time_key(self) -> SpatialMotionKeyframeV1:
        if self.tick is None and self.time_seconds is None:
            raise ValueError("motion keyframe requires tick or time_seconds")
        return self


class SpatialMixSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, max_length=64)
    source_kind: SpatialSourceKind
    track_id: str | None = Field(None, min_length=1, max_length=64)
    stem_id: str | None = Field(None, min_length=1, max_length=128)
    stem_role: str | None = Field(None, min_length=1, max_length=64)
    azimuth_deg: float = Field(0.0, ge=AZIMUTH_DEG_MIN, le=AZIMUTH_DEG_MAX)
    elevation_deg: float = Field(0.0, ge=ELEVATION_DEG_MIN, le=ELEVATION_DEG_MAX)
    distance: float = Field(1.0, ge=0.0, le=D_MAX)
    spread: float = Field(0.0, ge=SPREAD_MIN, le=SPREAD_MAX)
    gain: float = Field(1.0, ge=GAIN_MIN, le=GAIN_MAX)
    motion: list[SpatialMotionKeyframeV1] = Field(default_factory=list)
    muted: bool = False

    @field_validator("source_kind")
    @classmethod
    def validate_kind(cls, value: str) -> str:
        if value not in SOURCE_KINDS:
            raise ValueError(f"source_kind must be one of {sorted(SOURCE_KINDS)}")
        return value

    @model_validator(mode="after")
    def bind_rules(self) -> SpatialMixSourceV1:
        if len(self.motion) > MAX_MOTION_KEYFRAMES:
            raise ValueError(
                f"motion exceeds MAX_MOTION_KEYFRAMES={MAX_MOTION_KEYFRAMES}"
            )
        if self.source_kind == "track":
            if not self.track_id:
                raise ValueError("track sources require track_id")
        elif self.source_kind == "stem":
            if not self.stem_id:
                raise ValueError("stem sources require stem_id")
        return self


class SpatialSceneV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: SpatialSceneSchema = SCENE_SCHEMA_VERSION
    id: str | None = None
    project_id: str | None = None
    name: str = Field(..., min_length=1, max_length=120)
    listener: SpatialListenerV1 = Field(default_factory=SpatialListenerV1)
    sources: list[SpatialMixSourceV1] = Field(default_factory=list)
    source_composition_fingerprint: str = Field(..., min_length=8, max_length=128)
    source_stem_set_id: str | None = Field(None, min_length=1, max_length=128)
    source_stem_set_fingerprint: str | None = Field(None, min_length=8, max_length=128)
    engine_version: str = ENGINE_VERSION

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be empty")
        return name

    @model_validator(mode="after")
    def caps_and_stem_set(self) -> SpatialSceneV1:
        if len(self.sources) > MAX_SOURCES:
            raise ValueError(f"sources exceed MAX_SOURCES={MAX_SOURCES}")
        has_stem = any(s.source_kind == "stem" for s in self.sources)
        if has_stem:
            if not self.source_stem_set_id or not self.source_stem_set_fingerprint:
                raise ValueError(
                    "source_stem_set_id and source_stem_set_fingerprint required "
                    "when any stem source exists"
                )
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError("source ids must be unique within a scene")
        return self


class SpatialStereoCoeffsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_gain: float
    right_gain: float


class SpatialFoaCoeffsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    w: float
    y: float
    z: float
    x: float


class SpatialPreviewSourceV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    source_kind: SpatialSourceKind
    track_id: str | None = None
    stem_id: str | None = None
    stereo: SpatialStereoCoeffsV1
    foa: SpatialFoaCoeffsV1
    distance_gain: float
    spread: float
    muted: bool = False
    skipped: bool = False
    skip_reason: str | None = None


class SpatialPreviewStaleV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: bool = False
    stem_set: bool = False


class SpatialPreviewMetricsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_count: int = Field(0, ge=0)
    track_count: int = Field(0, ge=0)
    stem_count: int = Field(0, ge=0)
    skipped_count: int = Field(0, ge=0)
    mean_distance: float = Field(0.0, ge=0.0)
    stereo_imbalance: float = Field(0.0, ge=0.0)
    foa_energy: float = Field(0.0, ge=0.0)
    motion_sampled: bool = False
    sample_tick: int = Field(0, ge=0)


class SpatialPreviewV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: SpatialPreviewSchema = PREVIEW_SCHEMA_VERSION
    scene_id: str
    scene_revision: int = Field(..., ge=1)
    engine_version: str
    source_composition_fingerprint: str
    source_stem_set_fingerprint: str | None = None
    stale: SpatialPreviewStaleV1 = Field(default_factory=SpatialPreviewStaleV1)
    listener: SpatialListenerV1 = Field(default_factory=SpatialListenerV1)
    sources: list[SpatialPreviewSourceV1] = Field(default_factory=list)
    metrics: SpatialPreviewMetricsV1 = Field(default_factory=SpatialPreviewMetricsV1)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def refuse_pcm(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        for key in _PCM_KEY_HINTS:
            if key in data:
                raise ValueError("spatial preview must not include PCM fields")
        return data


class SpatialSceneSummaryV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    name: str
    document_revision: int
    source_composition_fingerprint: str
    source_stem_set_id: str | None = None
    source_stem_set_fingerprint: str | None = None
    source_count: int = 0
    created_at: str
    updated_at: str


class SpatialSceneGetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: SpatialSceneV1
    document_revision: int
    created_at: str
    updated_at: str


class SpatialSceneListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenes: list[SpatialSceneSummaryV1]


class SpatialPresetCatalogItemV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: SpatialPresetId
    name: str
    description: str
    source_count: int = Field(..., ge=0)


class SpatialPresetCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    presets: list[SpatialPresetCatalogItemV1]


class SpatialSceneCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    preset_id: SpatialPresetId | None = None
    scene: SpatialSceneV1 | None = None
    source_composition_fingerprint: str | None = None
    composition: dict[str, Any] | None = None
    source_stem_set_id: str | None = None
    source_stem_set_fingerprint: str | None = None
    stem_sha256_prefixes: list[str] | None = None


class SpatialSceneUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene: SpatialSceneV1
    expected_document_revision: int = Field(..., ge=1)


class SpatialStemMetaV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stem_id: str
    sha256_prefix: str = Field(..., min_length=8, max_length=128)
    role: str | None = None


class SpatialCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    composition: dict[str, Any] | None = None
    stem_set_id: str | None = None
    stem_set_fingerprint: str | None = None
    stems: list[SpatialStemMetaV1] | None = None
    at_tick: int = Field(0, ge=0)
    at_seconds: float | None = Field(None, ge=0.0)
    # Draft scene body override (unsaved edits) — compile without writing.
    scene: SpatialSceneV1 | None = None


class SpatialCompileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview: SpatialPreviewV1


def parse_spatial_scene(payload: dict[str, Any]) -> SpatialSceneV1:
    """Validate and parse a scene body; refuse embeds with stable codes."""
    if not isinstance(payload, dict):
        raise SpatialSceneError(
            "spatial_scene_invalid",
            SPATIAL_ERROR_CODES["spatial_scene_invalid"],
            http_status=422,
        )
    reject_scene_embedded_material(payload)
    schema = payload.get("schema_version")
    if schema is not None and schema != SCENE_SCHEMA_VERSION:
        _log_validation("unsupported_schema_version", schema_version=schema)
        raise SpatialSceneError(
            "unsupported_schema_version",
            SPATIAL_ERROR_CODES["unsupported_schema_version"],
            http_status=422,
            details={"schema_version": schema},
        )
    try:
        return SpatialSceneV1.model_validate(payload)
    except ValidationError as exc:
        # Map stem_id / caps messages to stable codes when possible.
        text = str(exc).lower()
        code = "spatial_scene_invalid"
        if "stem_id" in text and "require" in text:
            code = "stem_id_required"
        elif "max_sources" in text:
            code = "too_many_sources"
        elif "max_motion_keyframes" in text:
            code = "too_many_motion_keyframes"
        _log_validation(code)
        raise SpatialSceneError(
            code,
            SPATIAL_ERROR_CODES.get(code, SPATIAL_ERROR_CODES["spatial_scene_invalid"]),
            http_status=422,
            details={"errors": exc.errors()},
        ) from exc


def parse_spatial_preview(payload: dict[str, Any]) -> SpatialPreviewV1:
    if not isinstance(payload, dict):
        raise SpatialSceneError(
            "preview_invalid",
            SPATIAL_ERROR_CODES["preview_invalid"],
            http_status=422,
        )
    try:
        return SpatialPreviewV1.model_validate(payload)
    except ValidationError as exc:
        _log_validation("preview_invalid")
        raise SpatialSceneError(
            "preview_invalid",
            SPATIAL_ERROR_CODES["preview_invalid"],
            http_status=422,
            details={"errors": exc.errors()},
        ) from exc


def metrics_digest(metrics: SpatialPreviewMetricsV1 | dict[str, Any]) -> str:
    """Stable digest of summary metrics for distinguishability asserts."""
    if isinstance(metrics, SpatialPreviewMetricsV1):
        data = metrics.model_dump(mode="json")
    else:
        data = dict(metrics)
    for key in ("mean_distance", "stereo_imbalance", "foa_energy"):
        if key in data:
            data[key] = round(float(data[key]), 6)
    import json

    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def catalog_preset_id_ok(preset_id: str) -> bool:
    return preset_id in CATALOG_PRESET_IDS
