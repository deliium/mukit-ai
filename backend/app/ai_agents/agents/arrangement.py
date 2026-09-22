"""Arrangement agent — wraps arrangement context + deterministic reinstrument realize."""

from __future__ import annotations

import logging
from typing import Any

from app.ai_agents.agents.common import BaseMusicAgent, selection_str
from app.ai_agents.agents.typed_emit import (
    AGENT_ARRANGEMENT_PLAN_SCHEMA,
    AGENT_COMPOSITION_PATCH_SCHEMA,
    arrangement_plan_from_composition,
    composition_patch_payload,
    depends_on_edges,
    make_plan_artifact,
    parent_ids_from_context,
)
from app.ai_agents.progressive_realize import RealizeService, apply_realized_composition
from app.ai_agents.schemas import (
    AgentArtifactKind,
    AgentArtifactV1,
    AgentRunRequest,
    AgentRunResult,
)
from app.arrangement_schemas import (
    CompositionArrangementDraft,
    CompositionArrangementError,
    CompositionArrangementPreviewRequest,
)
from app.llm_settings import load_llm_settings
from app.schemas import LLMModelSelection
from app.services.composition_arrangement_context import build_arrangement_source_context
from app.services.composition_arrangement_patch import realize_arrangement_draft
from app.services.composition_arrangement_validation import validate_arrangement_candidate
from app.services.instrument_catalog import get_catalog, resolve_profile
from app.services.llm_composition_arrangement import run_composition_arrangement_preview

logger = logging.getLogger(__name__)

_ALT_PIANO = "bright_acoustic_piano"
_FALLBACK_ALT = "violin"


def _catalog_instrument_id(track: Any) -> str:
    profile = resolve_profile(
        alias=getattr(track, "instrument", None),
        midi_program=getattr(track, "midi_program", None),
        instrument_label=getattr(track, "instrument", None),
        catalog=get_catalog(),
    )
    if profile is not None:
        return profile.instrument_id
    return "acoustic_grand_piano"


def _pick_alternate_instrument(current_id: str) -> str:
    if current_id != _ALT_PIANO:
        return _ALT_PIANO
    return _FALLBACK_ALT


def _default_reinstrument_request(composition: Any) -> CompositionArrangementPreviewRequest:
    """Build change_instrumentation that reinstruments one non-melody track."""
    tracks = list(composition.tracks)
    if not tracks:
        raise CompositionArrangementError(
            "arrangement_invalid_operation",
            details={"reason": "no_tracks"},
        )

    target_index = 0
    for index, track in enumerate(tracks):
        role = str(getattr(track, "role", None) or "")
        if role not in {"melody", "lead"}:
            target_index = index
            break

    before = []
    after = []
    for index, track in enumerate(tracks):
        instrument_id = _catalog_instrument_id(track)
        role = getattr(track, "role", None) or "harmony"
        part_id = f"part-{index + 1}"
        before.append(
            {
                "part_id": part_id,
                "instrument_id": instrument_id,
                "role": role,
                "source_track_ids": [track.id],
                "doubling_policy": "none",
            }
        )
        after_instrument = (
            _pick_alternate_instrument(instrument_id)
            if index == target_index
            else instrument_id
        )
        after.append(
            {
                "part_id": part_id,
                "instrument_id": after_instrument,
                "role": role,
                "source_track_ids": [track.id],
                "doubling_policy": "none",
            }
        )

    return CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": composition,
            "operation": "change_instrumentation",
            "source_track_ids": [track.id for track in tracks],
            "instrumentation": {"before": before, "after": after},
            "preserve_melody": True,
            "preserve_harmony": True,
            "candidate_count": 1,
            "allow_unlisted_after": False,
            "selection": LLMModelSelection(),
            "options": {"max_repairs": 0},
        }
    )


def _draft_parts_for_request(arr_request: CompositionArrangementPreviewRequest) -> list[dict[str, Any]]:
    before_by_id = {part.part_id: part for part in arr_request.instrumentation.before}
    parts: list[dict[str, Any]] = []
    for after_part in arr_request.instrumentation.after:
        before = before_by_id.get(after_part.part_id)
        action = "retain"
        if before is not None and before.instrument_id != after_part.instrument_id:
            action = "reinstrument"
        parts.append(
            {
                "action": action,
                "part_id": after_part.part_id,
                "source_track_ids": list(after_part.source_track_ids),
                "source_note_refs": [],
                "notes": [],
            }
        )
    return parts


