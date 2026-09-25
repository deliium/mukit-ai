"""Host dispatch for plugin protocols. Callers keep shipped orchestrators."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Mapping

from pydantic import ValidationError

from app.composition_schemas import CompositionV2
from app.plugin_sdk.context import PluginContext
from app.plugin_sdk.errors import PluginError
from app.services.composition_validator import validate_composition_integrity

from .catalog import PluginRecord, get_record, update_record
from .import_guard import drop_private_module, plugin_import_guard
from .settings import load_plugin_settings

logger = logging.getLogger(__name__)

ANALYSIS_FRAGMENT_SCHEMA = "plugin.analysis.fragment.v1"
OUTPUT_INVALID = "plugin_output_invalid"


class PluginHost:
    """Invoke an activated plugin and enforce host postconditions."""

    def compose(self, model_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        plugin_id = _manifest_id_from_model(model_id)
        record = _require_category(plugin_id, "symbolic_composer")
        raw = _invoke(record, lambda: record.instance.compose(_context(record), request))
        return _validated_mapping(raw)

    def complete_text(self, model_id: str, prompt: str) -> str:
        plugin_id = _manifest_id_from_model(model_id)
        record = _require_category(plugin_id, "language_model")
        raw = _invoke(record, lambda: record.instance.complete_text(_context(record), prompt))
        if not isinstance(raw, str):
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        logger.info("plugin call", extra={"category": "language_model", "plugin_id": record.id, "success": True})
        return raw

    def transcribe(self, model_id: str, audio_ref: Mapping[str, Any]) -> dict[str, Any]:
        plugin_id = _manifest_id_from_model(model_id)
        record = _require_category(plugin_id, "transcription_model")
        raw = _invoke(record, lambda: record.instance.transcribe(_context(record), audio_ref))
        payload = _validated_mapping(raw)
        if _contains_absolute_path(payload):
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        logger.info("plugin call", extra={"category": "transcription_model", "plugin_id": record.id, "success": True})
        return payload

    def render(self, model_id: str, spec: Mapping[str, Any]) -> dict[str, Any]:
        plugin_id = _manifest_id_from_model(model_id)
        record = _require_category(plugin_id, "neural_renderer")
        raw = _invoke(record, lambda: record.instance.render(_context(record), spec))
        payload = _validated_mapping(raw)
        if _contains_absolute_path(payload):
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        logger.info("plugin call", extra={"category": "neural_renderer", "plugin_id": record.id, "success": True})
        return payload

    def analyze(self, plugin_id: str, composition: Mapping[str, Any]) -> dict[str, Any]:
        record = _require_category(plugin_id, "analyzer")
        snapshot = copy.deepcopy(dict(composition))
        raw = _invoke(record, lambda: record.instance.analyze(_context(record), composition))
        if not _json_equal(composition, snapshot):
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        data = _validated_mapping(raw)
        logger.info("plugin call", extra={"category": "analyzer", "plugin_id": record.id, "success": True})
        return {"schema_version": ANALYSIS_FRAGMENT_SCHEMA, "data": data}

    def run_agent(self, plugin_id: str, request_dict: Mapping[str, Any]) -> dict[str, Any]:
        record = _require_category(plugin_id, "music_agent")
        raw = _invoke(record, lambda: record.instance.run(_context(record), request_dict))
        payload = _validated_mapping(raw)
        if payload.get("mutates_composition") is not False:
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        logger.info("plugin call", extra={"category": "music_agent", "plugin_id": record.id, "success": True})
        return payload

    def export(self, plugin_id: str, composition: Mapping[str, Any]) -> tuple[bytes, str]:
        record = _require_category(plugin_id, "export_format")
        snapshot = copy.deepcopy(dict(composition))
        raw = _invoke(record, lambda: record.instance.export(_context(record), composition))
        if not _json_equal(composition, snapshot):
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        if not isinstance(raw, tuple) or len(raw) != 2:
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        payload, media_type = raw
        if not isinstance(payload, (bytes, bytearray)) or not isinstance(media_type, str) or not media_type.strip():
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        settings = load_plugin_settings()
        size = len(payload)
        logger.debug("plugin export bytes", extra={"plugin_id": record.id, "byte_length": size})
        if size > settings.max_export_bytes:
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        logger.info("plugin call", extra={"category": "export_format", "plugin_id": record.id, "success": True})
        return bytes(payload), media_type

    def postprocess(self, plugin_id: str, document: Mapping[str, Any]) -> dict[str, Any]:
        record = _require_category(plugin_id, "postprocess")
        snapshot = copy.deepcopy(dict(document))
        raw = _invoke(record, lambda: record.instance.process(_context(record), document))
        if raw is document or not _json_equal(document, snapshot):
            _warn(record.id, OUTPUT_INVALID)
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        payload = _validated_mapping(raw)
        logger.info("plugin call", extra={"category": "postprocess", "plugin_id": record.id, "success": True})
        return payload

    def validated_composition(self, model_id: str, payload: Mapping[str, Any]) -> CompositionV2:
        """Deep-copy, parse ``composition.v2``, and run the canonical integrity profile."""
        copied = copy.deepcopy(dict(payload))
        try:
            composition = CompositionV2.model_validate(copied)
        except ValidationError as exc:
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID) from exc
        result = validate_composition_integrity(composition, profile="canonical")
        if not result.ok:
            logger.warning(
                "plugin postcondition failed",
                extra={"plugin_id": model_id, "code": OUTPUT_INVALID, "error_codes": result.error_codes()},
            )
            raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
        logger.debug(
            "plugin composition validated",
            extra={
                "model_id": model_id,
                "note_count": sum(len(track.events) for track in composition.tracks),
            },
        )
        return composition


def _context(record: PluginRecord) -> PluginContext:
    from app.plugin_sdk.version import PLUGIN_API_VERSION
    from app.plugin_sdk.context import PluginLogger

    return PluginContext(
        api_version=PLUGIN_API_VERSION,
        plugin_id=record.id or "",
        config=dict(record.config),
        logger=PluginLogger(record.id or "plugin"),
    )


def _invoke(record: PluginRecord, fn: Any) -> Any:
    module_name = record.module_name
    if record.status != "active" or record.instance is None or not module_name:
        code = record.code or "plugin_not_found"
        raise PluginError(code, code)
    try:
        with plugin_import_guard(module_name) as guard:
            result = fn()
            if guard.violation is not None:
                raise PluginError("plugin_forbidden_import", "plugin_forbidden_import")
        return result
    except ImportError as exc:
        _reject_forbidden(record)
        raise PluginError("plugin_forbidden_import", "plugin_forbidden_import") from exc
    except PluginError:
        raise
    except Exception as exc:
        logger.warning(
            "plugin call failed",
            extra={"plugin_id": record.id, "code": OUTPUT_INVALID, "error_type": type(exc).__name__},
        )
        raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID) from exc


def _reject_forbidden(record: PluginRecord) -> None:
    if record.module_name:
        drop_private_module(record.module_name)
    record.status = "rejected"
    record.code = "plugin_forbidden_import"
    record.message = "plugin_forbidden_import"
    record.instance = None
    record.module_name = None
    update_record(record)
    logger.warning("plugin call rejected", extra={"plugin_id": record.id, "code": "plugin_forbidden_import"})


def _require_category(plugin_id: str, category: str) -> PluginRecord:
    record = get_record(plugin_id)
    if record is None or record.status != "active" or record.category != category:
        logger.warning("plugin call rejected", extra={"plugin_id": plugin_id, "code": "plugin_not_found"})
        raise PluginError("plugin_not_found", "plugin_not_found")
    return record


def _manifest_id_from_model(model_id: str) -> str:
    prefix = "plugin:"
    if not model_id.startswith(prefix) or not model_id[len(prefix) :]:
        raise PluginError("plugin_not_found", "plugin_not_found")
    return model_id[len(prefix) :]


def _validated_mapping(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise PluginError(OUTPUT_INVALID, OUTPUT_INVALID)
    return copy.deepcopy(dict(raw))


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, default=str) == json.dumps(right, sort_keys=True, default=str)
    except TypeError:
        return False


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("/") or value.startswith("\\\\")
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_path(item) for item in value)
    return False


def _warn(plugin_id: str | None, code: str) -> None:
    logger.warning("plugin postcondition failed", extra={"plugin_id": plugin_id, "code": code})
    logger.info("plugin call", extra={"plugin_id": plugin_id, "success": False, "code": code})
