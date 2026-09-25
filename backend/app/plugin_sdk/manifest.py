"""plugin.manifest.v1 discovery metadata. No code, secrets, PCM, or note arrays."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import schema_shape_error
from .errors import PluginError

logger = logging.getLogger(__name__)

PLUGIN_MANIFEST_SCHEMA = "plugin.manifest.v1"
MANIFEST_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
PLUGIN_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ENTRY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*$")
_SECRET_PROPERTY = re.compile(r"(?i)(secret|password|token|api_key|apikey|authorization)")
# Names the persistence guard rejects. Kept here so the SDK does not import services.
_FORBIDDEN_CONFIG_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "openai_api_key",
        "deepseek_api_key",
        "authorization",
        "access_token",
        "secret",
        "password",
        "bearer",
        "token",
    }
)

PluginCategory = Literal[
    "language_model",
    "symbolic_composer",
    "transcription_model",
    "neural_renderer",
    "analyzer",
    "music_agent",
    "export_format",
    "postprocess",
]

MODEL_CATEGORIES = frozenset(
    {"language_model", "symbolic_composer", "transcription_model", "neural_renderer"}
)

# Capability strings allowed on the manifest. Model rows match existing ModelCapability
# values. Non-model rows allow only their own kind string.
CATEGORY_CAPABILITIES: dict[str, frozenset[str]] = {
    "language_model": frozenset({"language_planner"}),
    "symbolic_composer": frozenset({"symbolic_composer"}),
    "transcription_model": frozenset({"audio_transcription"}),
    "neural_renderer": frozenset({"audio_generation"}),
    "analyzer": frozenset({"analyzer"}),
    "music_agent": frozenset({"music_agent"}),
    "export_format": frozenset({"export_format"}),
    "postprocess": frozenset({"postprocess"}),
}

PluginResource = Literal[
    "filesystem_model_dir",
    "network",
    "gpu",
    "project_read",
    "project_write",
    "audio_processing",
]

PLUGIN_RESOURCES = frozenset(
    {
        "filesystem_model_dir",
        "network",
        "gpu",
        "project_read",
        "project_write",
        "audio_processing",
    }
)

CATEGORY_METHOD: dict[str, str] = {
    "language_model": "complete_text",
    "symbolic_composer": "compose",
    "transcription_model": "transcribe",
    "neural_renderer": "render",
    "analyzer": "analyze",
    "music_agent": "run",
    "export_format": "export",
    "postprocess": "process",
}


class PluginDependencyV1(BaseModel):
    """Activation-order dependency. Exact version match when ``version`` is set."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str | None = None

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        if MANIFEST_ID_RE.fullmatch(value) is None:
            raise ValueError("dependency id is not a manifest slug")
        return value

    @field_validator("version")
    @classmethod
    def _version(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if PLUGIN_SEMVER_RE.fullmatch(value) is None:
            raise ValueError("dependency version must be MAJOR.MINOR.PATCH")
        return value


class PluginManifestV1(BaseModel):
    """Discovery document. The host loads code from ``entry``, not from this file."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["plugin.manifest.v1"]
    id: str
    name: str = Field(min_length=1, max_length=80)
    version: str
    api_compatibility: str = Field(min_length=1, max_length=32)
    category: PluginCategory
    capabilities: list[str] = Field(min_length=1)
    dependencies: list[PluginDependencyV1] = Field(default_factory=list)
    resources: list[PluginResource] = Field(default_factory=list)
    configuration_schema: dict[str, Any] | None = None
    entry: str = "plugin:register"

    @field_validator("id")
    @classmethod
    def _plugin_id(cls, value: str) -> str:
        if MANIFEST_ID_RE.fullmatch(value) is None:
            raise ValueError("id must match ^[a-z][a-z0-9_]{1,63}$")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("version")
    @classmethod
    def _semver(cls, value: str) -> str:
        if PLUGIN_SEMVER_RE.fullmatch(value) is None:
            raise ValueError("version must be MAJOR.MINOR.PATCH")
        return value

    @field_validator("entry")
    @classmethod
    def _entry(cls, value: str) -> str:
        if ENTRY_RE.fullmatch(value) is None:
            raise ValueError("entry must be module:attr with no dots")
        return value

    @model_validator(mode="after")
    def _capabilities_and_schema(self) -> PluginManifestV1:
        allowed = CATEGORY_CAPABILITIES[self.category]
        unknown = [item for item in self.capabilities if item not in allowed]
        if unknown:
            raise ValueError("capability is not allowed for category")
        seen: set[str] = set()
        for item in self.dependencies:
            if item.id in seen:
                raise ValueError("duplicate dependency id")
            seen.add(item.id)
        if self.configuration_schema is not None:
            shape_error = schema_shape_error(self.configuration_schema)
            if shape_error is not None:
                raise ValueError(shape_error)
            secret_name = _secret_property_name(self.configuration_schema)
            if secret_name is not None:
                logger.warning(
                    "plugin manifest secret-shaped property rejected",
                    extra={"code": "plugin_manifest_invalid", "plugin_id": self.id},
                )
                raise ValueError("configuration_schema property name is secret-shaped")
        return self


def peek_manifest_id(data: Any) -> str | None:
    """Best-effort id for a rejected document. Does not validate the rest."""
    if isinstance(data, dict) and isinstance(data.get("id"), str):
        candidate = data["id"]
        if MANIFEST_ID_RE.fullmatch(candidate):
            return candidate
    return None


def parse_manifest(data: Any, *, source_name: str | None = None) -> PluginManifestV1:
    """Parse a manifest mapping. ``source_name`` is logged as a basename only."""
    basename = Path(source_name).name if source_name else None
    plugin_id = peek_manifest_id(data)
    logger.debug("plugin manifest parse start", extra={"manifest_basename": basename, "plugin_id": plugin_id})
    if isinstance(data, dict):
        _warn_unknown_resources(data.get("resources"), plugin_id)
    try:
        manifest = PluginManifestV1.model_validate(data)
    except Exception as exc:
        logger.warning(
            "plugin manifest validation failed",
            extra={"code": "plugin_manifest_invalid", "plugin_id": plugin_id, "error_type": type(exc).__name__},
        )
        raise PluginError("plugin_manifest_invalid", "plugin_manifest_invalid") from exc
    logger.debug(
        "plugin manifest parse end",
        extra={
            "plugin_id": manifest.id,
            "category": manifest.category,
            "version": manifest.version,
            "resource_count": len(manifest.resources),
        },
    )
    return manifest


def _warn_unknown_resources(resources: Any, plugin_id: str | None) -> None:
    if not isinstance(resources, list):
        return
    if any(item not in PLUGIN_RESOURCES for item in resources):
        logger.warning(
            "plugin manifest resource rejected",
            extra={"code": "plugin_manifest_invalid", "plugin_id": plugin_id, "resource_count": len(resources)},
        )


def _secret_property_name(schema: dict[str, Any]) -> str | None:
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return None
    for name in properties:
        normalized = str(name).strip().lower().replace("-", "_")
        if _SECRET_PROPERTY.search(str(name)) or normalized in _FORBIDDEN_CONFIG_NAMES or normalized.endswith("_api_key"):
            return str(name)
    return None