class ArrangementAgent(BaseMusicAgent):
    """Propose arrangement via reinstrument realize or full preview when selected."""

    async def _run_impl(self, request: AgentRunRequest) -> AgentRunResult:
        draft = request.context.working_draft_composition
        selection = request.selection or {}
        warning_codes: list[str] = []
        working_draft_update = None
        payload: dict[str, Any] = {
            "schema": "arrangement.candidate",
            "track_count": len(draft.tracks),
        }

        mode = selection_str(selection, "mode", "reinstrument") or "reinstrument"
        try:
            if mode == "preview" and isinstance(selection.get("arrangement_preview"), dict):
                preview_req = CompositionArrangementPreviewRequest.model_validate(
                    {
                        "composition": draft,
                        **selection["arrangement_preview"],
                    }
                )
                response = await run_composition_arrangement_preview(
                    preview_req, settings=load_llm_settings()
                )
                if not response.candidates:
                    warning_codes.append("arrangement_no_candidates")
                    payload["rejected_count"] = len(response.rejected_attempts)
                else:
                    candidate = response.candidates[0]
                    realized = apply_realized_composition(
                        current_draft=draft,
                        realized=candidate.composition,
                        service=RealizeService.ARRANGEMENT_CANDIDATE,
                    )
                    working_draft_update = realized
                    payload.update(
                        {
                            "candidate_id": candidate.candidate_id,
                            "candidate_fingerprint": candidate.candidate_fingerprint,
                            "operation": candidate.operation,
                            "provider": candidate.provider,
                        }
                    )
                    warning_codes.extend(list(candidate.warning_codes or [])[:16])
            else:
                arr_request = _default_reinstrument_request(draft)
                context = build_arrangement_source_context(arr_request)
                arrangement_draft = CompositionArrangementDraft.model_validate(
                    {"parts": _draft_parts_for_request(arr_request)}
                )
                realized_bundle = realize_arrangement_draft(
                    arr_request,
                    arrangement_draft,
                    context=context,
                    candidate_ordinal=1,
                )
                validation = validate_arrangement_candidate(
                    arr_request,
                    realized_bundle,
                    context=context,
                )
                if not validation.ok:
                    warning_codes.append("arrangement_validation_soft_fail")
                    codes = list(validation.rejection_codes or validation.error_codes or [])[:8]
                    warning_codes.extend(str(c)[:80] for c in codes)
                realized = apply_realized_composition(
                    current_draft=draft,
                    realized=realized_bundle.composition,
                    service=RealizeService.ARRANGEMENT_CANDIDATE,
                )
                working_draft_update = realized
                payload.update(
                    {
                        "mode": "reinstrument",
                        "operation": "change_instrumentation",
                        "validated": bool(validation.ok),
                    }
                )
        except CompositionArrangementError as exc:
            logger.info(
                "Arrangement soft-failed",
                extra={
                    "agent_id": self._descriptor.id,
                    "code": getattr(exc, "code", type(exc).__name__),
                },
            )
            warning_codes.append(getattr(exc, "code", "arrangement_error")[:80])
            payload["error_code"] = getattr(exc, "code", "arrangement_error")
            # Pass-through validated draft so spine still yields a fingerprintable V2.
            working_draft_update = apply_realized_composition(
                current_draft=draft,
                realized=draft,
                service=RealizeService.VALIDATED_V2,
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "Arrangement unexpected soft-fail",
                extra={"agent_id": self._descriptor.id, "reason": type(exc).__name__},
            )
            warning_codes.append(f"arrangement_{type(exc).__name__}"[:80])
            payload["error"] = type(exc).__name__
            working_draft_update = apply_realized_composition(
                current_draft=draft,
                realized=draft,
                service=RealizeService.VALIDATED_V2,
            )

        art = AgentArtifactV1(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type="arrangement.candidate",
            payload=payload,
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance(
                "agent_arrangement_propose", runtime="arrangement_service"
            ),
            warning_codes=warning_codes,
        )
        form = request.context.structure_plan
        harmony = request.context.harmony_artifact
        arrangement_plan = make_plan_artifact(
            kind=AgentArtifactKind.PLAN,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_ARRANGEMENT_PLAN_SCHEMA,
            payload=arrangement_plan_from_composition(working_draft_update or draft),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance(
                "agent_arrangement_propose", runtime="arrangement_service"
            ),
            parent_artifact_ids=parent_ids_from_context(
                request.context, "structure_plan", "harmony_artifact", "melody_artifact"
            ),
            depends_on=depends_on_edges(form, harmony),
        )
        patch = make_plan_artifact(
            kind=AgentArtifactKind.CANDIDATE_PATCH,
            producer_agent_id=self._descriptor.id,
            content_type=AGENT_COMPOSITION_PATCH_SCHEMA,
            payload=composition_patch_payload(
                realize_service="arrangement_candidate",
                op_refs=["reinstrument_or_preview"],
                recipe="arrangement_service",
                source_fingerprint=request.context.source_fingerprint,
            ),
            source_fingerprint=request.context.source_fingerprint,
            provenance=self._provenance(
                "agent_arrangement_propose", runtime="arrangement_service"
            ),
            parent_artifact_ids=[arrangement_plan.artifact_id],
            depends_on=depends_on_edges(arrangement_plan),
            warning_codes=warning_codes,
        )
        logger.info(
            "Arrangement typed plans produced",
            extra={
                "agent_id": self._descriptor.id,
                "content_types": [
                    arrangement_plan.content_type,
                    patch.content_type,
                    art.content_type,
                ],
            },
        )
        return AgentRunResult(
            agent_id=self._descriptor.id,
            operation=request.operation,
            artifacts=[arrangement_plan, patch, art],
            updated_context_slots={
                "arrangement_candidate": arrangement_plan,
            },
            working_draft_update=working_draft_update,
            provenance_stage=self._stage(
                "agent_arrangement_propose", runtime="arrangement_service"
            ),
            warning_codes=warning_codes,
        )
