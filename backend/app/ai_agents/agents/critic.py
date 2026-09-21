"""Critic agent — deterministic composition.analysis.v1 + typed critique."""

from __future__ import annotations

import logging

from app.ai_agents.agents.common import BaseMusicAgent
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentCritiqueV1,
    AgentRunRequest,
    AgentRunResult,
    CritiqueRecommendation,
)
from app.analysis_schemas import CompositionAnalysisError
from app.services.composition_analysis import analyze_composition

logger = logging.getLogger(__name__)


class CriticAgent(BaseMusicAgent):
    """Critique via analysis sidecar; approve unless analysis failed."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        warning_codes: list[str] = []
        analysis_art = None
        recommendation = CritiqueRecommendation.APPROVE
        reason_codes = ["analysis_ok"]
        summary = "Critic approves working draft after deterministic analysis."

        try:
            report = analyze_composition(draft)
            # Bounded analysis payload for artifact (never full event dump).
            analysis_payload = {
                "schema": "composition.analysis.v1",
                "status": report.status,
                "algorithm_version": getattr(report, "algorithm_version", None),
                "source_fingerprint": getattr(report, "source_fingerprint", None),
                "warning_count": len(report.warnings or []),
                "warning_codes": [
                    getattr(w, "code", "unknown") for w in (report.warnings or [])[:16]
                ],
            }
            analysis_art = AgentArtifactV1(
                kind=AgentArtifactKind.ANALYSIS,
                producer_agent_id=self._descriptor.id,
                content_type="composition.analysis.v1",
                payload=analysis_payload,
                source_fingerprint=request.context.source_fingerprint,
                provenance=self._provenance(
                    "agent_critic_critique", runtime="composition_analysis"
                ),
            )
            if report.status == "failed":
                recommendation = CritiqueRecommendation.REVISE
                reason_codes = ["analysis_failed"]
                summary = "Critic requests revision: analysis status failed."
            elif report.status == "empty":
                reason_codes = ["analysis_empty"]
                summary = "Critic approves with empty analysis scope (no blocking findings)."
                warning_codes.append("critic_empty_analysis")
            elif report.status == "partial":
                reason_codes = ["analysis_partial"]
                summary = "Critic approves with partial analysis coverage."
                warning_codes.append("critic_partial_analysis")

            for warning in (report.warnings or [])[:8]:
                code = getattr(warning, "code", None)
                if code:
                    warning_codes.append(str(code)[:80])
        except CompositionAnalysisError as exc:
            logger.info(
                "Critic analysis soft-failed",
                extra={
                    "agent_id": self._descriptor.id,
                    "code": getattr(exc, "code", type(exc).__name__),
                },
            )
            recommendation = CritiqueRecommendation.REVISE
            reason_codes = [getattr(exc, "code", "analysis_error")[:80]]
            summary = "Critic requests revision: analysis could not run."
            warning_codes.append(getattr(exc, "code", "analysis_error")[:80])
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Critic unexpected analysis failure",
                extra={"agent_id": self._descriptor.id, "reason": type(exc).__name__},
            )
            recommendation = CritiqueRecommendation.REVISE
            reason_codes = [f"analysis_{type(exc).__name__}"[:80]]
            summary = "Critic requests revision: unexpected analysis failure."
            warning_codes.append(reason_codes[0])

        critique = AgentCritiqueV1(
            recommendation=recommendation,
            reason_codes=reason_codes[:16],
            summary=summary[:400],
        )
        critique_art = AgentArtifactV1(
            kind=AgentArtifactKind.CRITIQUE,
            producer_agent_id=self._descriptor.id,
            content_type="agent.critique.v1",
            payload=critique.model_dump(mode="json"),
            source_fingerprint=request.context.source_fingerprint,
            parent_artifact_ids=[analysis_art.artifact_id] if analysis_art else [],
            provenance=self._provenance(
                "agent_critic_critique", runtime="composition_analysis"
            ),
            warning_codes=warning_codes,
        )
        slots = {"critique": critique_art}
        artifacts = [critique_art]
        if analysis_art is not None:
            slots["analysis"] = analysis_art
            artifacts = [analysis_art, critique_art]

        logger.debug(
            "Critic recommendation",
            extra={
                "recommendation": recommendation.value,
                "warning_count": len(warning_codes),
            },
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=artifacts,
            updated_context_slots=slots,
            recommendation=recommendation,
            provenance_stage=self._stage(
                "agent_critic_critique", runtime="composition_analysis"
            ),
            warning_codes=warning_codes,
        )
