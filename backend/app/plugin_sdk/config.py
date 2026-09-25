"""JSON Schema subset for plugin config.json. No jsonschema dependency, no secret logging."""

from __future__ import annotations

import logging
from typing import Any

from .errors import PluginError

logger = logging.getLogger(__name__)

_ROOT_KEYS = frozenset({"type", "additionalProperties", "properties", "required"})
_PROP_KEYS = frozenset({"type", "enum", "minimum", "maximum", "minLength", "maxLength", "default"})
_TYPES = frozenset({"string", "number", "integer", "boolean"})


def schema_shape_error(schema: Any) -> str | None:
    """Return a short reason when ``schema`` is outside the v1 subset. Never includes values."""
    if not isinstance(schema, dict):
        return "configuration_schema must be an object"
    extra = set(schema) - _ROOT_KEYS
    if extra or any(str(key).startswith("$") for key in schema):
        return "configuration_schema has unsupported keywords"
    if schema.get("type") != "object":
        return "configuration_schema type must be object"
    if schema.get("additionalProperties") is not False:
        return "configuration_schema additionalProperties must be false"
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return "configuration_schema properties must be an object"
    for name, prop in properties.items():
        if not isinstance(name, str) or not name:
            return "configuration_schema property name invalid"
        reason = _property_shape_error(prop)
        if reason is not None:
            return reason
    required = schema.get("required", [])
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        return "configuration_schema required must be a list of strings"
    if any(item not in properties for item in required):
        return "configuration_schema required key is not a property"
    return None


def _property_shape_error(prop: Any) -> str | None:
    if not isinstance(prop, dict):
        return "configuration_schema property must be an object"
    if set(prop) - _PROP_KEYS or any(str(key).startswith("$") for key in prop):
        return "configuration_schema property has unsupported keywords"
    prop_type = prop.get("type")
    if prop_type not in _TYPES:
        return "configuration_schema property type is not allowed"
    enum = prop.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            return "configuration_schema enum must be a non-empty list"
        if any(_value_error(item, prop_type) is not None for item in enum):
            return "configuration_schema enum value does not match type"
    if "minimum" in prop or "maximum" in prop:
        if prop_type not in {"number", "integer"}:
            return "configuration_schema minimum/maximum require a numeric type"
        for key in ("minimum", "maximum"):
            if key in prop and not _is_number(prop[key]):
                return "configuration_schema bound must be numeric"
    if "minLength" in prop or "maxLength" in prop:
        if prop_type != "string":
            return "configuration_schema length bounds require string"
        for key in ("minLength", "maxLength"):
            if key in prop and (not isinstance(prop[key], int) or isinstance(prop[key], bool) or prop[key] < 0):
                return "configuration_schema length bound invalid"
    if "default" in prop and _value_error(prop["default"], prop_type, prop) is not None:
        return "configuration_schema default does not match property"
    return None


def validate_config(
    schema: dict[str, Any] | None,
    document: Any,
    *,
    plugin_id: str | None = None,
) -> dict[str, Any]:
    """Validate ``document`` against the v1 schema subset and apply non-required defaults.

    Config values and schema defaults are never logged.
    """
    logger.debug("plugin config validate start", extra={"plugin_id": plugin_id, "has_schema": schema is not None})
    if schema is None:
        if document in (None, {}):
            logger.debug("plugin config validate end", extra={"plugin_id": plugin_id, "applied_default_count": 0})
            return {}
        _fail(plugin_id)
    if not isinstance(schema, dict):
        _fail(plugin_id)
    shape = schema_shape_error(schema)
    if shape is not None:
        _fail(plugin_id)
    if document is None:
        document = {}
    if not isinstance(document, dict):
        _fail(plugin_id)
    properties: dict[str, Any] = schema.get("properties") or {}
    if schema.get("additionalProperties") is False:
        unknown = [key for key in document if key not in properties]
        if unknown:
            _fail(plugin_id)
    required = set(schema.get("required") or [])
    validated: dict[str, Any] = {}
    applied_defaults = 0
    for name, prop in properties.items():
        if name in document:
            if _value_error(document[name], prop["type"], prop) is not None:
                _fail(plugin_id)
            validated[name] = document[name]
            continue
        if name in required:
            _fail(plugin_id)
        if "default" in prop:
            validated[name] = prop["default"]
            applied_defaults += 1
    logger.debug(
        "plugin config validate end",
        extra={"plugin_id": plugin_id, "applied_default_count": applied_defaults, "key_count": len(validated)},
    )
    return validated


def _fail(plugin_id: str | None) -> None:
    logger.warning(
        "plugin config validation failed",
        extra={"code": "plugin_config_invalid", "plugin_id": plugin_id},
    )
    raise PluginError("plugin_config_invalid", "plugin_config_invalid")


def _value_error(value: Any, prop_type: str, prop: dict[str, Any] | None = None) -> str | None:
    prop = prop or {}
    if prop_type == "string":
        if not isinstance(value, str):
            return "type"
        if "minLength" in prop and len(value) < prop["minLength"]:
            return "minLength"
        if "maxLength" in prop and len(value) > prop["maxLength"]:
            return "maxLength"
    elif prop_type == "boolean":
        if not isinstance(value, bool):
            return "type"
    elif prop_type == "integer":
        if not _is_int(value):
            return "type"
        if "minimum" in prop and value < prop["minimum"]:
            return "minimum"
        if "maximum" in prop and value > prop["maximum"]:
            return "maximum"
    elif prop_type == "number":
        if not _is_number(value):
            return "type"
        if "minimum" in prop and value < prop["minimum"]:
            return "minimum"
        if "maximum" in prop and value > prop["maximum"]:
            return "maximum"
    else:
        return "type"
    enum = prop.get("enum")
    if enum is not None and value not in enum:
        return "enum"
    return None


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool))
