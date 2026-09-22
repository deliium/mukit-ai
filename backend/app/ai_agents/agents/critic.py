"""Critic agent — EvaluationEngine + optional model critique + RevisionPlan."""

from __future__ import annotations

import logging
import os

from app.ai_agents.agents.common import BaseMusicAgent
from app.ai_agents.agents.typed_emit import (
    AGENT_REVISION_PLAN_SCHEMA,
    bounded_analysis_payload,
    depends_on_edges,
    make_plan_artifact,
    parent_ids_from_context,
    revision_plan_payload,
)
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
    CritiqueRecommendation,
)
from app.services.composition_critique import evaluate_composition
from app.services.composition_critique_scope import resolve_critique_scope
from app.services.llm_composition_critique import run_model_critique

logger = logging.getLogger(__name__)


class CriticAgent(BaseMusicAgent):
    """Critique via EvaluationEngine; never mutates Composition."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        params = request.parameters or {}
        include_model = bool(params.get("include_model_critique"))
        revise_on_technical = bool(params.get("revise_on_technical"))
        climax_idx = params.get("requested_climax_section_index")
        if climax_idx is not None:
            try:
                climax_idx = int(climax_idx)
            except (TypeError, ValueError):
                climax_idx = None
        brief_text = None
        if request.context.brief is not None:
            brief_payload = request.context.brief.payload or {}
            brief_text = str(brief_payload.get("intent") or "")[:200]

        resolved = resolve_critique_scope(draft, None)
        model_findings, model_status = run_model_critique(
            analysis_report=None,
            resolved=resolved,
            brief_excerpt=brief_text,
            include_model_critique=include_model,
            env=os.environ,
        )

        result = evaluate_composition(
            draft,
            revise_on_technical=revise_on_technical,
            requested_climax_section_index=climax_idx,
            brief_text=brief_text,
            extra_findings=model_findings,
            model_critique_status=model_status,
        )
        critique = result.critique
        recommendation = critique.recommendation
        warning_codes: list[str] = list(critique.reason_codes[:8])

        analysis_art = None
        if result.analysis_report is not None:
            analysis_payload = bounded_analysis_payload(result.analysis_report)
            analysis_art = AgentArtifactV1(
                kind=AgentArtifactKind.ANALYSIS,
                producer_agent_id=self._descriptor.id,
                content_type="composition.analysis.bounded.v1",
                payload=analysis_payload,
                source_fingerprint=request.context.source_fingerprint,
                provenance=self._provenance(
                    "agent_critic_critique", runtime="composition_critique"
                ),
            )

        parents = parent_ids_from_context(
            request.context,
            "harmony_artifact",
            "melody_artifact",
            "arrangement_candidate",
        )
        if analysis_art is not None:
            parents = [analysis_art.artifact_id, *parents][:16]
        critique_art = AgentArtifactV1(
            kind=AgentArtifactKind.CRITIQUE,
            producer_agent_id=self._descriptor.id,
            content_type="agent.critique.v1",
            payload=critique.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            parent_artifact_ids=parents,
            depends_on=depends_on_edges(
                request.context.harmony_artifact,
                request.context.melody_artifact,
                request.context.arrangement_candidate,
            ),
            provenance=self._provenance(
                "agent_critic_critique", runtime="composition_critique"
            ),
            warning_codes=warning_codes,
        )
        slots = {"critique": critique_art}
        artifacts = [critique_art]
        if analysis_art is not None:
            slots["analysis"] = analysis_art
            artifacts = [analysis_art, critique_art]

        if recommendation == CritiqueRecommendation.REVISE:
            revision = make_plan_artifact(
                kind=AgentArtifactKind.PLAN,
                producer_agent_id=self._descriptor.id,
                content_type=AGENT_REVISION_PLAN_SCHEMA,
                payload=revision_plan_payload(revise_targets=critique.reason_codes[:8]),
                source_fingerprint=request.context.source_fingerprint,
                provenance=self._provenance(
                    "agent_critic_critique", runtime="composition_critique"
                ),
                parent_artifact_ids=[critique_art.artifact_id],
                depends_on=depends_on_edges(critique_art),
            )
            artifacts.append(revision)

        logger.info(
            "Critic recommendation",
            extra={
                "agent_id": self._descriptor.id,
                "recommendation": recommendation.value,
                "finding_count": len(critique.findings),
                "hard_constraint": critique.stratum_counts.hard_constraint,
                "technical": critique.stratum_counts.technical,
                "stylistic": critique.stratum_counts.stylistic,
                "subjective": critique.stratum_counts.subjective,
                "model_critique_status": model_status,
                "content_types": [a.content_type for a in artifacts],
            },
        )
        logger.debug(
            "Critic reason codes",
            extra={"agent_id": self._descriptor.id, "reason_codes": critique.reason_codes},
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=artifacts,
            updated_context_slots=slots,
            recommendation=recommendation,
            provenance_stage=self._stage(
                "agent_critic_critique", runtime="composition_critique"
            ),
            warning_codes=warning_codes,
        )
