"""Realize Ardour exchange intents via arrangement / development preview.

Locked map:
- counter_melody → arrangement create_countermelody (cello GM 42)
- arrangement_variation → arrangement change_instrumentation
- regenerate_region → development vary_section on alignment bars

Does not Apply. Does not import ``ai_agents/``.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from app.ardour_exchange_schemas import (
    ArdourExchangeError,
    ArdourExchangePreviewV1,
    ArdourExchangeRealizeIntent,
    ArdourExchangeRealizeRequestV1,
)
from app.arrangement_schemas import CompositionArrangementPreviewRequest
from app.composition_development_schemas import CompositionDevelopmentPreviewRequest
from app.composition_schemas import CompositionV2
from app.schemas import LLMModelSelection
from app.services.ardour_exchange_store import get_exchange_preview
from app.services.llm_composition_arrangement import run_composition_arrangement_preview
from app.services.llm_composition_development import run_composition_development_preview

logger = logging.getLogger(__name__)

_CELLO_INSTRUMENT_ID = "cello"
_PIANO_INSTRUMENT_ID = "acoustic_grand_piano"
_VIOLIN_INSTRUMENT_ID = "violin"


def _correlation_id(preview: ArdourExchangePreviewV1) -> str:
    return f"aexr_{preview.preview_id}_{secrets.token_hex(4)}"


def _primary_source_track_ids(composition: CompositionV2) -> list[str]:
    melody = [track.id for track in composition.tracks if track.role == "melody"]
    if melody:
        return melody
    # Import often defaults role to other — use all non-empty tracks.
    with_notes = [track.id for track in composition.tracks if track.events]
    if with_notes:
        return with_notes
    if composition.tracks:
        return [composition.tracks[0].id]
    raise ArdourExchangeError(
        "ardour_exchange_realize_unsupported",
        details={"reason": "no_source_tracks"},
    )


def _instrument_id_for_track(track: Any) -> str:
    label = str(getattr(track, "instrument", "") or "").strip().lower()
    if "cello" in label:
        return _CELLO_INSTRUMENT_ID
    if "violin" in label:
        return _VIOLIN_INSTRUMENT_ID
    if "piano" in label or "grand" in label:
        return _PIANO_INSTRUMENT_ID
    program = getattr(track, "midi_program", None)
    if program == 42:
        return _CELLO_INSTRUMENT_ID
    if program == 40:
        return _VIOLIN_INSTRUMENT_ID
    return _PIANO_INSTRUMENT_ID


def _part(
    part_id: str,
    instrument_id: str,
    *,
    role: str | None = None,
    source_track_ids: list[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "part_id": part_id,
        "instrument_id": instrument_id,
        "doubling_policy": "none",
    }
    if role is not None:
        payload["role"] = role
    if source_track_ids:
        payload["source_track_ids"] = list(source_track_ids)
    return payload


def _promote_source_roles_for_melody(composition: CompositionV2, source_ids: list[str]) -> CompositionV2:
    """Return a copy where source tracks are melody so before inventory can match.

    MIDI import often defaults role to ``other``; exchange counter-melody treats
    the selected idea as the melody line without mutating the stored preview.
    """
    data = composition.model_dump(mode="json")
    source_set = set(source_ids)
    for track in data.get("tracks") or []:
        if track.get("id") in source_set:
            track["role"] = "melody"
    return CompositionV2.model_validate(data)


def build_counter_melody_request(
    preview: ArdourExchangePreviewV1,
    *,
    candidate_count: int,
    instruction: str | None,
) -> CompositionArrangementPreviewRequest:
    composition = preview.draft_composition
    source_ids = _primary_source_track_ids(composition)
    working = _promote_source_roles_for_melody(composition, source_ids)
    source_track = next(t for t in working.tracks if t.id == source_ids[0])
    source_instrument = _instrument_id_for_track(source_track)
    before = [
        _part(
            "before-melody",
            source_instrument,
            role="melody",
            source_track_ids=source_ids,
        )
    ]
    after = [
        _part("after-melody", source_instrument, role="melody"),
        _part("after-cello-cm", _CELLO_INSTRUMENT_ID, role="countermelody"),
    ]
    return CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": working.model_dump(mode="json"),
            "operation": "create_countermelody",
            "source_track_ids": source_ids,
            "instrumentation": {"before": before, "after": after},
            "candidate_count": candidate_count,
            "instruction": instruction,
            "selection": LLMModelSelection().model_dump(mode="json"),
            "options": {"max_repairs": 1},
        }
    )


def build_arrangement_variation_request(
    preview: ArdourExchangePreviewV1,
    *,
    candidate_count: int,
    instruction: str | None,
) -> CompositionArrangementPreviewRequest:
    composition = preview.draft_composition
    source_ids = _primary_source_track_ids(composition)
    working = _promote_source_roles_for_melody(composition, source_ids)
    source_track = next(t for t in working.tracks if t.id == source_ids[0])
    source_instrument = _instrument_id_for_track(source_track)
    after_instrument = (
        _VIOLIN_INSTRUMENT_ID
        if source_instrument != _VIOLIN_INSTRUMENT_ID
        else _CELLO_INSTRUMENT_ID
    )
    return CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": working.model_dump(mode="json"),
            "operation": "change_instrumentation",
            "source_track_ids": source_ids,
            "instrumentation": {
                "before": [
                    _part(
                        "before-melody",
                        source_instrument,
                        role="melody",
                        source_track_ids=source_ids,
                    )
                ],
                "after": [_part("after-melody", after_instrument, role="melody")],
            },
            "candidate_count": candidate_count,
            "instruction": instruction,
            "selection": LLMModelSelection().model_dump(mode="json"),
            "options": {"max_repairs": 1},
        }
    )


def build_regenerate_region_request(
    preview: ArdourExchangePreviewV1,
    *,
    candidate_count: int,
    instruction: str | None,
) -> CompositionDevelopmentPreviewRequest:
    alignment = preview.alignment
    start_bar = alignment.start_bar
    end_bar = alignment.start_bar + alignment.bar_count - 1
    return CompositionDevelopmentPreviewRequest.model_validate(
        {
            "composition": preview.draft_composition.model_dump(mode="json"),
            "operation": "vary_section",
            "source": {"start_bar": start_bar, "end_bar": end_bar},
            "variation_strength": "balanced",
            "candidate_count": candidate_count,
            "instruction": instruction,
            "selection": LLMModelSelection().model_dump(mode="json"),
            "options": {"max_repairs": 1},
        }
    )


async def realize_exchange_intent(
    request: ArdourExchangeRealizeRequestV1,
    *,
    preview: ArdourExchangePreviewV1 | None = None,
) -> dict[str, Any]:
    """Run the locked preview pipeline and return its payload + correlation id."""
    active = preview if preview is not None else get_exchange_preview()
    if active is None:
        raise ArdourExchangeError("ardour_exchange_preview_missing")

    intent: ArdourExchangeRealizeIntent = request.intent
    correlation_id = _correlation_id(active)
    logger.info(
        "Ardour exchange realize start",
        extra={
            "intent": intent,
            "correlation_id": correlation_id,
            "package_id": active.package_id,
        },
    )

    if intent == "counter_melody":
        arrangement_request = build_counter_melody_request(
            active,
            candidate_count=request.candidate_count,
            instruction=request.instruction,
        )
        response = await run_composition_arrangement_preview(arrangement_request)
        operation = "create_countermelody"
        surface = "arrangement"
        payload = response.model_dump(mode="json")
    elif intent == "arrangement_variation":
        arrangement_request = build_arrangement_variation_request(
            active,
            candidate_count=request.candidate_count,
            instruction=request.instruction,
        )
        response = await run_composition_arrangement_preview(arrangement_request)
        operation = "change_instrumentation"
        surface = "arrangement"
        payload = response.model_dump(mode="json")
    elif intent == "regenerate_region":
        development_request = build_regenerate_region_request(
            active,
            candidate_count=request.candidate_count,
            instruction=request.instruction,
        )
        response = await run_composition_development_preview(development_request)
        operation = "vary_section"
        surface = "development"
        payload = response.model_dump(mode="json")
    else:
        logger.warning(
            "Ardour exchange realize unsupported",
            extra={"intent": intent, "code": "ardour_exchange_realize_unsupported"},
        )
        raise ArdourExchangeError(
            "ardour_exchange_realize_unsupported",
            details={"intent": str(intent)},
        )

    candidate_count = len(payload.get("candidates") or [])
    logger.info(
        "Ardour exchange realize complete",
        extra={
            "intent": intent,
            "operation": operation,
            "correlation_id": correlation_id,
            "candidate_count": candidate_count,
            "surface": surface,
        },
    )
    return {
        "correlation_id": correlation_id,
        "intent": intent,
        "operation": operation,
        "surface": surface,
        "package_id": active.package_id,
        "preview_id": active.preview_id,
        "preview": payload,
    }
