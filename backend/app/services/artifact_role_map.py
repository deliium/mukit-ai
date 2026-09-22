"""Validate and normalize ``artifact_role_map`` for multi-agent Apply."""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.ai_agents.errors import ArtifactPayloadRejectedError
from app.services.agent_artifact_workspace import LINK_ROLES, ROLE_EXPECTED_CONTENT_TYPES

logger = logging.getLogger(__name__)

REQUIRED_SPINE_ROLES: tuple[str, ...] = (
    "brief",
    "harmony_plan",
    "motif_plan",
    "arrangement_plan",
    "critique",
)


def validate_artifact_role_map(
    role_map: Mapping[str, Any] | None,
    *,
    require_revision_plan: bool = False,
) -> dict[str, dict[str, str] | None]:
    """Return normalized role map or raise ``ArtifactPayloadRejectedError``."""
    if not isinstance(role_map, Mapping):
        raise ArtifactPayloadRejectedError(
            "artifact_role_map is required for multi-agent-apply",
            details={"missing": list(REQUIRED_SPINE_ROLES)},
        )
    normalized: dict[str, dict[str, str] | None] = {}
    missing: list[str] = []
    required = list(REQUIRED_SPINE_ROLES)
    if require_revision_plan:
        required.append("revision_plan")

    for role in LINK_ROLES:
        entry = role_map.get(role)
        if entry is None:
            normalized[role] = None
            if role in required:
                missing.append(role)
            continue
        if not isinstance(entry, Mapping):
            missing.append(role)
            normalized[role] = None
            continue
        artifact_id = str(entry.get("artifact_id") or "").strip()
        content_type = str(entry.get("content_type") or "").strip()
        if not artifact_id or not content_type:
            missing.append(role)
            normalized[role] = None
            continue
        expected = ROLE_EXPECTED_CONTENT_TYPES.get(role)
        if expected and content_type not in expected:
            raise ArtifactPayloadRejectedError(
                f"artifact_role_map role {role} content_type mismatch",
                details={"role": role, "content_type": content_type},
            )
        normalized[role] = {"artifact_id": artifact_id, "content_type": content_type}

    if missing:
        logger.info(
            "artifact_role_map validation failed",
            extra={"missing_roles": missing, "present_roles": [k for k, v in normalized.items() if v]},
        )
        raise ArtifactPayloadRejectedError(
            "artifact_role_map missing required roles",
            details={"missing": missing},
        )

    logger.info(
        "artifact_role_map validated",
        extra={
            "role_keys": sorted(k for k, v in normalized.items() if v is not None),
            "require_revision_plan": require_revision_plan,
        },
    )
    return normalized
