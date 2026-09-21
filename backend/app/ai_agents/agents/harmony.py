"""Harmony agent — wraps deterministic reharmonization preview + progressive realize."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_agents.agents.common import (
    BaseMusicAgent,
    selection_int,
    selection_str,
)
from app.ai_agents.progressive_realize import RealizeService, apply_realized_composition
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
)
from app.harmony_schemas import ReharmonizeError, ReharmonizePreviewRequest
from app.services.composition_reharmonization import (
    preview_reharmonization,
    recommend_target_track_ids,
)

logger = logging.getLogger(__name__)


class HarmonyAgent(BaseMusicAgent):
    """Propose harmony via ``preview_reharmonization`` (deterministic engine by default)."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        selection = request.selection or {}
        bar_count = max(1, int(draft.bar_count or 1))
        start_bar = selection_int(selection, "start_bar", 1)
        end_bar = selection_int(selection, "end_bar", min(4, bar_count))
        start_bar = max(1, min(start_bar, bar_count))
        end_bar = max(start_bar, min(end_bar, bar_count))

        content_policy = selection_str(
            selection, "content_policy", "preserve_melody_adapt_harmony"
        ) or "preserve_melody_adapt_harmony"
        operation = selection_str(selection, "operation", "increase_tension") or "increase_tension"
        engine = selection_str(selection, "engine", "deterministic") or "deterministic"

        targets = selection.get("target_track_ids")
        if not isinstance(targets, list) or not targets:
            targets = recommend_target_track_ids(draft, content_policy)  # type: ignore[arg-type]
        target_ids = [str(t).strip() for t in targets if str(t).strip()]

        warning_codes: list[str] = []
        working_draft_update = None
        payload: dict[str, Any] = {
            "schema": "reharmonize.candidate",
            "engine": engine,
            "operation": operation,
            "content_policy": content_policy,
            "start_bar": start_bar,
            "end_bar": end_bar,
            "target_track_ids": target_ids,
        }

        if engine != "deterministic":
            # Spine real adapter uses deterministic path only — avoid silent AI note fallback.
            warning_codes.append("harmony_engine_forced_deterministic")
            engine = "deterministic"
            payload["engine"] = engine

        try:
            reharm_request = ReharmonizePreviewRequest(
                composition=draft,
                selection={"start_bar": start_bar, "end_bar": end_bar},
                operation=operation,  # type: ignore[arg-type]
                content_policy=content_policy,  # type: ignore[arg-type]
                target_track_ids=target_ids,
                engine="deterministic",
                instruction=selection_str(selection, "instruction"),
            )
            response = preview_reharmonization(reharm_request)
            realized = apply_realized_composition(
                current_draft=draft,
                realized=response.composition,
                service=RealizeService.REHARMONIZE_CANDIDATE,
            )
            working_draft_update = realized
            payload.update(
                {
                    "base_fingerprint": response.base_fingerprint,
                    "proposal_fingerprint": response.proposal_fingerprint,
                    "harmony_change_count": len(response.harmony_changes),
                    "track_change_count": len(response.track_changes),
                    "provider": response.provider,
                }
            )
            warning_codes.extend(list(response.warnings or [])[:16])
        except ReharmonizeError as exc:
            logger.info(
                "Harmony preview soft-failed; emitting recommendation without draft update",
                extra={
                    "agent_id": self._descriptor.id,
                    "code": getattr(exc, "code", type(exc).__name__),
                },
            )
            warning_codes.append(getattr(exc, "code", "reharmonize_error")[:80])
            payload["error_code"] = getattr(exc, "code", "reharmonize_error")

        art = AgentArtifactV1(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type="reharmonize.candidate",
            payload=payload,
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance("agent_harmony_propose", runtime="reharmonize_deterministic"),
            warning_codes=warning_codes,
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[art],
            updated_context_slots={"harmony_artifact": art},
            working_draft_update=working_draft_update,
            provenance_stage=self._stage(
                "agent_harmony_propose", runtime="reharmonize_deterministic"
            ),
            warning_codes=warning_codes,
        )
