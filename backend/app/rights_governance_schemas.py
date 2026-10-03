"""Strict ``rights.registry`` and ``model.data.provenance`` contracts.

Rights entries name permission-to-use. They are not a score and they are not
``composition.v5``. Note events, pitch lists, WAV/PCM bytes, and prompts never
belong here. Content provenance remains the derivation DAG — complementary.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.dataset.schemas import DatasetProvenance, ProvenanceStatus

logger = logging.getLogger(__name__)

RIGHTS_ENTRY_SCHEMA: Literal["rights.registry.entry.v1"] = "rights.registry.entry.v1"
MODEL_DATA_PROVENANCE_SCHEMA: Literal["model.data.provenance.manifest.v1"] = (
    "model.data.provenance.manifest.v1"
)

ENTRY_ID_RE = re.compile(r"^rights_[0-9a-f]{16}$")
FINGERPRINT_PREFIX_RE = re.compile(r"^[0-9a-fA-F]{1,40}$")

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
        "prompt",
        "prompts",
    }
)

OwnershipClass = Literal["public_domain", "user_owned", "licensed", "unknown"]
UsePolicy = Literal["training_allowed", "reference_only", "no_training"]
VerificationStatus = Literal["unverified", "attested", "verified", "disputed"]
RightsSourceKind = Literal[
    "dataset_item",
    "dataset_source",
    "project",
    "composition_revision",
    "external_file",
    "personal_snapshot_project",
]
AllowedUse = Literal["train", "reference_analyze", "eval_holdout"]
RestrictionCode = Literal[
    "no_redistribution",
    "attribution_required",
    "share_alike",
    "evaluation_only",
]
RightsUseKind = Literal["train", "reference_analyze"]
ModelDataManifestKind = Literal[
    "dataset_train_split",
    "personal_adapter_train",
    "music_transformer_train",
]

USE_POLICY_ALIASES: frozenset[str] = frozenset(
    {"training_allowed", "reference_only", "no_training"}
)

RIGHTS_GOVERNANCE_ERROR_CODES: dict[str, str] = {
    "embedded_note_material": "Rights documents cannot embed note events, PCM, or prompts.",
    "rights_invalid": "Rights document failed schema validation.",
    "rights_license_required": "Licensed training_allowed entries require license or license_spdx.",
    "rights_train_refused": "This source is not eligible for training.",
    "rights_reference_refused": "This source is not eligible for reference analysis.",
    "rights_not_found": "Rights registry entry was not found.",
    "rights_cas_conflict": "Rights registry entry version conflict.",
    "rights_disputed": "Disputed rights entries refuse gated uses.",
}

_DIGEST_FIELDS: tuple[str, ...] = (
    "source_kind",
    "source_id",
    "ownership_class",
    "use_policy",
    "allowed_uses",
    "license",
    "license_spdx",
    "attribution",
    "source_url",
    "source_reference",
    "restrictions",
    "verification_status",
    "legacy_status",
)


def log_rights_schema_failure(model: str, code: str) -> None:
    """DEBUG schema rejection without attribution bodies or note fields."""
    logger.debug(
        "Rights governance schema rejected",
        extra={"model": model, "code": code},
    )


class RightsGovernanceError(Exception):
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


def map_rights_governance_error_to_http(exc: RightsGovernanceError) -> tuple[int, dict[str, Any]]:
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


def new_rights_entry_id() -> str:
    """Allocate a ``rights_<16 hex>`` id."""
    return f"rights_{secrets.token_hex(8)}"


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


def reject_embedded_note_material(payload: Any, *, model: str = "RightsRegistryEntryV1") -> None:
    """Raise when a raw tree contains note-event, PCM, or prompt keys."""
    hits = _scan_embedded(payload)
    if not hits:
        return
    log_rights_schema_failure(model, "embedded_note_material")
    raise RightsGovernanceError(
        "embedded_note_material",
        RIGHTS_GOVERNANCE_ERROR_CODES["embedded_note_material"],
        http_status=422,
        details={"hit_count": len(hits)},
    )


def project_allowed_uses(
    use_policy: UsePolicy,
    *,
    include_non_trainable_in_eval: bool = False,
) -> list[AllowedUse]:
    """Part I.3 allowed-uses projection from use_policy."""
    if use_policy == "training_allowed":
        return ["train", "reference_analyze", "eval_holdout"]
    if use_policy == "reference_only":
        return ["reference_analyze"]
    if use_policy == "no_training":
        if include_non_trainable_in_eval:
            return ["eval_holdout"]
        return []
    return []


def compute_rights_digest(fields: dict[str, Any]) -> str:
    """SHA-256 hex digest over canonical rights fields (full hex)."""
    payload = {key: fields.get(key) for key in _DIGEST_FIELDS}
    if isinstance(payload.get("allowed_uses"), list):
        payload["allowed_uses"] = sorted(str(item) for item in payload["allowed_uses"])
    if isinstance(payload.get("restrictions"), list):
        payload["restrictions"] = sorted(str(item) for item in payload["restrictions"])
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def rights_digest_prefix(digest: str, *, length: int = 40) -> str:
    return digest[:length]


def map_legacy_dataset_provenance(
    provenance: DatasetProvenance,
    *,
    use_policy_override: UsePolicy | str | None = None,
    source_kind: RightsSourceKind = "project",
    source_id: str = "unknown",
    source_fingerprint_prefix: str | None = None,
    entry_id: str | None = None,
    attribution: str | None = None,
    restrictions: list[RestrictionCode] | None = None,
    include_non_trainable_in_eval: bool = False,
) -> RightsRegistryEntryV1:
    """Part H mapping from legacy ``DatasetProvenance`` into a registry entry.

    Explicit ``use_policy_override`` (sidecar ``use_policy``) wins over the
    default train mapping from ``status``. Never invents ``training_allowed``
    when override is ``reference_only`` / ``no_training``.
    """
    status: ProvenanceStatus = provenance.status
    ownership: OwnershipClass
    use_policy: UsePolicy
    verification: VerificationStatus

    if status == "public_domain":
        ownership = "public_domain"
        use_policy = "training_allowed"
        has_ref = bool(provenance.source_url or provenance.source_reference)
        verification = "verified" if has_ref else "unverified"
    elif status == "user_owned":
        ownership = "user_owned"
        if provenance.user_owned_attested:
            use_policy = "training_allowed"
            verification = "attested"
        else:
            use_policy = "no_training"
            verification = "unverified"
    elif status == "verified_redistributable":
        ownership = "licensed"
        use_policy = "training_allowed"
        verification = "verified"
    elif status == "restricted":
        ownership = "licensed" if (provenance.license or provenance.license_spdx) else "unknown"
        use_policy = "no_training"
        has_ref = bool(
            provenance.license
            or provenance.license_spdx
            or provenance.source_url
            or provenance.source_reference
        )
        verification = "verified" if has_ref else "unverified"
    else:
        ownership = "unknown"
        use_policy = "no_training"
        verification = "unverified"

    if use_policy_override is not None:
        override = str(use_policy_override).strip()
        if override not in USE_POLICY_ALIASES:
            log_rights_schema_failure("map_legacy_dataset_provenance", "rights_invalid")
            raise RightsGovernanceError(
                "rights_invalid",
                RIGHTS_GOVERNANCE_ERROR_CODES["rights_invalid"],
                http_status=422,
                details={"field": "use_policy"},
            )
        use_policy = override  # type: ignore[assignment]

    attr = attribution
    if attr is None:
        attr = provenance.composer or provenance.author

    entry = RightsRegistryEntryV1(
        entry_id=entry_id or new_rights_entry_id(),
        source_kind=source_kind,
        source_id=source_id,
        source_fingerprint_prefix=source_fingerprint_prefix,
        ownership_class=ownership,
        use_policy=use_policy,
        allowed_uses=project_allowed_uses(
            use_policy,
            include_non_trainable_in_eval=include_non_trainable_in_eval,
        ),
        license=provenance.license,
        license_spdx=provenance.license_spdx,
        attribution=attr,
        source_url=provenance.source_url,
        source_reference=provenance.source_reference,
        restrictions=list(restrictions or []),
        verification_status=verification,
        legacy_status=status,
    )
    logger.debug(
        "Mapped legacy dataset provenance",
        extra={
            "source_kind": source_kind,
            "source_id_prefix": source_id[:12],
            "ownership_class": ownership,
            "use_policy": use_policy,
            "legacy_status": status,
        },
    )
    return entry


def map_legacy_status_fields(
    *,
    status: str,
    user_owned_attested: bool = False,
    license: str | None = None,
    license_spdx: str | None = None,
    source_url: str | None = None,
    source_reference: str | None = None,
    composer: str | None = None,
    author: str | None = None,
    use_policy_override: UsePolicy | str | None = None,
    source_kind: RightsSourceKind = "project",
    source_id: str = "unknown",
    source_fingerprint_prefix: str | None = None,
    entry_id: str | None = None,
    include_non_trainable_in_eval: bool = False,
) -> RightsRegistryEntryV1:
    """Map Part H from raw status fields without requiring DatasetProvenance validation.

    Supports ``user_owned`` without attestation (→ ``no_training``) and alias
    ``use_policy`` strings when ownership defaults to ``unknown``.
    """
    cleaned_status = str(status).strip()
    if cleaned_status in USE_POLICY_ALIASES and use_policy_override is None:
        # Alias-as-status: treat as use_policy with ownership unknown unless overridden.
        use_policy_override = cleaned_status
        cleaned_status = "unknown"

    payload: dict[str, Any] = {
        "status": cleaned_status if cleaned_status in {
            "verified_redistributable",
            "user_owned",
            "public_domain",
            "restricted",
            "unknown",
        } else "unknown",
        "license": license,
        "license_spdx": license_spdx,
        "source_url": source_url,
        "source_reference": source_reference,
        "composer": composer,
        "author": author,
        "user_owned_attested": bool(user_owned_attested),
    }
    # Bypass DatasetProvenance attestation gate for the unattested user_owned row.
    if payload["status"] == "user_owned" and not payload["user_owned_attested"]:
        ownership: OwnershipClass = "user_owned"
        use_policy: UsePolicy = "no_training"
        verification: VerificationStatus = "unverified"
        if use_policy_override is not None:
            override = str(use_policy_override).strip()
            if override not in USE_POLICY_ALIASES:
                raise RightsGovernanceError(
                    "rights_invalid",
                    RIGHTS_GOVERNANCE_ERROR_CODES["rights_invalid"],
                    http_status=422,
                    details={"field": "use_policy"},
                )
            use_policy = override  # type: ignore[assignment]
        return RightsRegistryEntryV1(
            entry_id=entry_id or new_rights_entry_id(),
            source_kind=source_kind,
            source_id=source_id,
            source_fingerprint_prefix=source_fingerprint_prefix,
            ownership_class=ownership,
            use_policy=use_policy,
            allowed_uses=project_allowed_uses(
                use_policy,
                include_non_trainable_in_eval=include_non_trainable_in_eval,
            ),
            license=license,
            license_spdx=license_spdx,
            attribution=composer or author,
            source_url=source_url,
            source_reference=source_reference,
            restrictions=[],
            verification_status=verification,
            legacy_status="user_owned",
        )

    try:
        provenance = DatasetProvenance.model_validate(payload)
    except ValidationError as exc:
        log_rights_schema_failure("map_legacy_status_fields", "rights_invalid")
        raise RightsGovernanceError(
            "rights_invalid",
            RIGHTS_GOVERNANCE_ERROR_CODES["rights_invalid"],
            http_status=422,
        ) from exc
    return map_legacy_dataset_provenance(
        provenance,
        use_policy_override=use_policy_override,
        source_kind=source_kind,
        source_id=source_id,
        source_fingerprint_prefix=source_fingerprint_prefix,
        entry_id=entry_id,
        include_non_trainable_in_eval=include_non_trainable_in_eval,
    )


def synthetic_unknown_entry(
    *,
    source_kind: RightsSourceKind,
    source_id: str,
) -> RightsRegistryEntryV1:
    """Part K.1c fail-closed synthetic entry — never training_allowed."""
    return RightsRegistryEntryV1(
        entry_id=new_rights_entry_id(),
        source_kind=source_kind,
        source_id=source_id,
        ownership_class="unknown",
        use_policy="no_training",
        allowed_uses=[],
        verification_status="unverified",
        legacy_status="unknown",
    )


class RightsRegistryEntryV1(_Strict):
    """Durable non-playable rights registry entry."""

    schema_version: Literal["rights.registry.entry.v1"] = RIGHTS_ENTRY_SCHEMA
    entry_id: str = Field(..., pattern=ENTRY_ID_RE.pattern)
    source_kind: RightsSourceKind
    source_id: str = Field(..., min_length=1, max_length=240)
    source_fingerprint_prefix: str | None = Field(default=None, max_length=40)
    ownership_class: OwnershipClass
    use_policy: UsePolicy
    allowed_uses: list[AllowedUse] = Field(default_factory=list)
    license: str | None = Field(default=None, max_length=240)
    license_spdx: str | None = Field(default=None, max_length=64)
    attribution: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=2000)
    source_reference: str | None = Field(default=None, max_length=500)
    restrictions: list[RestrictionCode] = Field(default_factory=list)
    verification_status: VerificationStatus = "unverified"
    legacy_status: ProvenanceStatus | None = None
    rights_digest: str = Field(default="", max_length=64)
    created_at: str = Field(default_factory=_utc_now_iso, min_length=1, max_length=40)
    updated_at: str = Field(default_factory=_utc_now_iso, min_length=1, max_length=40)
    entry_version: int = Field(default=1, ge=1)

    @field_validator("source_id", "license", "license_spdx", "attribution", "source_url", "source_reference")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        return cleaned

    @field_validator("source_fingerprint_prefix")
    @classmethod
    def check_prefix(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not FINGERPRINT_PREFIX_RE.fullmatch(cleaned):
            raise ValueError("rights_invalid")
        return cleaned[:40]

    @field_validator("allowed_uses")
    @classmethod
    def normalize_allowed_uses(cls, value: list[AllowedUse]) -> list[AllowedUse]:
        seen: set[str] = set()
        ordered: list[AllowedUse] = []
        for item in value:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    @field_validator("restrictions")
    @classmethod
    def normalize_restrictions(cls, value: list[RestrictionCode]) -> list[RestrictionCode]:
        seen: set[str] = set()
        ordered: list[RestrictionCode] = []
        for item in value:
            if item in seen:
                continue
            seen.add(item)
            ordered.append(item)
        return ordered

    @model_validator(mode="after")
    def check_policy_consistency(self) -> RightsRegistryEntryV1:
        expected = project_allowed_uses(self.use_policy)
        # Allow eval_holdout extra when product gates used empty no_training set;
        # require reference_only never includes train.
        if self.use_policy == "reference_only" and "train" in self.allowed_uses:
            raise ValueError("rights_invalid")
        if self.use_policy == "training_allowed":
            for required in ("train", "reference_analyze"):
                if required not in self.allowed_uses:
                    # Auto-fill missing projection if caller omitted.
                    object.__setattr__(self, "allowed_uses", expected)
                    break
        if (
            self.ownership_class == "licensed"
            and self.use_policy == "training_allowed"
            and not (self.license or self.license_spdx)
        ):
            raise ValueError("rights_license_required")
        digest = compute_rights_digest(
            {
                "source_kind": self.source_kind,
                "source_id": self.source_id,
                "ownership_class": self.ownership_class,
                "use_policy": self.use_policy,
                "allowed_uses": list(self.allowed_uses),
                "license": self.license,
                "license_spdx": self.license_spdx,
                "attribution": self.attribution,
                "source_url": self.source_url,
                "source_reference": self.source_reference,
                "restrictions": list(self.restrictions),
                "verification_status": self.verification_status,
                "legacy_status": self.legacy_status,
            }
        )
        object.__setattr__(self, "rights_digest", digest)
        return self


class ModelDataProvenanceSourceV1(_Strict):
    """One train-eligible source row inside a model/data provenance manifest."""

    entry_id: str = Field(..., pattern=ENTRY_ID_RE.pattern)
    source_kind: RightsSourceKind
    source_id: str = Field(..., min_length=1, max_length=240)
    ownership_class: OwnershipClass
    use_policy: Literal["training_allowed"] = "training_allowed"
    rights_digest_prefix: str = Field(..., min_length=8, max_length=40)


class ModelDataProvenanceManifestV1(_Strict):
    """Train-source audit manifest — distinct from content.provenance.manifest.v1."""

    schema_version: Literal["model.data.provenance.manifest.v1"] = MODEL_DATA_PROVENANCE_SCHEMA
    manifest_kind: ModelDataManifestKind
    subject_id: str = Field(..., min_length=1, max_length=240)
    sources: list[ModelDataProvenanceSourceV1] = Field(default_factory=list)
    excluded_source_counts_by_use_policy: dict[str, int] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_utc_now_iso, min_length=1, max_length=40)
    manifest_digest_prefix: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def check_train_only_sources(self) -> ModelDataProvenanceManifestV1:
        for source in self.sources:
            if source.use_policy != "training_allowed":
                raise ValueError("rights_invalid")
        payload = {
            "manifest_kind": self.manifest_kind,
            "subject_id": self.subject_id,
            "sources": [
                s.model_dump(mode="json") for s in sorted(self.sources, key=lambda row: row.entry_id)
            ],
            "excluded_source_counts_by_use_policy": dict(
                sorted(self.excluded_source_counts_by_use_policy.items())
            ),
            "created_at": self.created_at,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()[:40]
        object.__setattr__(self, "manifest_digest_prefix", digest)
        return self


class RightsEvaluateRequestV1(_Strict):
    """Preview evaluate payload for ``POST /rights-governance/evaluate``."""

    use: RightsUseKind
    entry: RightsRegistryEntryV1 | None = None
    legacy: DatasetProvenance | None = None
    source_kind: RightsSourceKind | None = None
    source_id: str | None = Field(default=None, max_length=240)

    @model_validator(mode="after")
    def require_subject(self) -> RightsEvaluateRequestV1:
        if self.entry is None and self.legacy is None:
            raise ValueError("rights_invalid")
        return self


class RightsEvaluateResultV1(_Strict):
    allowed: bool
    code: str | None = Field(default=None, max_length=80)
    message: str = Field(default="", max_length=200)
    ownership_class: OwnershipClass | None = None
    use_policy: UsePolicy | None = None
    verification_status: VerificationStatus | None = None
    entry_id: str | None = Field(default=None, max_length=40)


class RightsGovernanceStatusV1(_Strict):
    """Caps for ``GET /rights-governance/status`` only — never ``/ready``."""

    registry_enabled: bool = True
    max_entries_per_project: int = 256
    max_attribution_chars: int = 500


def _validation_code(exc: ValidationError) -> str:
    text = str(exc)
    for code in (
        "rights_license_required",
        "embedded_note_material",
        "rights_invalid",
    ):
        if code in text:
            return code
    return "rights_invalid"


def _map_validation(model: str, exc: ValidationError) -> RightsGovernanceError:
    code = _validation_code(exc)
    log_rights_schema_failure(model, code)
    return RightsGovernanceError(
        code,
        RIGHTS_GOVERNANCE_ERROR_CODES.get(code, RIGHTS_GOVERNANCE_ERROR_CODES["rights_invalid"]),
        http_status=422,
    )


def _parse(model_name: str, model: type[BaseModel], data: dict[str, Any]) -> BaseModel:
    if not isinstance(data, dict):
        log_rights_schema_failure(model_name, "rights_invalid")
        raise RightsGovernanceError(
            "rights_invalid",
            RIGHTS_GOVERNANCE_ERROR_CODES["rights_invalid"],
            http_status=422,
        )
    reject_embedded_note_material(data, model=model_name)
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise _map_validation(model_name, exc) from exc


def parse_rights_registry_entry(data: dict[str, Any]) -> RightsRegistryEntryV1:
    """Parse and validate a rights registry entry document."""
    result = _parse("RightsRegistryEntryV1", RightsRegistryEntryV1, data)
    assert isinstance(result, RightsRegistryEntryV1)
    return result


def parse_model_data_provenance_manifest(data: dict[str, Any]) -> ModelDataProvenanceManifestV1:
    """Parse and validate a model/data provenance manifest."""
    result = _parse("ModelDataProvenanceManifestV1", ModelDataProvenanceManifestV1, data)
    assert isinstance(result, ModelDataProvenanceManifestV1)
    return result
