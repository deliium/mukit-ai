"""Public wire documents the client accepts. Extra keys are rejected."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from mukit_adaptive.errors import AdaptiveClientError

SESSION_SCHEMA = "adaptive.engine.session.v1"
ACK_SCHEMA = "adaptive.engine.ack.v1"
ERROR_SCHEMA = "adaptive.engine.error.v1"
CONTEXT_SCHEMA = "adaptive.context.external.v1"
PHASES_SCHEMA = "adaptive.client.phases.v1"

_SESSION_ID_RE = re.compile(r"^aeng_[0-9a-f]{8}$")
_REQUEST_ID_RE = re.compile(r"^req_[a-z0-9]{1,32}$")
_CONTEXT_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")
_CLOCK_OWNERS = frozenset({"engine", "existing"})
_TRANSPORTS = frozenset({"playing", "held", "stopped"})
_PHASES = frozenset({"bed", "phrase", "stinger"})
_DISPOSITIONS = frozenset({"committed", "queued", "rejected"})
_ACK_DISPOSITIONS = frozenset({"committed", "queued", "rejected", "finished"})
_ACK_KINDS = frozenset({"state", "intensity", "stinger", "cue", "context"})

_SESSION_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "project_id",
        "score_id",
        "clock_owner",
        "transport",
        "runtime_state_id",
        "bar",
        "beat",
        "intensity",
        "phase",
        "active_stinger_id",
        "pending_transition_id",
        "pending_to_state_id",
        "warnings",
        "document_revision",
        "context_attached",
        "telemetry",
    }
)
_TELEMETRY_FIELDS = frozenset(
    {
        "command_count",
        "rejected_count",
        "coalesced_count",
        "dropped_context_count",
        "ack_count",
    }
)
_ACK_FIELDS = frozenset(
    {
        "schema_version",
        "session_id",
        "request_id",
        "kind",
        "disposition",
        "runtime_state_id",
        "detail_code",
    }
)
_ERROR_FIELDS = frozenset(
    {"schema_version", "code", "message", "session_id", "retry_after_ms", "details"}
)
_COMMAND_FIELDS = frozenset(
    {"session", "disposition", "request_id", "coalesced", "applied", "retry_after_ms"}
)


def request_id_ok(value: str) -> bool:
    return _REQUEST_ID_RE.fullmatch(value) is not None


def session_id_ok(value: str) -> bool:
    return _SESSION_ID_RE.fullmatch(value) is not None


@dataclass(frozen=True)
class EngineTelemetry:
    command_count: int
    rejected_count: int
    coalesced_count: int
    dropped_context_count: int
    ack_count: int


@dataclass(frozen=True)
class EngineSession:
    schema_version: str
    session_id: str
    project_id: str
    score_id: str
    clock_owner: str
    transport: str
    runtime_state_id: str
    bar: int
    beat: int
    intensity: float
    phase: str
    active_stinger_id: str | None
    pending_transition_id: str | None
    pending_to_state_id: str | None
    warnings: tuple[str, ...]
    document_revision: int
    context_attached: bool
    telemetry: EngineTelemetry


@dataclass(frozen=True)
class EngineAck:
    schema_version: str
    session_id: str
    request_id: str | None
    kind: str
    disposition: str
    runtime_state_id: str
    detail_code: str | None


@dataclass(frozen=True)
class EngineCommandResult:
    session: EngineSession
    disposition: str
    request_id: str | None
    coalesced: bool
    applied: bool
    retry_after_ms: int | None


def parse_session(data: object) -> EngineSession:
    body = _require_object(data, _SESSION_FIELDS, "session")
    if body["schema_version"] != SESSION_SCHEMA:
        raise _invalid("schema_version")
    session_id = _require_text(body["session_id"], "session_id")
    if not session_id_ok(session_id):
        raise _invalid("session_id")
    clock_owner = _require_choice(body["clock_owner"], _CLOCK_OWNERS, "clock_owner")
    transport = _require_choice(body["transport"], _TRANSPORTS, "transport")
    phase = _require_choice(body["phase"], _PHASES, "phase")
    pending_transition = _optional_text(body["pending_transition_id"], "pending_transition_id")
    pending_state = _optional_text(body["pending_to_state_id"], "pending_to_state_id")
    if (pending_transition is None) != (pending_state is None):
        raise _invalid("pending_transition_id")
    warnings = body["warnings"]
    if not isinstance(warnings, list) or len(warnings) > 8:
        raise _invalid("warnings")
    warning_codes: list[str] = []
    for item in warnings:
        if not isinstance(item, str) or not item:
            raise _invalid("warnings")
        warning_codes.append(item)
    return EngineSession(
        schema_version=SESSION_SCHEMA,
        session_id=session_id,
        project_id=_require_text(body["project_id"], "project_id"),
        score_id=_require_text(body["score_id"], "score_id"),
        clock_owner=clock_owner,
        transport=transport,
        runtime_state_id=_require_text(body["runtime_state_id"], "runtime_state_id"),
        bar=_require_int(body["bar"], "bar", minimum=1),
        beat=_require_int(body["beat"], "beat", minimum=1),
        intensity=_require_unit(body["intensity"], "intensity"),
        phase=phase,
        active_stinger_id=_optional_text(body["active_stinger_id"], "active_stinger_id"),
        pending_transition_id=pending_transition,
        pending_to_state_id=pending_state,
        warnings=tuple(warning_codes),
        document_revision=_require_int(body["document_revision"], "document_revision", minimum=1),
        context_attached=_require_bool(body["context_attached"], "context_attached"),
        telemetry=_parse_telemetry(body["telemetry"]),
    )


def parse_ack(data: object) -> EngineAck:
    body = _require_object(data, _ACK_FIELDS, "ack")
    if body["schema_version"] != ACK_SCHEMA:
        raise _invalid("schema_version")
    session_id = _require_text(body["session_id"], "session_id")
    if not session_id_ok(session_id):
        raise _invalid("session_id")
    request_id = body["request_id"]
    if request_id is not None:
        request_id = _require_text(request_id, "request_id")
        if not request_id_ok(request_id):
            raise _invalid("request_id")
    detail = body["detail_code"]
    if detail is not None:
        detail = _require_text(detail, "detail_code")
    return EngineAck(
        schema_version=ACK_SCHEMA,
        session_id=session_id,
        request_id=request_id,
        kind=_require_choice(body["kind"], _ACK_KINDS, "kind"),
        disposition=_require_choice(body["disposition"], _ACK_DISPOSITIONS, "disposition"),
        runtime_state_id=_require_text(body["runtime_state_id"], "runtime_state_id"),
        detail_code=detail,
    )


def parse_error_document(data: object, *, status: int | None = None) -> AdaptiveClientError:
    """Parse a socket error document or the object under HTTP ``detail``."""
    body = _require_object(data, _ERROR_FIELDS, "error", required=frozenset({"schema_version", "code", "message"}))
    if body.get("schema_version") != ERROR_SCHEMA:
        raise _invalid("schema_version")
    code = _require_text(body["code"], "code")
    message = _require_text(body["message"], "message")
    session_id = body.get("session_id")
    if session_id is not None:
        session_id = _require_text(session_id, "session_id")
    retry_after = body.get("retry_after_ms")
    if retry_after is not None:
        retry_after = _require_int(retry_after, "retry_after_ms", minimum=0)
    details = body.get("details")
    if details is not None and not isinstance(details, dict):
        raise _invalid("details")
    return AdaptiveClientError(
        code,
        message,
        status=status,
        session_id=session_id,
        retry_after_ms=retry_after,
        details=details,
    )


def parse_command_result(data: object) -> EngineCommandResult:
    body = _require_object(data, _COMMAND_FIELDS, "command")
    request_id = body["request_id"]
    if request_id is not None:
        request_id = _require_text(request_id, "request_id")
        if not request_id_ok(request_id):
            raise _invalid("request_id")
    retry_after = body["retry_after_ms"]
    if retry_after is not None:
        retry_after = _require_int(retry_after, "retry_after_ms", minimum=0)
    return EngineCommandResult(
        session=parse_session(body["session"]),
        disposition=_require_choice(body["disposition"], _DISPOSITIONS, "disposition"),
        request_id=request_id,
        coalesced=_require_bool(body["coalesced"], "coalesced"),
        applied=_require_bool(body["applied"], "applied"),
        retry_after_ms=retry_after,
    )


def validate_context_values(values: object) -> dict[str, Any]:
    """Reject a nested object before any HTTP call. Do not echo the values."""
    if not isinstance(values, dict):
        raise _invalid("values")
    cleaned: dict[str, Any] = {}
    for key, item in values.items():
        if not isinstance(key, str) or _CONTEXT_KEY_RE.fullmatch(key) is None:
            raise _invalid("values")
        cleaned[key] = _scalar_value(item)
    return cleaned


def _parse_telemetry(data: object) -> EngineTelemetry:
    body = _require_object(data, _TELEMETRY_FIELDS, "telemetry")
    return EngineTelemetry(
        command_count=_require_int(body["command_count"], "command_count", minimum=0),
        rejected_count=_require_int(body["rejected_count"], "rejected_count", minimum=0),
        coalesced_count=_require_int(body["coalesced_count"], "coalesced_count", minimum=0),
        dropped_context_count=_require_int(body["dropped_context_count"], "dropped_context_count", minimum=0),
        ack_count=_require_int(body["ack_count"], "ack_count", minimum=0),
    )


def _scalar_value(item: object) -> object:
    if isinstance(item, bool):
        return item
    if isinstance(item, (int, float)):
        if isinstance(item, bool) or not math.isfinite(float(item)):
            raise _invalid("values")
        return item
    if isinstance(item, str):
        if not 1 <= len(item) <= 80:
            raise _invalid("values")
        return item
    if isinstance(item, list):
        if not 1 <= len(item) <= 32:
            raise _invalid("values")
        cleaned: list[str] = []
        for entry in item:
            if not isinstance(entry, str) or not 1 <= len(entry) <= 80:
                raise _invalid("values")
            cleaned.append(entry)
        return cleaned
    raise _invalid("values")


def _require_object(
    data: object,
    allowed: frozenset[str],
    field: str,
    *,
    required: frozenset[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise _invalid(field)
    keys = set(data)
    extra = keys - allowed
    needed = allowed if required is None else required
    missing = needed - keys
    if extra or missing:
        raise _invalid(field)
    return data


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise _invalid(field)
    return value


def _optional_text(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _require_text(value, field)


def _require_choice(value: object, allowed: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise _invalid(field)
    return value


def _require_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid(field)
    return value


def _require_int(value: object, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _invalid(field)
    return value


def _require_unit(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(field)
    number = float(value)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise _invalid(field)
    return number


def _invalid(field: str) -> AdaptiveClientError:
    return AdaptiveClientError("engine_payload_invalid", f"invalid {field}")
