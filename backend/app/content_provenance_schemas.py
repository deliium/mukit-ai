"""Strict ``content.provenance`` contracts.

Records name derivation lineage. They are not a score and they are not
``composition.v5``. Note events, pitch lists, WAV/PCM bytes, and prompts never
belong here. C2PA Content Credentials are an optional egress projection —
``honesty.cryptographic`` is true only via the locked Part K formula.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

logger = logging.getLogger(__name__)

PROVENANCE_RECORD_SCHEMA: Literal["content.provenance.record.v1"] = "content.provenance.record.v1"
PROVENANCE_CHAIN_SCHEMA: Literal["content.provenance.chain.v1"] = "content.provenance.chain.v1"
PROVENANCE_MANIFEST_SCHEMA: Literal["content.provenance.manifest.v1"] = "content.provenance.manifest.v1"
CREDENTIALS_STATUS_SCHEMA: Literal["content.credentials.status.v1"] = "content.credentials.status.v1"

FORBIDDEN_NOTE_KEYS: frozenset[str] = frozenset(
    {
        "events",
        "notes",
        "pitch",
        "pitches",
        "midi_events",
        "composition",
        "composition_json",
        "wav",
        "pcm",
    }
)

RECORD_ID_RE = re.compile(r"^cprov_[0-9a-f]{16}$")
FINGERPRINT_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{1,40}$")

ProvenanceArtifactKind = Literal[
    "composition_revision",
    "neural_render",
    "neural_stem",
    "neural_stem_set",
    "mix_plan_revision",
    "import_session",
    "audio_recovery_bind",
    "agent_artifact",
    "personal_adapter",
    "composer_profile",
    "reference_project",
    "asset_pack",
    "external_file",
]

ProvenanceOperation = Literal[
    "human_edit",
    "ai_generate",
    "ai_edit_region",
    "ai_arrange",
    "ai_develop",
    "ai_motif_apply",
    "universe_theme_reuse",
    "import_midi",
    "import_musicxml",
    "transcription_apply",
    "recovery_bind",
    "reference_condition",
    "personal_adapter_generate",
    "neural_render",
    "neural_stem",
    "neural_stem_rerender",
    "mix_plan_apply",
    "asset_pack_generate",
    "asset_pack_slot_regenerate",
    "performance_realize",
    "spatial_compile",
]

ProvenanceActorKind = Literal["human", "ai", "import", "system"]
ProvenanceTrustClass = Literal["mukit_internal", "c2pa_signed", "unavailable"]

CONTENT_PROVENANCE_ERROR_CODES: dict[str, str] = {
    "embedded_note_material": "Provenance documents cannot embed note events or PCM.",
    "provenance_invalid": "Provenance document failed schema validation.",
    "provenance_cycle": "This parent link would close a directed cycle.",
    "provenance_parent_cap": "Parent record ids exceed the cap of 8.",
    "provenance_project_cap": "The project is at its provenance record cap.",
    "provenance_not_found": "Provenance record or artifact was not found.",
    "provenance_fingerprint_unusable": "Fingerprint prefix is unusable.",
    "provenance_honesty_invalid": "Manifest honesty block violates the locked cryptographic formula.",
    "content_credentials_disabled": "Content Credentials are disabled.",
    "c2pa_library_unavailable": "C2PA library is not available.",
    "content_credentials_failed": "Content Credentials attach failed.",
}

_AI_OPERATIONS: frozenset[str] = frozenset(
    {
        "ai_generate",
        "ai_edit_region",
        "ai_arrange",
        "ai_develop",
        "ai_motif_apply",
        "reference_condition",
        "personal_adapter_generate",
        "neural_render",
        "neural_stem",
        "neural_stem_rerender",
    }
)


def log_provenance_schema_failure(model: str, code: str) -> None:
    """DEBUG schema rejection without labels or note fields."""
    logger.debug(
        "Content provenance schema rejected",
        extra={"model": model, "code": code},
    )


class ContentProvenanceError(Exception):
    """Domain error mapped to a structured HTTP detail."""

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
        self.message = message[:200]
        self.http_status = http_status
        self.details = details or {}


def map_content_provenance_error_to_http(exc: ContentProvenanceError) -> tuple[int, dict[str, Any]]:
    """Map domain errors to ``(status, detail)`` for HTTPException."""
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": exc.message,
    }
    if exc.details:
        safe: dict[str, Any] = {}
        for key, value in exc.details.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, list) and all(isinstance(item, str) for item in value):
                safe[key] = value[:16]
        if safe:
            detail["details"] = safe
    return int(exc.http_status), detail


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _scan_embedded(payload: Any) -> list[str]:
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if key_text in FORBIDDEN_NOTE_KEYS:
                found.append(key_text)
            found.extend(_scan_embedded(value))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(_scan_embedded(item))
    return found


def reject_embedded_note_material(payload: Any, *, model: str = "ContentProvenanceRecordV1") -> None:
    """Raise when a raw tree contains note-event or PCM keys. Does not log the tree."""
    hits = _scan_embedded(payload)
    if not hits:
        return
    log_provenance_schema_failure(model, "embedded_note_material")
    raise ContentProvenanceError(
        "embedded_note_material",
        CONTENT_PROVENANCE_ERROR_CODES["embedded_note_material"],
        http_status=422,
        details={"hit_count": len(hits)},
    )


def compute_honesty_cryptographic(
    *,
    c2pa_attached: bool,
    c2pa_fake_mode: bool,
    records: list[Any],
) -> bool:
    """Locked Part K formula: attached ∧ ¬fake_mode ∧ any(trust_class == c2pa_signed)."""
    if not c2pa_attached or c2pa_fake_mode:
        return False
    for record in records:
        trust = getattr(record, "trust_class", None)
        if trust is None and isinstance(record, dict):
            trust = record.get("trust_class")
        if trust == "c2pa_signed":
            return True
    return False


class ProvenanceParentArtifactV1(_Strict):
    """Display parent when the parent record was GC'd or never captured."""

    kind: ProvenanceArtifactKind
    id: str = Field(..., min_length=1, max_length=120)
    fingerprint_prefix: str | None = Field(default=None, max_length=40)

    @field_validator("id")
    @classmethod
    def strip_id(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("provenance_invalid")
        return cleaned

    @field_validator("fingerprint_prefix")
    @classmethod
    def check_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not FINGERPRINT_PREFIX_RE.fullmatch(cleaned):
            raise ValueError("provenance_fingerprint_unusable")
        return cleaned[:40]


class ContentProvenanceRecordV1(_Strict):
    """One DAG node. Ids, operation, models, parents, timestamps — never events/PCM/prompts."""

    schema_version: Literal["content.provenance.record.v1"] = PROVENANCE_RECORD_SCHEMA
    record_id: str = Field(..., pattern=RECORD_ID_RE.pattern)
    project_id: str | None = Field(default=None, min_length=1, max_length=64)
    artifact_kind: ProvenanceArtifactKind
    artifact_id: str = Field(..., min_length=1, max_length=120)
    artifact_fingerprint_prefix: str | None = Field(default=None, max_length=40)
    operation: ProvenanceOperation
    model_id: str | None = Field(default=None, max_length=160)
    model_version: str | None = Field(default=None, max_length=120)
    runtime: str | None = Field(default=None, max_length=64)
    user_action: str | None = Field(default=None, max_length=64)
    actor_kind: ProvenanceActorKind
    parent_record_ids: list[str] = Field(default_factory=list)
    parent_artifacts: list[ProvenanceParentArtifactV1] = Field(default_factory=list, max_length=8)
    source_generation_provenance: dict[str, Any] | None = None
    trust_class: ProvenanceTrustClass = "mukit_internal"
    created_at: str = Field(default_factory=_utc_now_iso, min_length=1, max_length=40)

    @field_validator("artifact_id", "project_id", "model_id", "model_version", "runtime", "user_action")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("provenance_invalid")
        return cleaned

    @field_validator("artifact_fingerprint_prefix")
    @classmethod
    def check_artifact_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not FINGERPRINT_PREFIX_RE.fullmatch(cleaned):
            raise ValueError("provenance_fingerprint_unusable")
        return cleaned[:40]

    @field_validator("parent_record_ids")
    @classmethod
    def check_parent_ids(cls, value: list[str]) -> list[str]:
        if len(value) > 8:
            raise ValueError("provenance_parent_cap")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if not text or not RECORD_ID_RE.fullmatch(text):
                raise ValueError("provenance_invalid")
            if text in seen:
                continue
            seen.add(text)
            cleaned.append(text)
        return cleaned

    @field_validator("user_action")
    @classmethod
    def refuse_download_action(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.strip().lower() in {"download_manifest", "download_chain"}:
            raise ValueError("provenance_invalid")
        return value

    @model_validator(mode="after")
    def check_self_parent(self) -> ContentProvenanceRecordV1:
        if self.record_id in self.parent_record_ids:
            raise ValueError("provenance_cycle")
        if self.source_generation_provenance is not None and self.operation not in _AI_OPERATIONS:
            # Compact nest only when the operation is AI-shaped.
            raise ValueError("provenance_invalid")
        return self


class ProvenanceChainDiagnosticV1(_Strict):
    """Codes only — never full payloads."""

    code: str = Field(..., min_length=1, max_length=80)
    message: str = Field(default="", max_length=200)
    record_id: str | None = Field(default=None, max_length=40)


class ContentProvenanceChainV1(_Strict):
    """Assembled walk from a leaf toward roots for UI."""

    schema_version: Literal["content.provenance.chain.v1"] = PROVENANCE_CHAIN_SCHEMA
    project_id: str = Field(..., min_length=1, max_length=64)
    leaf_artifact_kind: ProvenanceArtifactKind
    leaf_artifact_id: str = Field(..., min_length=1, max_length=120)
    records: list[ContentProvenanceRecordV1] = Field(default_factory=list, max_length=64)
    diagnostics: list[ProvenanceChainDiagnosticV1] = Field(default_factory=list, max_length=32)


class ProvenanceC2paHonestyV1(_Strict):
    attached: bool = False
    fake_mode: bool = False
    status: str | None = Field(default=None, max_length=64)
    reason_code: str | None = Field(default=None, max_length=80)


class ProvenanceHonestyV1(_Strict):
    """Cryptographic honesty block. Part K formula is enforced on the parent manifest."""

    cryptographic: bool = False
    c2pa: ProvenanceC2paHonestyV1 = Field(default_factory=ProvenanceC2paHonestyV1)


class ContentProvenanceManifestV1(_Strict):
    """Exportable envelope for a leaf asset."""

    schema_version: Literal["content.provenance.manifest.v1"] = PROVENANCE_MANIFEST_SCHEMA
    project_id: str = Field(..., min_length=1, max_length=64)
    leaf_artifact_kind: ProvenanceArtifactKind
    leaf_artifact_id: str = Field(..., min_length=1, max_length=120)
    leaf_fingerprint_prefix: str | None = Field(default=None, max_length=40)
    records: list[ContentProvenanceRecordV1] = Field(default_factory=list, max_length=64)
    honesty: ProvenanceHonestyV1 = Field(default_factory=ProvenanceHonestyV1)
    manifest_digest_prefix: str | None = Field(default=None, max_length=40)
    assembled_at: str = Field(default_factory=_utc_now_iso, min_length=1, max_length=40)
    diagnostics: list[ProvenanceChainDiagnosticV1] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def check_honesty_formula(self) -> ContentProvenanceManifestV1:
        expected = compute_honesty_cryptographic(
            c2pa_attached=self.honesty.c2pa.attached,
            c2pa_fake_mode=self.honesty.c2pa.fake_mode,
            records=self.records,
        )
        if self.honesty.cryptographic != expected:
            raise ValueError("provenance_honesty_invalid")
        if self.honesty.cryptographic and (
            self.honesty.c2pa.fake_mode or not self.honesty.c2pa.attached
        ):
            raise ValueError("provenance_honesty_invalid")
        return self


class ContentCredentialsStatusV1(_Strict):
    """Optional C2PA attempt result. May set ``fake_mode``; never upgrades records alone."""

    schema_version: Literal["content.credentials.status.v1"] = CREDENTIALS_STATUS_SCHEMA
    enabled: bool = False
    fake_mode: bool = False
    attached: bool = False
    status: str = Field(default="disabled", max_length=64)
    reason_code: str | None = Field(default=None, max_length=80)
    artifact_kind: ProvenanceArtifactKind | None = None
    artifact_id: str | None = Field(default=None, max_length=120)
    credential_path_prefix: str | None = Field(default=None, max_length=80)


class ContentProvenanceStatusV1(_Strict):
    """Flags for ``GET /content-provenance/status`` only — never ``/ready``."""

    provenance_enabled: bool = True
    credentials_enabled: bool = False
    credentials_fake: bool = False


def _validation_code(exc: ValidationError) -> str:
    text = str(exc)
    for code in (
        "provenance_honesty_invalid",
        "provenance_parent_cap",
        "provenance_cycle",
        "provenance_fingerprint_unusable",
        "embedded_note_material",
        "provenance_invalid",
    ):
        if code in text:
            return code
    return "provenance_invalid"


def _map_validation(model: str, exc: ValidationError) -> ContentProvenanceError:
    code = _validation_code(exc)
    log_provenance_schema_failure(model, code)
    return ContentProvenanceError(
        code,
        CONTENT_PROVENANCE_ERROR_CODES.get(code, CONTENT_PROVENANCE_ERROR_CODES["provenance_invalid"]),
        http_status=422,
    )


def _parse(model_name: str, model: type[BaseModel], data: dict[str, Any]) -> BaseModel:
    if not isinstance(data, dict):
        log_provenance_schema_failure(model_name, "provenance_invalid")
        raise ContentProvenanceError(
            "provenance_invalid",
            CONTENT_PROVENANCE_ERROR_CODES["provenance_invalid"],
            http_status=422,
        )
    reject_embedded_note_material(data, model=model_name)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise _map_validation(model_name, exc) from exc


def parse_content_provenance_record(data: dict[str, Any]) -> ContentProvenanceRecordV1:
    """Parse and validate a provenance record document."""
    result = _parse("ContentProvenanceRecordV1", ContentProvenanceRecordV1, data)
    assert isinstance(result, ContentProvenanceRecordV1)
    return result


def parse_content_provenance_chain(data: dict[str, Any]) -> ContentProvenanceChainV1:
    result = _parse("ContentProvenanceChainV1", ContentProvenanceChainV1, data)
    assert isinstance(result, ContentProvenanceChainV1)
    return result


def parse_content_provenance_manifest(data: dict[str, Any]) -> ContentProvenanceManifestV1:
    result = _parse("ContentProvenanceManifestV1", ContentProvenanceManifestV1, data)
    assert isinstance(result, ContentProvenanceManifestV1)
    return result


def parse_content_credentials_status(data: dict[str, Any]) -> ContentCredentialsStatusV1:
    result = _parse("ContentCredentialsStatusV1", ContentCredentialsStatusV1, data)
    assert isinstance(result, ContentCredentialsStatusV1)
    return result
