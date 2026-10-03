"""Strict Ardour companion DTOs (``ardour.companion.*.v1``).

Process-memory OSC companion only. Never embeds note events or Composition.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

ARDOUR_COMPANION_CONNECT_SCHEMA: Literal["ardour.companion.connect.v1"] = (
    "ardour.companion.connect.v1"
)
ARDOUR_COMPANION_SESSION_SCHEMA: Literal["ardour.companion.session.v1"] = (
    "ardour.companion.session.v1"
)
ARDOUR_COMPANION_STATUS_SCHEMA: Literal["ardour.companion.status.v1"] = (
    "ardour.companion.status.v1"
)

_SESSION_ID_RE = re.compile(r"^ardc_[0-9a-f]{8}$")

ArdourConnectionState = Literal[
    "disabled",
    "disconnected",
    "connecting",
    "awaiting_feedback",
    "connected",
    "stale",
    "error",
]

ArdourHostClass = Literal["loopback", "private", "public", "link_local", "multicast", "unspecified", "unknown"]

ARDOUR_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ardour_companion_disabled",
        "ardour_control_permission_required",
        "ardour_host_refused",
        "ardour_not_connected",
        "ardour_feedback_stale",
        "ardour_payload_invalid",
        "ardour_osc_send_failed",
        "ardour_bind_failed",
    }
)

ARDOUR_ERROR_MESSAGES: dict[str, str] = {
    "ardour_companion_disabled": "Ardour companion is disabled.",
    "ardour_control_permission_required": "Explicit DAW control permission is required.",
    "ardour_host_refused": "OSC host is outside the allowlist.",
    "ardour_not_connected": "No active Ardour companion session.",
    "ardour_feedback_stale": "Ardour feedback is missing or stale.",
    "ardour_payload_invalid": "Ardour companion request body is invalid.",
    "ardour_osc_send_failed": "Failed to send OSC datagram to Ardour.",
    "ardour_bind_failed": "Failed to bind the OSC feedback listen port.",
}

ARDOUR_ERROR_HTTP: dict[str, int] = {
    "ardour_companion_disabled": 403,
    "ardour_control_permission_required": 422,
    "ardour_host_refused": 422,
    "ardour_not_connected": 409,
    "ardour_feedback_stale": 409,
    "ardour_payload_invalid": 422,
    "ardour_osc_send_failed": 502,
    "ardour_bind_failed": 502,
}


class ArdourCompanionError(Exception):
    """Domain error for Ardour companion routes and session service."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ARDOUR_ERROR_CODES:
            raise ValueError(f"unknown ardour companion error code: {code}")
        text = message if message is not None else ARDOUR_ERROR_MESSAGES[code]
        super().__init__(text)
        self.code = code
        self.message = text
        self.http_status = ARDOUR_ERROR_HTTP.get(code, 422)
        self.details = details or {}


