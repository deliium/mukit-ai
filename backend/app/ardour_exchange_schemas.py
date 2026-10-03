"""Strict Ardour exchange DTOs (``ardour.exchange.*.v1``).

Non-playable package/manifest/context documents plus session preview that may
carry a draft ``composition.v2``. This module does not import FastAPI, SQLite
project_store, ``ai_agents/``, torch, or video modules.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.composition_schemas import CompositionV2, normalize_time_signature

logger = logging.getLogger(__name__)

MANIFEST_SCHEMA: Literal["ardour.exchange.manifest.v1"] = "ardour.exchange.manifest.v1"
PACKAGE_SCHEMA: Literal["ardour.exchange.package.v1"] = "ardour.exchange.package.v1"
SESSION_CONTEXT_SCHEMA: Literal["ardour.exchange.session_context.v1"] = (
    "ardour.exchange.session_context.v1"
)
PREVIEW_SCHEMA: Literal["ardour.exchange.preview.v1"] = "ardour.exchange.preview.v1"
PREPARE_REQUEST_SCHEMA: Literal["ardour.exchange.prepare.v1"] = "ardour.exchange.prepare.v1"
REALIZE_REQUEST_SCHEMA: Literal["ardour.exchange.realize_request.v1"] = (
    "ardour.exchange.realize_request.v1"
)

PACKAGE_ID_PATTERN = re.compile(r"^aex_[0-9a-f]{16}$")
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-fA-F._-]{16,128}$")
AUDIO_RELPATH_PATTERN = re.compile(r"^audio/[A-Za-z0-9._-]{1,120}\.wav$")
MATERIAL_RELPATH = "material.mid"

ArdourExchangeDirection = Literal["inbound", "outbound"]
ArdourExchangeRealizeIntent = Literal[
    "counter_melody",
    "arrangement_variation",
    "regenerate_region",
]
ArdourConnectionStateLite = Literal[
    "disabled",
    "disconnected",
    "connecting",
    "awaiting_feedback",
    "connected",
    "stale",
    "error",
]

FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
        "composition",
        "composition_json",
        "session_xml",
        "ardour_session",
        "prompt",
    }
)

ARDOUR_EXCHANGE_ERROR_CODES: frozenset[str] = frozenset(
    {
        "ardour_exchange_disabled",
        "ardour_exchange_root_unconfigured",
        "ardour_exchange_package_invalid",
        "ardour_exchange_package_too_large",
        "ardour_exchange_midi_invalid",
        "ardour_exchange_preview_missing",
        "ardour_exchange_alignment_invalid",
        "ardour_exchange_realize_unsupported",
        "ardour_exchange_stem_unavailable",
        "ardour_exchange_forbidden_payload",
        "ardour_exchange_path_escape",
        "ardour_exchange_not_found",
    }
)

ARDOUR_EXCHANGE_ERROR_MESSAGES: dict[str, str] = {
    "ardour_exchange_disabled": "Ardour exchange is disabled.",
    "ardour_exchange_root_unconfigured": "ARDOUR_EXCHANGE_ROOT is missing or refused.",
    "ardour_exchange_package_invalid": "Exchange package layout or manifest is invalid.",
    "ardour_exchange_package_too_large": "Exchange package exceeds the upload size limit.",
    "ardour_exchange_midi_invalid": "Exchange MIDI could not be imported.",
    "ardour_exchange_preview_missing": "No exchange preview is available.",
    "ardour_exchange_alignment_invalid": "Exchange alignment fields are inconsistent.",
    "ardour_exchange_realize_unsupported": "Unsupported exchange realize intent.",
    "ardour_exchange_stem_unavailable": "Optional stem is missing or incomplete.",
    "ardour_exchange_forbidden_payload": "Exchange document cannot carry that field.",
    "ardour_exchange_path_escape": "Exchange package path escape refused.",
    "ardour_exchange_not_found": "Exchange package was not found.",
}

ARDOUR_EXCHANGE_ERROR_HTTP: dict[str, int] = {
    "ardour_exchange_disabled": 403,
    "ardour_exchange_root_unconfigured": 503,
    "ardour_exchange_package_invalid": 422,
    "ardour_exchange_package_too_large": 413,
    "ardour_exchange_midi_invalid": 422,
    "ardour_exchange_preview_missing": 404,
    "ardour_exchange_alignment_invalid": 422,
    "ardour_exchange_realize_unsupported": 422,
    "ardour_exchange_stem_unavailable": 409,
    "ardour_exchange_forbidden_payload": 422,
    "ardour_exchange_path_escape": 422,
    "ardour_exchange_not_found": 404,
}


class ArdourExchangeError(Exception):
    """Domain error for Ardour exchange routes and services."""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if code not in ARDOUR_EXCHANGE_ERROR_CODES:
            raise ValueError(f"unknown ardour exchange error code: {code}")
        text = message if message is not None else ARDOUR_EXCHANGE_ERROR_MESSAGES[code]
        super().__init__(text)
        self.code = code
        self.message = str(text)[:240]
        self.http_status = ARDOUR_EXCHANGE_ERROR_HTTP.get(code, 422)
        self.details = details or {}


def map_ardour_exchange_error_to_http(exc: ArdourExchangeError) -> tuple[int, dict[str, Any]]:
    """Return ``(status, sanitized detail)`` for ``HTTPException``."""
    detail: dict[str, Any] = {"code": exc.code, "message": exc.message}
    if exc.details:
        safe = {
            key: value
            for key, value in exc.details.items()
            if isinstance(value, (str, int, float, bool)) or value is None
        }
        if safe:
            detail["details"] = safe
    return exc.http_status, detail


def scan_forbidden_exchange_keys(payload: Any) -> list[str]:
    """Return forbidden key names found anywhere in ``payload``."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_PAYLOAD_KEYS:
                found.append(key_text)
            found.extend(scan_forbidden_exchange_keys(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(scan_forbidden_exchange_keys(item))
    return found


def reject_exchange_forbidden_payload(payload: Any, *, model_name: str) -> None:
    """Raise when a request-body or manifest payload carries a forbidden key."""
    found = scan_forbidden_exchange_keys(payload)
    if not found:
        return
    logger.debug(
        "Ardour exchange forbidden payload",
        extra={"model_name": model_name, "code": "ardour_exchange_forbidden_payload"},
    )
    raise ArdourExchangeError(
        "ardour_exchange_forbidden_payload",
        details={"field_name": found[0]},
    )


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ForbiddenScanStrict(_Strict):
    """Strict models that refuse forbidden keys (manifest / prepare / ingest)."""

    @model_validator(mode="before")
    @classmethod
    def _reject_forbidden_keys(cls, value: Any) -> Any:
        if isinstance(value, dict):
            try:
                reject_exchange_forbidden_payload(value, model_name=cls.__name__)
            except ArdourExchangeError as exc:
                raise ValueError(exc.code) from exc
        return value


def _normalize_tempo_bpm(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("tempo_bpm must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("tempo_bpm must be finite")
    normalized = int(round(number))
    if normalized < 40 or normalized > 240:
        raise ValueError("tempo_bpm out of range")
    return normalized


class ArdourExchangeAlignmentEcho(_Strict):
    """Alignment fields echoed on preview (manifest authority)."""

    start_bar: int = Field(ge=1)
    bar_count: int = Field(ge=1, le=256)
    tempo_bpm: int = Field(ge=40, le=240)
    time_signature: str = Field(min_length=3, max_length=16)


class ArdourExchangeManifestV1(_ForbiddenScanStrict):
    """Package alignment and naming metadata — never a playable score."""

    schema_version: Literal["ardour.exchange.manifest.v1"] = MANIFEST_SCHEMA
    package_id: str = Field(min_length=20, max_length=20)
    direction: ArdourExchangeDirection
    track_name: str = Field(min_length=1, max_length=120)
    source_track_ssid: int | None = Field(default=None, ge=1, le=1024)
    tempo_bpm: int = Field(ge=40, le=240)
    time_signature: str = Field(min_length=3, max_length=16)
    ticks_per_quarter: int = Field(default=480, gt=0)
    start_bar: int = Field(ge=1)
    bar_count: int = Field(ge=1, le=256)
    start_samples: int = Field(ge=0)
    length_samples: int | None = Field(default=None, ge=0)
    sample_rate: int = Field(ge=8000, le=192000)
    material_relpath: Literal["material.mid"] = MATERIAL_RELPATH
    audio_relpaths: list[str] = Field(default_factory=list, max_length=16)
    source_fingerprint: str = Field(min_length=16, max_length=128)
    created_at: str = Field(min_length=8, max_length=64)

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        if not PACKAGE_ID_PATTERN.match(value):
            raise ValueError("package_id must match aex_<16 hex>")
        return value

    @field_validator("tempo_bpm", mode="before")
    @classmethod
    def _coerce_tempo(cls, value: Any) -> int:
        return _normalize_tempo_bpm(value)

    @field_validator("time_signature")
    @classmethod
    def _validate_meter(cls, value: str) -> str:
        return normalize_time_signature(value, model_name=cls.__name__)

    @field_validator("source_fingerprint")
    @classmethod
    def _validate_fingerprint(cls, value: str) -> str:
        text = value.strip()
        if not FINGERPRINT_PATTERN.match(text):
            raise ValueError("source_fingerprint shape invalid")
        return text

    @field_validator("audio_relpaths")
    @classmethod
    def _validate_audio_relpaths(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            text = str(item).strip()
            if not AUDIO_RELPATH_PATTERN.match(text):
                raise ValueError("audio_relpaths must be audio/*.wav")
            if ".." in text or text.startswith("/") or "\\" in text:
                raise ValueError("audio_relpaths path escape refused")
            cleaned.append(text)
        return cleaned


class ArdourExchangePackageV1(_ForbiddenScanStrict):
    """On-disk package listing row (no note events)."""

    schema_version: Literal["ardour.exchange.package.v1"] = PACKAGE_SCHEMA
    package_id: str = Field(min_length=20, max_length=20)
    direction: ArdourExchangeDirection
    track_name: str = Field(min_length=1, max_length=120)
    bar_count: int = Field(ge=1, le=256)
    byte_size: int = Field(ge=0)
    created_at: str = Field(min_length=8, max_length=64)

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        if not PACKAGE_ID_PATTERN.match(value):
            raise ValueError("package_id must match aex_<16 hex>")
        return value


class ArdourExchangeSessionContextV1(_Strict):
    """OSC-observed companion snapshot; not region-bar authority when a manifest exists."""

    schema_version: Literal["ardour.exchange.session_context.v1"] = SESSION_CONTEXT_SCHEMA
    connection_state: ArdourConnectionStateLite
    locate_samples: int | None = Field(default=None, ge=0)
    transport_playing: bool | None = None
    selected_ssid: int | None = Field(default=None, ge=1, le=1024)
    selected_strip_name: str | None = Field(default=None, max_length=120)
    sample_rate: int | None = Field(default=None, ge=8000, le=192000)
    tempo_bpm: int | None = Field(default=None, ge=40, le=240)
    stale: bool = False
    warnings: list[str] = Field(default_factory=list, max_length=32)


class ArdourExchangeImportReportSummary(_Strict):
    """Sanitized import codes only — never raw MIDI bytes."""

    issue_codes: list[str] = Field(default_factory=list, max_length=64)
    warning_codes: list[str] = Field(default_factory=list, max_length=64)
    note_count: int = Field(default=0, ge=0)
    track_count: int = Field(default=0, ge=0)


class ArdourExchangePreviewV1(_Strict):
    """Session-only ingest preview. May include playable ``draft_composition``."""

    schema_version: Literal["ardour.exchange.preview.v1"] = PREVIEW_SCHEMA
    preview_id: str = Field(min_length=8, max_length=40)
    package_id: str = Field(min_length=20, max_length=20)
    manifest: ArdourExchangeManifestV1
    session_context: ArdourExchangeSessionContextV1 | None = None
    draft_composition: CompositionV2
    import_report: ArdourExchangeImportReportSummary
    alignment: ArdourExchangeAlignmentEcho

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        if not PACKAGE_ID_PATTERN.match(value):
            raise ValueError("package_id must match aex_<16 hex>")
        return value


class ArdourExchangeIngestRequestV1(_ForbiddenScanStrict):
    """JSON ingest by package id under the exchange root."""

    package_id: str = Field(min_length=20, max_length=20)

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        if not PACKAGE_ID_PATTERN.match(value):
            raise ValueError("package_id must match aex_<16 hex>")
        return value


class ArdourExchangePrepareRequestV1(_ForbiddenScanStrict):
    """Build an outbound package from working V2 (or last draft) + alignment."""

    schema_version: Literal["ardour.exchange.prepare.v1"] = PREPARE_REQUEST_SCHEMA
    track_ids: list[str] = Field(default_factory=list, max_length=64)
    start_bar: int | None = Field(default=None, ge=1)
    bar_count: int | None = Field(default=None, ge=1, le=256)
    track_name: str | None = Field(default=None, min_length=1, max_length=120)
    stem_id: str | None = Field(default=None, min_length=1, max_length=80)
    # Alignment defaults copied from current preview when omitted.
    use_preview_alignment: bool = True


class ArdourExchangePrepareResultV1(_Strict):
    """Outbound prepare response (download path; no note events)."""

    schema_version: Literal["ardour.exchange.prepare.v1"] = PREPARE_REQUEST_SCHEMA
    package_id: str = Field(min_length=20, max_length=20)
    manifest: ArdourExchangeManifestV1
    download_path: str = Field(min_length=1, max_length=240)
    has_audio: bool = False
    byte_size: int = Field(ge=0)

    @field_validator("package_id")
    @classmethod
    def _validate_package_id(cls, value: str) -> str:
        if not PACKAGE_ID_PATTERN.match(value):
            raise ValueError("package_id must match aex_<16 hex>")
        return value


class ArdourExchangeRealizeRequestV1(_Strict):
    """Thin realize intent — maps to arrangement/development preview."""

    schema_version: Literal["ardour.exchange.realize_request.v1"] = REALIZE_REQUEST_SCHEMA
    intent: ArdourExchangeRealizeIntent
    instruction: str | None = Field(default=None, max_length=200)
    candidate_count: int = Field(default=1, ge=1, le=4)


class ArdourExchangeApplyResponseV1(_Strict):
    """SPA Apply payload. Server does not write ``projects.composition_json``."""

    composition: CompositionV2
    manifest: ArdourExchangeManifestV1
    import_report: ArdourExchangeImportReportSummary


class ArdourExchangeStatusV1(_Strict):
    """Always-200 exchange status snapshot."""

    enabled: bool
    root_configured: bool
    companion_connected: bool = False
    max_package_bytes: int = Field(ge=0)
    preview_present: bool = False
    package_count: int = Field(default=0, ge=0)
