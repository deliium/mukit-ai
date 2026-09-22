"""Declarative dependency rules for typed agent artifacts.

Validation fails closed with ``artifact_dependency_unsatisfied``.
Agents emit envelopes only; this module is pure (no SQLite).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.ai_agents.artifact_schemas import (
    AGENT_ARRANGEMENT_PLAN_SCHEMA,
    AGENT_COMPOSITION_PATCH_SCHEMA,
    AGENT_FORM_PLAN_SCHEMA,
    AGENT_HARMONY_PLAN_SCHEMA,
    AGENT_MOTIF_PLAN_SCHEMA,
    AGENT_ORCHESTRATION_PLAN_SCHEMA,
    AGENT_PERFORMANCE_PLAN_SCHEMA,
    AGENT_PRODUCTION_PLAN_SCHEMA,
    AGENT_RENDER_PLAN_SCHEMA,
    AGENT_REVISION_PLAN_SCHEMA,
    COMPOSITION_PLAN_SCHEMA,
)
from app.ai_agents.schemas import (
    AGENT_BRIEF_SCHEMA,
    AGENT_CRITIQUE_SCHEMA,
    AgentArtifactV1,
)

logger = logging.getLogger(__name__)

ARTIFACT_DEPENDENCY_UNSATISFIED = "artifact_dependency_unsatisfied"


@dataclass(frozen=True)
class DependencyRequirement:
    """One required dependency class for a content type."""

    required_content_types: frozenset[str]
    """Any-of group: at least one artifact matching these content types."""

    label: str
    """Human-stable label for error details (e.g. ``harmony_plan``)."""


@dataclass(frozen=True)
class DependencyRule:
    content_type: str
    requirements: tuple[DependencyRequirement, ...]
    require_source_revision_or_fingerprint: bool = False
    require_working_draft_fingerprint: bool = False
    # Special: RevisionPlan requires a CritiqueReport with recommendation=revise
    require_critique_revise: bool = False


# Initial registry (locked by plan).
_DEPENDENCY_REGISTRY: dict[str, DependencyRule] = {
    AGENT_MOTIF_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_MOTIF_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(frozenset({AGENT_BRIEF_SCHEMA}), "brief"),
            DependencyRequirement(frozenset({AGENT_HARMONY_PLAN_SCHEMA}), "harmony_plan"),
        ),
    ),
    AGENT_ARRANGEMENT_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_ARRANGEMENT_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(
                frozenset({COMPOSITION_PLAN_SCHEMA, AGENT_FORM_PLAN_SCHEMA}),
                "composition_or_form_plan",
            ),
            # FormPlan path also wants HarmonyPlan; CompositionPlan alone is enough.
            # Implemented as soft-any: if only FormPlan present, HarmonyPlan required below.
        ),
        require_source_revision_or_fingerprint=True,
    ),
    AGENT_ORCHESTRATION_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_ORCHESTRATION_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(frozenset({AGENT_ARRANGEMENT_PLAN_SCHEMA}), "arrangement_plan"),
        ),
    ),
    AGENT_PERFORMANCE_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_PERFORMANCE_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(
                frozenset({AGENT_MOTIF_PLAN_SCHEMA, AGENT_ARRANGEMENT_PLAN_SCHEMA}),
                "motif_or_arrangement_plan",
            ),
        ),
    ),
    AGENT_PRODUCTION_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_PRODUCTION_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(
                frozenset({AGENT_ARRANGEMENT_PLAN_SCHEMA}),
                "arrangement_plan_optional_with_revision",
            ),
        ),
        require_source_revision_or_fingerprint=True,
    ),
    AGENT_RENDER_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_RENDER_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(
                frozenset({AGENT_ARRANGEMENT_PLAN_SCHEMA}),
                "arrangement_plan_optional_with_revision",
            ),
        ),
        require_source_revision_or_fingerprint=True,
    ),
    AGENT_CRITIQUE_SCHEMA: DependencyRule(
        content_type=AGENT_CRITIQUE_SCHEMA,
        requirements=(),
        require_working_draft_fingerprint=True,
    ),
    AGENT_REVISION_PLAN_SCHEMA: DependencyRule(
        content_type=AGENT_REVISION_PLAN_SCHEMA,
        requirements=(
            DependencyRequirement(frozenset({AGENT_CRITIQUE_SCHEMA}), "critique"),
        ),
        require_critique_revise=True,
    ),
    AGENT_COMPOSITION_PATCH_SCHEMA: DependencyRule(
        content_type=AGENT_COMPOSITION_PATCH_SCHEMA,
        requirements=(
            DependencyRequirement(
                frozenset(
                    {
                        AGENT_HARMONY_PLAN_SCHEMA,
                        AGENT_MOTIF_PLAN_SCHEMA,
                        AGENT_ARRANGEMENT_PLAN_SCHEMA,
                        COMPOSITION_PLAN_SCHEMA,
                        AGENT_FORM_PLAN_SCHEMA,
                    }
                ),
                "realize_plan",
            ),
        ),
    ),
}


class ArtifactDependencyError(ValueError):
    """Raised when dependency rules are not satisfied."""

    def __init__(
        self,
        message: str,
        *,
        missing: Sequence[str] | None = None,
        content_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = ARTIFACT_DEPENDENCY_UNSATISFIED
        self.missing = list(missing or [])
        self.content_type = content_type


def dependency_registry() -> Mapping[str, DependencyRule]:
    return _DEPENDENCY_REGISTRY


def _index_by_id(artifacts: Iterable[AgentArtifactV1 | Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in artifacts:
        if isinstance(item, AgentArtifactV1):
            out[item.artifact_id] = item
        elif isinstance(item, Mapping) and item.get("artifact_id"):
            out[str(item["artifact_id"])] = item
    return out


def _content_type_of(item: AgentArtifactV1 | Mapping[str, Any]) -> str:
    if isinstance(item, AgentArtifactV1):
        return item.content_type
    return str(item.get("content_type") or "")


def _payload_of(item: AgentArtifactV1 | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(item, AgentArtifactV1):
        return dict(item.payload or {})
    payload = item.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _available_content_types(
    available: Iterable[AgentArtifactV1 | Mapping[str, Any]],
) -> set[str]:
    return {_content_type_of(a) for a in available if _content_type_of(a)}


def validate_artifact_dependencies(
    artifact: AgentArtifactV1 | Mapping[str, Any],
    *,
    available_artifacts: Sequence[AgentArtifactV1 | Mapping[str, Any]] = (),
    source_revision_id: str | None = None,
    source_fingerprint: str | None = None,
    working_draft_fingerprint: str | None = None,
) -> None:
    """Validate ``depends_on`` / context against the dependency registry.

    Raises ``ArtifactDependencyError`` (code ``artifact_dependency_unsatisfied``).
    """
    if isinstance(artifact, AgentArtifactV1):
        content_type = artifact.content_type
        depends_on = list(getattr(artifact, "depends_on", None) or [])
        parent_ids = list(artifact.parent_artifact_ids or [])
        art_source_rev = getattr(artifact, "source_revision_id", None) or source_revision_id
        art_fp = artifact.source_fingerprint or source_fingerprint
    else:
        content_type = str(artifact.get("content_type") or "")
        depends_on = list(artifact.get("depends_on") or [])
        parent_ids = list(artifact.get("parent_artifact_ids") or [])
        art_source_rev = artifact.get("source_revision_id") or source_revision_id
        art_fp = artifact.get("source_fingerprint") or source_fingerprint

    rule = _DEPENDENCY_REGISTRY.get(content_type)
    if rule is None:
        logger.debug(
            "No dependency rule for content_type",
            extra={"content_type": content_type[:80]},
        )
        return

    by_id = _index_by_id(available_artifacts)
    # Resolve depends_on edges into available set.
    resolved: list[Any] = list(available_artifacts)
    for edge in depends_on:
        if isinstance(edge, Mapping):
            aid = str(edge.get("artifact_id") or "")
        else:
            aid = str(getattr(edge, "artifact_id", "") or "")
        if aid and aid in by_id:
            continue
        if aid and aid not in by_id:
            logger.info(
                "Artifact dependency validation failed",
                extra={
                    "code": ARTIFACT_DEPENDENCY_UNSATISFIED,
                    "content_type": content_type[:80],
                    "missing": ["depends_on_unresolved"],
                },
            )
            raise ArtifactDependencyError(
                f"depends_on artifact not found: {aid[:12]}",
                missing=["depends_on_unresolved"],
                content_type=content_type,
            )
    for pid in parent_ids:
        if pid and pid not in by_id:
            # Parents are informational; allow missing only if not required by rule.
            logger.debug(
                "Parent artifact id not in available set",
                extra={"artifact_id_prefix": str(pid)[:12]},
            )

    available_types = _available_content_types(resolved)
    missing: list[str] = []

    # ArrangementPlan: CompositionPlan alone OR (FormPlan + HarmonyPlan)
    if content_type == AGENT_ARRANGEMENT_PLAN_SCHEMA:
        has_composition_plan = COMPOSITION_PLAN_SCHEMA in available_types
        has_form = AGENT_FORM_PLAN_SCHEMA in available_types
        has_harmony = AGENT_HARMONY_PLAN_SCHEMA in available_types
        if not has_composition_plan and not (has_form and has_harmony):
            if not has_composition_plan and not has_form:
                missing.append("composition_or_form_plan")
            elif has_form and not has_harmony:
                missing.append("harmony_plan")
    else:
        for req in rule.requirements:
            # Production/Render: arrangement OR source revision/fingerprint
            if req.label.endswith("optional_with_revision"):
                if req.required_content_types.isdisjoint(available_types):
                    if not (art_source_rev or art_fp):
                        missing.append(req.label)
                continue
            if req.required_content_types.isdisjoint(available_types):
                missing.append(req.label)

    if rule.require_source_revision_or_fingerprint:
        if not (art_source_rev or art_fp):
            missing.append("source_revision_or_fingerprint")

    if rule.require_working_draft_fingerprint:
        fp = working_draft_fingerprint or art_fp
        if not fp:
            missing.append("working_draft_fingerprint")

    if rule.require_critique_revise:
        critique_items = [
            a for a in resolved if _content_type_of(a) == AGENT_CRITIQUE_SCHEMA
        ]
        if not critique_items:
            missing.append("critique")
        else:
            ok = False
            for item in critique_items:
                payload = _payload_of(item)
                if str(payload.get("recommendation") or "").lower() == "revise":
                    ok = True
                    break
            if not ok:
                missing.append("critique_recommendation_revise")

    if missing:
        logger.info(
            "Artifact dependency validation failed",
            extra={
                "code": ARTIFACT_DEPENDENCY_UNSATISFIED,
                "content_type": content_type[:80],
                "missing": missing,
            },
        )
        raise ArtifactDependencyError(
            f"Artifact dependencies unsatisfied for {content_type}: {', '.join(missing)}",
            missing=missing,
            content_type=content_type,
        )

    logger.info(
        "Artifact dependency validation passed",
        extra={
            "content_type": content_type[:80],
            "requirement_count": len(rule.requirements),
            "available_type_count": len(available_types),
        },
    )
    logger.debug(
        "Artifact dependency edges",
        extra={
            "depends_on_prefixes": [
                (
                    str(e.get("artifact_id") if isinstance(e, Mapping) else getattr(e, "artifact_id", ""))[
                        :12
                    ]
                )
                for e in depends_on
            ][:16],
        },
    )


logger.info(
    "Artifact dependency registry loaded",
    extra={"rule_count": len(_DEPENDENCY_REGISTRY)},
)
