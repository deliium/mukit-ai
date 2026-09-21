"""Provenance enforcement and train eligibility policy."""

from __future__ import annotations

import logging

from app.dataset.errors import DatasetProvenanceError
from app.dataset.schemas import (
    TRAIN_ELIGIBLE_STATUSES,
    DatasetEligibilityPolicy,
    DatasetProvenance,
    ProvenanceStatus,
)


logger = logging.getLogger(__name__)


def compute_train_eligible(
    provenance: DatasetProvenance,
    *,
    eligibility: DatasetEligibilityPolicy | None = None,
) -> bool:
    policy = eligibility or DatasetEligibilityPolicy()
    allowed = set(policy.train_statuses) or set(TRAIN_ELIGIBLE_STATUSES)
    eligible = provenance.status in allowed
    if eligible and provenance.status == "user_owned" and not provenance.user_owned_attested:
        eligible = False
    return eligible


def assert_train_split_policy(
    statuses_in_train: list[ProvenanceStatus],
    *,
    eligibility: DatasetEligibilityPolicy,
) -> None:
    """Hard-fail when unknown/restricted would pollute train without unsafe override."""
    unsafe = [s for s in statuses_in_train if s not in TRAIN_ELIGIBLE_STATUSES]
    if not unsafe:
        return
    if eligibility.allow_unsafe_train_pollution:
        logger.error(
            "Unsafe train pollution override enabled",
            extra={
                "unsafe_statuses": sorted(set(unsafe)),
                "allow_unsafe_train_pollution": True,
            },
        )
        return
    logger.error(
        "Attempted train pollution with non-eligible provenance",
        extra={"unsafe_statuses": sorted(set(unsafe))},
    )
    raise DatasetProvenanceError(
        "train_pollution_blocked",
        "unknown/restricted items cannot enter the train split",
        details={"unsafe_statuses": sorted(set(unsafe))},
    )


def summarize_eligibility(items: list[tuple[str, bool]]) -> dict[str, int]:
    """items: list of (provenance_status, train_eligible)."""
    by_status: dict[str, int] = {}
    eligible = 0
    for status, is_eligible in items:
        by_status[status] = by_status.get(status, 0) + 1
        if is_eligible:
            eligible += 1
    logger.info(
        "Provenance eligibility summary",
        extra={
            "eligibility_by_status": by_status,
            "train_eligible_count": eligible,
            "excluded_count": len(items) - eligible,
        },
    )
    return by_status
