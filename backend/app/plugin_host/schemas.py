"""Public plugin catalog DTOs. Configuration values are never included."""

from __future__ import annotations

import copy
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .catalog import PluginRecord

_SECRET_KEY = re.compile(r"(?i)(secret|password|token|api_key|apikey|authorization)")


class PluginPublic(BaseModel):
    """Manifest public fields plus activation status. No config document."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    name: str | None = None
    version: str | None = None
    api_compatibility: str | None = None
    category: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    status: str
    code: str | None = None
    message: str
    model_id: str | None = None
    configuration_schema: dict[str, Any] | None = None


class PluginListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugins: list[PluginPublic]


def public_plugin(record: PluginRecord) -> PluginPublic:
    model_id = None
    if record.status == "active" and record.category in {
        "language_model",
        "symbolic_composer",
        "transcription_model",
        "neural_renderer",
    } and record.id:
        model_id = f"plugin:{record.id}"
    return PluginPublic(
        id=record.id,
        name=record.name,
        version=record.version,
        api_compatibility=record.api_compatibility,
        category=record.category,
        capabilities=list(record.capabilities),
        dependencies=list(record.dependency_ids),
        status=record.status,
        code=record.code,
        message=record.message,
        model_id=model_id,
        configuration_schema=_public_schema(record.configuration_schema),
    )


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
