"""Helpers for emitting typed plan artifacts from spine agents."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_agents.artifact_schemas import (
    AgentArrangementPlanV1,
    AgentCompositionPatchV1,
    AgentFormPlanV1,
    AgentHarmonyPlanV1,
    AgentMotifPlanV1,
    AgentRevisionPlanV1,
    ArrangementTrackHint,
    FormPlanSection,
    HarmonyPlanEvent,
    MotifPlanEntry,
    project_music_analysis_bounded,
)
from app.ai_agents.schemas import (
    AGENT_ARRANGEMENT_PLAN_SCHEMA,
    AGENT_COMPOSITION_PATCH_SCHEMA,
    AGENT_FORM_PLAN_SCHEMA,
    AGENT_HARMONY_PLAN_SCHEMA,
    AGENT_MOTIF_PLAN_SCHEMA,
    AGENT_REVISION_PLAN_SCHEMA,
    AgentArtifactKind,
    AgentArtifactV1,
    AgentWorkflowContext,
    ArtifactDependencyEdge,
    CritiqueRecommendation,
)
from app.composition_schemas import CompositionV2

logger = logging.getLogger(__name__)


def _slot_artifact(ctx: AgentWorkflowContext, name: str) -> AgentArtifactV1 | None:
    return getattr(ctx, name, None)


def parent_ids_from_context(ctx: AgentWorkflowContext, *slot_names: str) -> list[str]:
    ids: list[str] = []
    for name in slot_names:
        art = _slot_artifact(ctx, name)
        if art is not None:
            ids.append(art.artifact_id)
    return ids[:16]


def depends_on_edges(*artifacts: AgentArtifactV1 | None) -> list[ArtifactDependencyEdge]:
    edges: list[ArtifactDependencyEdge] = []
    for art in artifacts:
        if art is not None:
            edges.append(ArtifactDependencyEdge(artifact_id=art.artifact_id, relation="requires"))
    return edges


def form_plan_from_composition(composition: CompositionV2) -> dict[str, Any]:
    sections = list(composition.sections or [])
    if not sections:
        plan = AgentFormPlanV1(
            sections=[
                FormPlanSection(
                    label="A",
                    start_bar=1,
                    bar_count=max(1, int(composition.bar_count or 1)),
                )
            ],
            comment="derived_from_composition",
        )
    else:
        form_sections: list[FormPlanSection] = []
        for section in sections[:32]:
            form_sections.append(
                FormPlanSection(
                    label=str(getattr(section, "label", None) or getattr(section, "name", None) or "section")[
                        :64
                    ],
                    start_bar=max(1, int(getattr(section, "start_bar", 1) or 1)),
                    bar_count=max(1, int(getattr(section, "bar_count", 1) or 1)),
                )
            )
        plan = AgentFormPlanV1(sections=form_sections, comment="derived_from_composition")
    return plan.model_dump(mode="json")


def harmony_plan_from_composition(composition: CompositionV2) -> dict[str, Any]:
    events: list[HarmonyPlanEvent] = []
    for item in list(composition.harmony or [])[:64]:
        chord = str(getattr(item, "chord", None) or getattr(item, "symbol", None) or "").strip()
        if not chord:
            continue
        bar = int(getattr(item, "bar", None) or getattr(item, "start_bar", 1) or 1)
        events.append(HarmonyPlanEvent(bar=max(1, bar), chord=chord[:32]))
    plan = AgentHarmonyPlanV1(
        key=str(composition.key or "") or None,
        chord_events=events,
        comment="derived_from_composition_harmony",
    )
    return plan.model_dump(mode="json")


def motif_plan_from_composition(composition: CompositionV2) -> dict[str, Any]:
    motifs: list[MotifPlanEntry] = []
    for motif in list(composition.motifs or [])[:16]:
        label = str(getattr(motif, "label", None) or getattr(motif, "id", None) or "motif")[:64]
        motifs.append(MotifPlanEntry(motif_label=label, role="melody"))
    if not motifs:
        motifs.append(MotifPlanEntry(motif_label="primary", role="melody", recurrence="once"))
    plan = AgentMotifPlanV1(motifs=motifs, comment="derived_from_composition_motifs")
    return plan.model_dump(mode="json")


def arrangement_plan_from_composition(composition: CompositionV2) -> dict[str, Any]:
    hints: list[ArrangementTrackHint] = []
    for track in list(composition.tracks or [])[:16]:
        role = str(getattr(track, "role", None) or "harmony")
        try:
            hints.append(
                ArrangementTrackHint(
                    role=role,
                    instrument_family=str(getattr(track, "instrument", None) or "unknown")[:80],
                )
            )
        except Exception:  # noqa: BLE001 — skip unsupported roles
            continue
    plan = AgentArrangementPlanV1(
        track_hints=hints,
        texture_summary=f"tracks={len(composition.tracks)}",
        comment="derived_from_composition_tracks",
    )
    return plan.model_dump(mode="json")


def composition_patch_payload(
    *,
    realize_service: str,
    op_refs: list[str] | None = None,
    recipe: str | None = None,
    source_fingerprint: str | None = None,
) -> dict[str, Any]:
    return AgentCompositionPatchV1(
        realize_service=realize_service,  # type: ignore[arg-type]
        op_refs=op_refs or [],
        recipe=recipe,
        source_fingerprint=source_fingerprint,
    ).model_dump(mode="json")


def revision_plan_payload(
    *,
    revise_targets: list[str] | None = None,
    stop_criteria: list[str] | None = None,
) -> dict[str, Any]:
    return AgentRevisionPlanV1(
        critique_recommendation=CritiqueRecommendation.REVISE,
        revise_targets=revise_targets or ["harmony", "melody_motif"],
        stop_criteria=stop_criteria or ["critic_approve"],
        comment="critic_revise",
    ).model_dump(mode="json")


def make_plan_artifact(
    *,
    kind: AgentArtifactKind,
    producer_agent_id: str,
    content_type: str,
    payload: dict[str, Any],
    source_fingerprint: str | None,
    provenance: Any,
    parent_artifact_ids: list[str] | None = None,
    depends_on: list[ArtifactDependencyEdge] | None = None,
    warning_codes: list[str] | None = None,
) -> AgentArtifactV1:
    art = AgentArtifactV1(
        kind=kind,
        producer_agent_id=producer_agent_id,
        content_type=content_type,
        payload=payload,
        source_fingerprint=source_fingerprint,
        parent_artifact_ids=parent_artifact_ids or [],
        depends_on=depends_on or [],
        provenance=provenance,
        warning_codes=warning_codes or [],
    )
    logger.info(
        "Typed plan artifact emitted",
        extra={
            "agent_id": producer_agent_id,
            "content_types": [content_type],
            "artifact_id_prefix": art.artifact_id[:12],
            "depends_on_count": len(art.depends_on),
        },
    )
    return art


def bounded_analysis_payload(report: Any) -> dict[str, Any]:
    """Build durable-safe analysis projection from an analysis report object."""
    raw = {
        "schema_version": "composition.analysis.v1",
        "status": getattr(report, "status", "ok"),
        "algorithm_version": getattr(report, "algorithm_version", None),
        "source_fingerprint": getattr(report, "source_fingerprint", None) or ("x" * 32),
        "warnings": [
            {"code": getattr(w, "code", "unknown")} for w in (getattr(report, "warnings", None) or [])
        ],
        "resolved_scope": (
            report.resolved_scope.model_dump(mode="json")
            if hasattr(getattr(report, "resolved_scope", None), "model_dump")
            else {}
        ),
        "section_summaries": list(getattr(report, "section_summaries", None) or [])[:32],
    }
    return project_music_analysis_bounded(raw)


__all__ = [
    "AGENT_ARRANGEMENT_PLAN_SCHEMA",
    "AGENT_COMPOSITION_PATCH_SCHEMA",
    "AGENT_FORM_PLAN_SCHEMA",
    "AGENT_HARMONY_PLAN_SCHEMA",
    "AGENT_MOTIF_PLAN_SCHEMA",
    "AGENT_REVISION_PLAN_SCHEMA",
    "arrangement_plan_from_composition",
    "bounded_analysis_payload",
    "composition_patch_payload",
    "depends_on_edges",
    "form_plan_from_composition",
    "harmony_plan_from_composition",
    "make_plan_artifact",
    "motif_plan_from_composition",
    "parent_ids_from_context",
    "revision_plan_payload",
]