def map_ardour_error_to_http(exc: ArdourCompanionError) -> tuple[int, dict[str, Any]]:
    """Return ``(status, sanitized detail)`` for ``HTTPException``."""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc)[:240],
    }
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return exc.http_status, detail


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def classify_ip_host(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ArdourHostClass:
    """Return a coarse host class for logging (never the raw unexpected payload)."""
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link_local"
    if address.is_multicast:
        return "multicast"
    if address.is_unspecified:
        return "unspecified"
    if address.is_private:
        return "private"
    return "public"


def _ip_is_allowed(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allow_public: bool,
) -> bool:
    if address.is_loopback:
        return True
    if address.is_link_local or address.is_multicast or address.is_unspecified:
        return False
    if address.is_private:
        return True
    return allow_public


def validate_ardour_osc_host(
    host: str,
    *,
    allow_public_hosts: bool = False,
) -> tuple[str, ArdourHostClass]:
    """Validate an OSC target host against the ship-1 allowlist.

    Allows loopback and private addresses. Refuses link-local, multicast, and
    unspecified. Hostnames are allowed only when DNS yields an address that
    passes the same check (or public hosts are allowed).
    """
    text = (host or "").strip()
    if not text or len(text) > 253:
        logger.warning(
            "Ardour OSC host refused",
            extra={"code": "ardour_host_refused", "host_class": "unknown"},
        )
        raise ArdourCompanionError(
            "ardour_host_refused",
            details={"reason": "empty_or_too_long"},
        )

    host_lower = text.lower().rstrip(".")
    try:
        ip = ipaddress.ip_address(host_lower)
    except ValueError:
        ip = None

    if ip is not None:
        host_class = classify_ip_host(ip)
        if not _ip_is_allowed(ip, allow_public=allow_public_hosts):
            logger.warning(
                "Ardour OSC host refused",
                extra={"code": "ardour_host_refused", "host_class": host_class},
            )
            raise ArdourCompanionError(
                "ardour_host_refused",
                details={"reason": "ip", "host_class": host_class},
            )
        return text, host_class

    try:
        infos = socket.getaddrinfo(host_lower, None, type=socket.SOCK_DGRAM)
    except OSError:
        logger.warning(
            "Ardour OSC host refused",
            extra={"code": "ardour_host_refused", "host_class": "unknown"},
        )
        raise ArdourCompanionError(
            "ardour_host_refused",
            details={"reason": "dns"},
        ) from None

    resolved: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            resolved.append(ipaddress.ip_address(sockaddr[0]))
        except ValueError:
            continue
    if not resolved:
        logger.warning(
            "Ardour OSC host refused",
            extra={"code": "ardour_host_refused", "host_class": "unknown"},
        )
        raise ArdourCompanionError(
            "ardour_host_refused",
            details={"reason": "dns_empty"},
        )

    for address in resolved:
        if not _ip_is_allowed(address, allow_public=allow_public_hosts):
            host_class = classify_ip_host(address)
            logger.warning(
                "Ardour OSC host refused",
                extra={"code": "ardour_host_refused", "host_class": host_class},
            )
            raise ArdourCompanionError(
                "ardour_host_refused",
                details={"reason": "resolved_ip", "host_class": host_class},
            )

    primary = classify_ip_host(resolved[0])
    logger.debug(
        "Ardour OSC hostname accepted",
        extra={"host_class": primary, "resolved_count": len(resolved)},
    )
    return text, primary


class ArdourCompanionConnectV1(_Strict):
    """``ardour.companion.connect.v1`` connect request body."""

    schema_version: Literal["ardour.companion.connect.v1"] = ARDOUR_COMPANION_CONNECT_SCHEMA
    host: str = Field(..., min_length=1, max_length=253)
    osc_port: int = Field(..., ge=1, le=65535)
    feedback_port: int = Field(..., ge=1, le=65535)
    control_permission: bool = False

    @field_validator("host")
    @classmethod
    def _strip_host(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("host must not be empty")
        return text

    @model_validator(mode="after")
    def _ports_distinct_from_default_osc(self) -> ArdourCompanionConnectV1:
        # Feedback must never bind Ardour's default OSC control port.
        if self.feedback_port == 3819:
            raise ValueError("feedback_port must not be 3819")
        if self.osc_port == self.feedback_port:
            raise ValueError("osc_port and feedback_port must differ")
        return self


class ArdourCompanionSessionV1(_Strict):
    """``ardour.companion.session.v1`` session echo after connect/disconnect."""

    schema_version: Literal["ardour.companion.session.v1"] = ARDOUR_COMPANION_SESSION_SCHEMA
    session_id: str
    host: str
    osc_port: int = Field(..., ge=1, le=65535)
    feedback_port: int = Field(..., ge=1, le=65535)
    control_permission: bool
    connection_state: ArdourConnectionState
    last_error_code: str | None = None
    fake: bool = False

    @field_validator("session_id")
    @classmethod
    def _session_id_shape(cls, value: str) -> str:
        if not _SESSION_ID_RE.fullmatch(value):
            raise ValueError("session_id must match ardc_<8 hex>")
        return value

    @field_validator("last_error_code")
    @classmethod
    def _error_code_known(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ARDOUR_ERROR_CODES:
            raise ValueError("unknown last_error_code")
        return value


class ArdourStripObservedV1(_Strict):
    """One feedback-observed mixer strip."""

    ssid: int = Field(..., ge=1, le=1024)
    name: str | None = None
    fader: float | None = Field(default=None, ge=0.0, le=1.0)
    pan: float | None = Field(default=None, ge=0.0, le=1.0)
    mute: int | None = Field(default=None, ge=0, le=1)
    solo: int | None = Field(default=None, ge=0, le=1)


class ArdourCompanionStatusV1(_Strict):
    """``ardour.companion.status.v1`` feedback-observed companion status."""

    schema_version: Literal["ardour.companion.status.v1"] = ARDOUR_COMPANION_STATUS_SCHEMA
    enabled: bool
    connection_state: ArdourConnectionState
    session_id: str | None = None
    host: str | None = None
    osc_port: int | None = Field(default=None, ge=1, le=65535)
    feedback_port: int | None = Field(default=None, ge=1, le=65535)
    control_permission: bool = False
    fake: bool = False
    transport_playing: bool | None = None
    locate_samples: int | None = Field(default=None, ge=0)
    last_requested_locate_samples: int | None = Field(default=None, ge=0)
    record_armed: bool | None = None
    selected_ssid: int | None = Field(default=None, ge=1, le=1024)
    strips: list[ArdourStripObservedV1] = Field(default_factory=list)
    feedback_age_ms: int | None = Field(default=None, ge=0)
    stale: bool = False
    last_error_code: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("last_error_code")
    @classmethod
    def _status_error_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value not in ARDOUR_ERROR_CODES:
            raise ValueError("unknown last_error_code")
        return value


class ArdourLocateRequestV1(_Strict):
    samples: int = Field(..., ge=0)
    roll: int = Field(0, ge=0, le=1)


class ArdourRecordArmRequestV1(_Strict):
    desired: bool


class ArdourStripFloatValueV1(_Strict):
    value: float = Field(..., ge=0.0, le=1.0)


class ArdourStripBoolValueV1(_Strict):
    value: int = Field(..., ge=0, le=1)


class ArdourCommandAcceptedV1(_Strict):
    """Mutating command response: accepted send + current feedback status."""

    accepted: bool
    command: Literal[
        "play",
        "stop",
        "locate",
        "record_arm",
        "strip_fader",
        "strip_pan",
        "strip_mute",
        "strip_solo",
        "connect",
        "disconnect",
    ]
    status: ArdourCompanionStatusV1
    warnings: list[str] = Field(default_factory=list)


class ArdourStripsResponseV1(_Strict):
    selected_ssid: int | None = None
    strips: list[ArdourStripObservedV1] = Field(default_factory=list)
    connection_state: ArdourConnectionState
    stale: bool = False
    feedback_age_ms: int | None = Field(default=None, ge=0)


class ArdourRecordStatusV1(_Strict):
    record_armed: bool | None = None
    connection_state: ArdourConnectionState
    stale: bool = False
    feedback_age_ms: int | None = Field(default=None, ge=0)
