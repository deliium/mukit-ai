"""Melody / Motif agent — motif apply when possible; never silent LLM note fallback."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_agents.agents.common import BaseMusicAgent, selection_str
from app.ai_agents.progressive_realize import RealizeService, apply_realized_composition
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
)
from app.llm_settings import load_llm_settings
from app.motif_schemas import MotifApplyRequest
from app.services.composition_motif_editor import apply_motif_operation
from app.services.composition_motif_similarity import MECHANICAL_OPERATIONS

logger = logging.getLogger(__name__)


class MelodyMotifAgent(BaseMusicAgent):
    """Wrap motif apply when selection/motifs allow; otherwise pass-through draft."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        selection = request.selection or {}
        warning_codes: list[str] = []
        working_draft_update = None
        payload: dict[str, Any] = {"schema": "melody.draft"}
        content_type = "melody.draft"

        motif_payload = selection.get("motif_apply")
        if isinstance(motif_payload, dict):
            operation = selection_str(motif_payload, "operation", "transpose") or "transpose"
            try:
                if operation not in MECHANICAL_OPERATIONS:
                    # No silent LLM/creative note fallback when symbolic/mechanical path is required.
                    warning_codes.append("melody_no_silent_llm_fallback")
                    raise ValueError(
                        "creative motif requires explicit provider selection; refused silent LLM"
                    )

                apply_req = MotifApplyRequest.model_validate(
                    {
                        "composition": draft,
                        **motif_payload,
                        "operation": operation,
                    }
                )
                outcome = await apply_motif_operation(apply_req, load_llm_settings())
                realized = apply_realized_composition(
                    current_draft=draft,
                    realized=outcome.composition,
                    service=RealizeService.MOTIF_APPLY,
                )
                working_draft_update = realized
                content_type = "motif.draft"
                payload = {
                    "schema": "motif.draft",
                    "operation": operation,
                    "occurrence_id": getattr(outcome, "occurrence_id", None),
                }
            except Exception as exc:  # noqa: BLE001 — soft fail to recommendation
                logger.info(
                    "Motif apply soft-failed",
                    extra={"agent_id": self._descriptor.id, "reason": type(exc).__name__},
                )
                warning_codes.append(f"motif_apply_{type(exc).__name__}"[:80])
                payload["error"] = type(exc).__name__
                working_draft_update = None
        else:
            # No apply request: keep draft via validated V2 (no invented notes).
            realized = apply_realized_composition(
                current_draft=draft,
                realized=draft,
                service=RealizeService.VALIDATED_V2,
            )
            working_draft_update = realized
            motif_count = len(draft.motifs or [])
            payload.update(
                {
                    "note": "passthrough_no_motif_apply",
                    "motif_definition_count": motif_count,
                    "hint": "Pass selection.motif_apply to run mechanical motif transforms",
                }
            )
            if motif_count == 0:
                warning_codes.append("melody_passthrough_no_motifs")
            else:
                warning_codes.append("melody_passthrough_motifs_present")

        art = AgentArtifactV1(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type=content_type,
            payload=payload,
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_melody_motif_propose", runtime="motif_service"),
            warning_codes=warning_codes,
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots={"melody_artifact": art},
            working_draft_update=working_draft_update,
            provenance_stage=self._stage("agent_melody_motif_propose", runtime="motif_service"),
            warning_codes=warning_codes,
        )
