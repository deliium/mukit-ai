"""Pure project-role matrix and revision-origin classifier.

No SQLite imports. ``ai_agents`` must not import this module.
"""

from __future__ import annotations

from typing import Literal

Role = Literal["owner", "editor", "commenter", "viewer"]
Action = Literal[
    "read",
    "write_score",
    "write_audio",
    "comment",
    "review_open",
    "review_decide",
    "share",
    "delete_project",
    "train_adapter",
    "train_model_lab",
]
RevisionOrigin = Literal["ai", "human"]

ROLES: frozenset[str] = frozenset({"owner", "editor", "commenter", "viewer"})
GRANTABLE_ROLES: frozenset[str] = frozenset({"editor", "commenter", "viewer"})
ACTIONS: frozenset[str] = frozenset(
    {
        "read",
        "write_score",
        "write_audio",
        "comment",
        "review_open",
        "review_decide",
        "share",
        "delete_project",
        "train_adapter",
        "train_model_lab",
    }
)

_ROLE_ACTIONS: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read"}),
    "commenter": frozenset({"read", "comment"}),
    "editor": frozenset(
        {"read", "write_score", "write_audio", "comment", "review_open"}
    ),
    "owner": ACTIONS,
}

AI_ORIGIN_OPERATIONS: frozenset[str] = frozenset(
    {
        "generate-apply",
        "ai-region-edit-apply",
        "creative-motif-apply",
        "reharmonize-apply",
        "development-apply",
        "arrangement-apply",
        "multi-agent-apply",
        "film-score-adapt-apply",
        "musical-universe-theme-apply",
        "autonomous-stage",
        "asset-pack-generate",
        "asset-pack-slot-regenerate",
    }
)

HUMAN_ORIGIN_OPERATIONS: frozenset[str] = frozenset(
    {
        "project-create",
        "manual-checkpoint",
        "pre-ai-checkpoint",
        "revision-restore",
        "import",
        "migration",
    }
)

REVIEW_NOT_ALLOWED_OPERATIONS: frozenset[str] = frozenset({"project-create"})


def role_allows(role: str, action: str) -> bool:
    """Return whether ``role`` may perform ``action``. Unknown values are denied."""
    allowed = _ROLE_ACTIONS.get(role)
    if allowed is None:
        return False
    return action in allowed


def revision_origin(operation_type: str) -> RevisionOrigin | None:
    """Classify a revision operation as ``ai`` or ``human``.

    Returns ``None`` when the operation is not in either origin set.
    """
    if operation_type in AI_ORIGIN_OPERATIONS:
        return "ai"
    if operation_type in HUMAN_ORIGIN_OPERATIONS:
        return "human"
    return None


def review_allowed_for_operation(operation_type: str) -> bool:
    """Reviews cover every operation except ``project-create``."""
    if operation_type in REVIEW_NOT_ALLOWED_OPERATIONS:
        return False
    return revision_origin(operation_type) is not None
