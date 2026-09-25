"""Public plugin catalog DTOs. Configuration values are never included."""

from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.plugin_sdk.manifest import MODEL_CATEGORIES

from .catalog import PluginRecord

_SECRET_KEY = re.compile(r"(?i)(secret|password|token|api_key|apikey|authorization)")


PluginLifecycleStatus = Literal[
    "discovered",
    "installed",
    "enabled",
    "disabled",
    "incompatible",
    "failed",
]

PluginHealthStatus = Literal["unknown", "healthy", "unhealthy"]


class PluginHealthV1(BaseModel):
    """Last load or invoke outcome. Separate from lifecycle status."""

    model_config = ConfigDict(extra="forbid")

    status: PluginHealthStatus
    code: str | None = None
    invocation_failure_count: int = 0


class PluginPublic(BaseModel):
    """Manifest public fields plus lifecycle and health. No config document."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    version: str | None = None
    api_compatibility: str | None = None
    category: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    status: str
    code: str | None = None
    message: str
    model_id: str | None = None
    installed_version: str | None = None
    config_present: bool = False
    health: PluginHealthV1
    configuration_schema: dict[str, Any] | None = None


class PluginListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: list[PluginPublic]


def public_plugin(record: PluginRecord) -> PluginPublic:
    model_id = None
    if record.status == "enabled" and record.category in MODEL_CATEGORIES and record.id:
        model_id = f"plugin:{record.id}"
    resources = record.resources
    if not resources and record.manifest is not None:
        resources = tuple(record.manifest.resources)
    return PluginPublic(
        id=record.id,
        name=record.name,
        version=record.version,
        api_compatibility=record.api_compatibility,
        category=record.category,
        capabilities=list(record.capabilities),
        dependencies=list(record.dependency_ids),
        resources=list(resources),
        status=record.status,
        code=record.code,
        message=record.message,
        model_id=model_id,
        installed_version=record.installed_version,
        config_present=record.config_present,
        health=PluginHealthV1(
            status=_health_status(record),
            code=record.health_code,
            invocation_failure_count=record.invocation_failure_count,
        ),
        configuration_schema=_public_schema(record.configuration_schema),
    )


def _health_status(record: PluginRecord) -> PluginHealthStatus:
    if record.health_status in {"unknown", "healthy", "unhealthy"}:
        return record.health_status  # type: ignore[return-value]
    return "unknown"


def _public_schema(schema: dict[str, Any] | None) -> dict[str, Any] | None:
    if schema is None:
        return None
    copied = copy.deepcopy(schema)
    properties = copied.get("properties")
    if isinstance(properties, dict):
        for name, spec in properties.items():
            if isinstance(spec, dict) and _SECRET_KEY.search(str(name)):
                spec.pop("default", None)
    return copied
