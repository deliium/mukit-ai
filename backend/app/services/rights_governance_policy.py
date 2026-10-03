"""Pure rights-governance evaluator and Part K resolve.

No FastAPI, SQLite, or ``dataset.pipeline`` imports. Callers adapt I/O.
Rights gates hard-fail — unlike content-provenance soft-fail capture.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from pydantic import ValidationError

from app.dataset.schemas import DatasetProvenance
from app.rights_governance_schemas import (
    RIGHTS_GOVERNANCE_ERROR_CODES,
    RightsEvaluateResultV1,
    RightsGovernanceError,
    RightsRegistryEntryV1,
    RightsSourceKind,
    RightsUseKind,
    map_legacy_dataset_provenance,
    map_legacy_status_fields,
    synthetic_unknown_entry,
)

logger = logging.getLogger(__name__)


def evaluate_rights_use(
    entry: RightsRegistryEntryV1,
    use: RightsUseKind,
) -> RightsEvaluateResultV1:
    """Part I formulas. Hard-fail semantics via ``allowed=False`` + code."""
    logger.debug(
        "Evaluating rights use",
        extra={
            "entry_id": entry.entry_id,
            "use": use,
            "ownership_class": entry.ownership_class,
            "use_policy": entry.use_policy,
            "verification_status": entry.verification_status,
        },
    )
    if entry.verification_status == "disputed":
        logger.warning(
            "Rights use refused",
            extra={
                "code": "rights_disputed",
                "source_id_prefix": entry.source_id[:12],
                "use": use,
            },
        )
        return RightsEvaluateResultV1(
            allowed=False,
            code="rights_disputed",
            message=RIGHTS_GOVERNANCE_ERROR_CODES["rights_disputed"],
            ownership_class=entry.ownership_class,
            use_policy=entry.use_policy,
            verification_status=entry.verification_status,
            entry_id=entry.entry_id,
        )

    if use == "train":
        train_ok = (
            entry.use_policy == "training_allowed"
            and (
                entry.ownership_class != "user_owned"
                or entry.verification_status in {"attested", "verified"}
            )
            and "train" in entry.allowed_uses
        )
        if not train_ok:
            logger.warning(
                "Rights use refused",
                extra={
                    "code": "rights_train_refused",
                    "source_id_prefix": entry.source_id[:12],
                    "use_policy": entry.use_policy,
                },
            )
            return RightsEvaluateResultV1(
                allowed=False,
                code="rights_train_refused",
                message=RIGHTS_GOVERNANCE_ERROR_CODES["rights_train_refused"],
                ownership_class=entry.ownership_class,
                use_policy=entry.use_policy,
                verification_status=entry.verification_status,
                entry_id=entry.entry_id,
            )
        logger.debug(
            "Rights use allowed",
            extra={"use": "train", "use_policy": entry.use_policy, "entry_id": entry.entry_id},
        )
        return RightsEvaluateResultV1(
            allowed=True,
            code=None,
            message="",
            ownership_class=entry.ownership_class,
            use_policy=entry.use_policy,
            verification_status=entry.verification_status,
            entry_id=entry.entry_id,
        )

    # reference_analyze
    ref_ok = (
        entry.use_policy in {"training_allowed", "reference_only"}
        and "reference_analyze" in entry.allowed_uses
    )
    if not ref_ok:
        logger.warning(
            "Rights use refused",
            extra={
                "code": "rights_reference_refused",
                "source_id_prefix": entry.source_id[:12],
                "use_policy": entry.use_policy,
            },
        )
        return RightsEvaluateResultV1(
            allowed=False,
            code="rights_reference_refused",
            message=RIGHTS_GOVERNANCE_ERROR_CODES["rights_reference_refused"],
            ownership_class=entry.ownership_class,
            use_policy=entry.use_policy,
            verification_status=entry.verification_status,
            entry_id=entry.entry_id,
        )
    logger.debug(
        "Rights use allowed",
        extra={
            "use": "reference_analyze",
            "use_policy": entry.use_policy,
            "entry_id": entry.entry_id,
        },
    )
    return RightsEvaluateResultV1(
        allowed=True,
        code=None,
        message="",
        ownership_class=entry.ownership_class,
        use_policy=entry.use_policy,
        verification_status=entry.verification_status,
        entry_id=entry.entry_id,
    )


def _coerce_request_rights(
    request_rights: DatasetProvenance | Mapping[str, Any] | RightsRegistryEntryV1 | None,
    *,
    source_kind: RightsSourceKind,
    source_id: str,
) -> RightsRegistryEntryV1 | None:
    if request_rights is None:
        return None
    if isinstance(request_rights, RightsRegistryEntryV1):
        return request_rights
    if isinstance(request_rights, DatasetProvenance):
        return map_legacy_dataset_provenance(
            request_rights,
            source_kind=source_kind,
            source_id=source_id,
        )
    if isinstance(request_rights, Mapping):
        raw = dict(request_rights)
        # Prefer explicit registry-shaped payload.
        if raw.get("schema_version") == "rights.registry.entry.v1" or (
            "ownership_class" in raw and "use_policy" in raw
        ):
            try:
                return RightsRegistryEntryV1.model_validate(raw)
            except ValidationError:
                pass
        use_policy_override = raw.get("use_policy")
        status = raw.get("status") or raw.get("provenance_status") or "unknown"
        try:
            return map_legacy_status_fields(
                status=str(status),
                user_owned_attested=bool(raw.get("user_owned_attested", False)),
                license=raw.get("license"),
                license_spdx=raw.get("license_spdx"),
                source_url=raw.get("source_url"),
                source_reference=raw.get("source_reference"),
                composer=raw.get("composer"),
                author=raw.get("author"),
                use_policy_override=(
                    str(use_policy_override) if use_policy_override is not None else None
                ),
                source_kind=source_kind,
                source_id=source_id,
            )
        except RightsGovernanceError:
            return None
    return None


def resolve_rights_entry(
    source_kind: RightsSourceKind,
    source_id: str,
    *,
    registry_row: RightsRegistryEntryV1 | None = None,
    request_rights: DatasetProvenance | Mapping[str, Any] | RightsRegistryEntryV1 | None = None,
) -> tuple[RightsRegistryEntryV1, str]:
    """Part K.1 resolve order: registry → request attestation → synthetic unknown.

    Returns ``(entry, resolution)`` where resolution is
    ``registry`` | ``request_attestation`` | ``synthetic_unknown``.
    Never invents ``training_allowed`` on missing data. Registry wins over
    contradictory request claims for the same source.
    """
    if registry_row is not None:
        logger.debug(
            "Rights resolve registry win",
            extra={
                "source_kind": source_kind,
                "source_id_prefix": source_id[:12],
                "entry_id": registry_row.entry_id,
                "use_policy": registry_row.use_policy,
            },
        )
        return registry_row, "registry"

    mapped = _coerce_request_rights(
        request_rights,
        source_kind=source_kind,
        source_id=source_id,
    )
    if mapped is not None:
        logger.debug(
            "Rights resolve request attestation",
            extra={
                "source_kind": source_kind,
                "source_id_prefix": source_id[:12],
                "use_policy": mapped.use_policy,
            },
        )
        return mapped, "request_attestation"

    synthetic = synthetic_unknown_entry(source_kind=source_kind, source_id=source_id)
    logger.debug(
        "Rights resolve synthetic unknown",
        extra={"source_kind": source_kind, "source_id_prefix": source_id[:12]},
    )
    return synthetic, "synthetic_unknown"


def require_rights_use(
    entry: RightsRegistryEntryV1,
    use: RightsUseKind,
) -> RightsRegistryEntryV1:
    """Evaluate and raise ``RightsGovernanceError`` when refused (hard-fail)."""
    result = evaluate_rights_use(entry, use)
    if result.allowed:
        return entry
    code = result.code or (
        "rights_train_refused" if use == "train" else "rights_reference_refused"
    )
    raise RightsGovernanceError(
        code,
        result.message or RIGHTS_GOVERNANCE_ERROR_CODES.get(code, "Rights use refused."),
        http_status=422,
        details={
            "source_kind": entry.source_kind,
            "source_id": entry.source_id,
            "use_policy": entry.use_policy,
        },
    )
