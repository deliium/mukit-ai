"""Realize Ardour exchange intents via arrangement / development / harmony preview.

Locked map:
- counter_melody → arrangement create_countermelody (cello GM 42)
- arrangement_variation → arrangement change_instrumentation
- regenerate_region → development vary_section on alignment bars
- add_accompaniment → arrangement add_accompaniment (melody + acoustic_grand_piano harmony)
- orchestrate_selection → arrangement orchestrate_selected_tracks (violin/cello/strings)
- reharmonize_selection → deterministic reharmonize with preserve_harmony_adapt_melody

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
from app.harmony_schemas import (
    HarmonyReplaceOperation,
    HarmonySpanInput,
    ReharmonizeError,
    ReharmonizePreviewRequest,
)
from app.schemas import LLMModelSelection
from app.services.ardour_exchange_store import get_exchange_preview
from app.services.composition_harmony_timeline import apply_harmony_timeline_operation
from app.services.composition_reharmonization import (
    _invent_progression,
    preview_reharmonization,
)
from app.services.composition_timeline import compile_timeline
from app.services.composition_tonality import parse_key
from app.services.llm_composition_arrangement import run_composition_arrangement_preview
from app.services.llm_composition_development import run_composition_development_preview

logger = logging.getLogger(__name__)

_CELLO_INSTRUMENT_ID = "cello"
_PIANO_INSTRUMENT_ID = "acoustic_grand_piano"
_VIOLIN_INSTRUMENT_ID = "violin"
_STRING_ENSEMBLE_INSTRUMENT_ID = "string_ensemble_1"

_REHARMONIZE_TO_EXCHANGE: dict[str, str] = {
    "reharmonize_invalid_selection": "ardour_exchange_alignment_invalid",
    "reharmonize_invalid_targets": "ardour_exchange_realize_unsupported",
    "reharmonize_no_realizable_target": "ardour_exchange_realize_unsupported",
    "reharmonize_crossing_note": "ardour_exchange_realize_unsupported",
    "reharmonize_unsupported_symbol": "ardour_exchange_realize_unsupported",
    "reharmonize_modulation_required": "ardour_exchange_realize_unsupported",
    "reharmonize_tonicize_required": "ardour_exchange_realize_unsupported",
    "reharmonize_preservation_failed": "ardour_exchange_realize_unsupported",
    "reharmonize_inaudible_success": "ardour_exchange_realize_unsupported",
    "reharmonize_internal_error": "ardour_exchange_realize_unsupported",
}


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

    MIDI import often defaults role to ``other``; exchange intents treat
    the selected idea as the melody line without mutating the stored preview.
    """
    data = composition.model_dump(mode="json")
    source_set = set(source_ids)
    for track in data.get("tracks") or []:
        if track.get("id") in source_set:
            track["role"] = "melody"
    return CompositionV2.model_validate(data)


def _shift_pitch_token(pitch: str, semitones: int) -> str:
    """Shift a pitch token by semitones; fall back to original on parse failure."""
    try:
        from app.composition_schemas import midi_pitch_number
        from app.services.composition_import import midi_number_to_pitch

        midi = midi_pitch_number(pitch)
        shifted = max(0, min(127, midi + semitones))
        return midi_number_to_pitch(shifted)
    except Exception:
        return pitch


def _clone_source_as_role(
    composition: CompositionV2,
    *,
    source_track_id: str,
    role: str,
    track_id: str,
    pitch_semitones: int = 0,
    event_stride: int = 1,
) -> CompositionV2:
    """Clone one source track under a new id/role for role-aware redistribute.

    Typical Ardour MIDI selections are a single idea track; orchestrate after
    parts expect melody/bass/harmony sources. Clones are pitch-shifted / thinned
    so validation does not reject exact cross-instrument clones. Working-copy only.
    """
    data = composition.model_dump(mode="json")
    source = next((track for track in data.get("tracks") or [] if track.get("id") == source_track_id), None)
    if source is None:
        raise ArdourExchangeError(
            "ardour_exchange_realize_unsupported",
            details={"reason": "clone_source_missing", "track_id": source_track_id},
        )
    clone = dict(source)
    clone["id"] = track_id
    clone["role"] = role
    clone["name"] = f"{source.get('name') or 'Idea'} ({role})"
    events = []
    stride = max(1, int(event_stride))
    for index, event in enumerate(source.get("events") or []):
        if index % stride != 0:
            continue
        cloned_event = dict(event)
        event_id = cloned_event.get("id")
        cloned_event["id"] = f"{track_id}-e{index:04d}" if not event_id else f"{track_id}-{event_id}"
        if pitch_semitones and "pitch" in cloned_event:
            cloned_event["pitch"] = _shift_pitch_token(
                str(cloned_event["pitch"]),
                pitch_semitones,
            )
        events.append(cloned_event)
    if not events and source.get("events"):
        # Keep at least one event so redistribute can claim material.
        first = dict(source["events"][0])
        first["id"] = f"{track_id}-e0000"
        if pitch_semitones and "pitch" in first:
            first["pitch"] = _shift_pitch_token(str(first["pitch"]), pitch_semitones)
        events = [first]
    clone["events"] = events
    data.setdefault("tracks", []).append(clone)
    return CompositionV2.model_validate(data)


def _ensure_orchestrate_role_sources(
    composition: CompositionV2,
    source_ids: list[str],
) -> tuple[CompositionV2, list[str]]:
    """Ensure melody/bass/harmony sources exist for orchestrate redistribute."""
    working = composition
    roles_present = {str(track.role or "") for track in working.tracks}
    expanded = list(source_ids)
    primary = source_ids[0]
    if "bass" not in roles_present:
        bass_id = f"{primary}-orch-bass"
        working = _clone_source_as_role(
            working,
            source_track_id=primary,
            role="bass",
            track_id=bass_id,
            pitch_semitones=-12,
            event_stride=2,
        )
        expanded.append(bass_id)
        logger.debug(
            "Ardour exchange orchestrate cloned bass source",
            extra={"track_id": bass_id, "surface": "arrangement"},
        )
    else:
        expanded.extend(
            track.id
            for track in working.tracks
            if track.role == "bass" and track.id not in expanded and track.events
        )
    if "harmony" not in roles_present and "pad" not in roles_present:
        harmony_id = f"{primary}-orch-harmony"
        working = _clone_source_as_role(
            working,
            source_track_id=primary,
            role="harmony",
            track_id=harmony_id,
            pitch_semitones=-5,
            event_stride=3,
        )
        expanded.append(harmony_id)
        logger.debug(
            "Ardour exchange orchestrate cloned harmony source",
            extra={"track_id": harmony_id, "surface": "arrangement"},
        )
    else:
        expanded.extend(
            track.id
            for track in working.tracks
            if track.role in {"harmony", "pad"} and track.id not in expanded and track.events
        )
    # De-dupe while preserving order.
    seen: set[str] = set()
    unique: list[str] = []
    for track_id in expanded:
        if track_id in seen:
            continue
        seen.add(track_id)
        unique.append(track_id)
    return working, unique


def _map_reharmonize_error(exc: ReharmonizeError) -> ArdourExchangeError:
    code = _REHARMONIZE_TO_EXCHANGE.get(exc.code, "ardour_exchange_realize_unsupported")
    details: dict[str, Any] = {"reharmonize_code": exc.code}
    for key, value in (exc.details or {}).items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            details[key] = value
    return ArdourExchangeError(code, details=details)


def _seed_invented_harmony_if_empty(
    composition: CompositionV2,
    *,
    start_bar: int,
    end_bar: int,
) -> CompositionV2:
    """When MIDI import left ``harmony: []``, invent spans so melody can adapt.

    Uses the same invent helper as the reharmonize engine; does not mutate notes.
    """
    if composition.harmony:
        overlapping = False
        timeline = compile_timeline(composition)
        start_tick, end_tick = timeline.bar_range_ticks(start_bar, end_bar)
        for item in composition.harmony:
            if item.start_tick < end_tick and item.start_tick + item.duration_ticks > start_tick:
                overlapping = True
                break
        if overlapping:
            return composition
    else:
        timeline = compile_timeline(composition)
        start_tick, end_tick = timeline.bar_range_ticks(start_bar, end_bar)

    active_key_label = timeline.active_key(start_tick)
    active_key = parse_key(active_key_label)
    if active_key is None:
        raise ArdourExchangeError(
            "ardour_exchange_realize_unsupported",
            details={"reason": "unparseable_key", "active_key": active_key_label},
        )
    invented = _invent_progression(start_tick, end_tick, "reharmonize", active_key, None)
    logger.debug(
        "Ardour exchange reharmonize seeding invented harmony",
        extra={
            "span_count": len(invented),
            "start_bar": start_bar,
            "end_bar": end_bar,
            "surface": "harmony",
        },
    )
    result = apply_harmony_timeline_operation(
        composition,
        HarmonyReplaceOperation(
            start_tick=start_tick,
            duration_ticks=end_tick - start_tick,
            spans=[
                HarmonySpanInput(
                    start_tick=span.start_tick,
                    duration_ticks=span.duration_ticks,
                    chord=span.chord,
                )
                for span in invented
            ],
        ),
    )
    return result.composition


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


def build_add_accompaniment_request(
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
        _part("after-piano-harmony", _PIANO_INSTRUMENT_ID, role="harmony"),
    ]
    logger.debug(
        "Ardour exchange accompaniment builder",
        extra={
            "source_track_count": len(source_ids),
            "after_part_count": len(after),
            "surface": "arrangement",
        },
    )
    return CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": working.model_dump(mode="json"),
            "operation": "add_accompaniment",
            "source_track_ids": source_ids,
            "instrumentation": {"before": before, "after": after},
            "candidate_count": candidate_count,
            "instruction": instruction,
            "selection": LLMModelSelection().model_dump(mode="json"),
            "options": {"max_repairs": 1},
        }
    )


def build_orchestrate_selection_request(
    preview: ArdourExchangePreviewV1,
    *,
    candidate_count: int,
    instruction: str | None,
) -> CompositionArrangementPreviewRequest:
    composition = preview.draft_composition
    source_ids = _primary_source_track_ids(composition)
    working = _promote_source_roles_for_melody(composition, source_ids)
    working, orchestrate_ids = _ensure_orchestrate_role_sources(working, source_ids)
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
    # Include cloned support tracks in before inventory when present.
    for track in working.tracks:
        if track.id in source_ids:
            continue
        if track.id not in orchestrate_ids:
            continue
        if track.role == "bass":
            before.append(
                _part(
                    "before-bass",
                    _instrument_id_for_track(track),
                    role="bass",
                    source_track_ids=[track.id],
                )
            )
        elif track.role in {"harmony", "pad"}:
            before.append(
                _part(
                    "before-harmony",
                    _instrument_id_for_track(track),
                    role="harmony",
                    source_track_ids=[track.id],
                )
            )
    after = [
        _part("after-violin", _VIOLIN_INSTRUMENT_ID, role="melody"),
        _part("after-cello", _CELLO_INSTRUMENT_ID, role="bass"),
        _part("after-strings", _STRING_ENSEMBLE_INSTRUMENT_ID, role="harmony"),
    ]
    logger.debug(
        "Ardour exchange orchestrate builder",
        extra={
            "source_track_count": len(orchestrate_ids),
            "after_part_count": len(after),
            "surface": "arrangement",
        },
    )
    return CompositionArrangementPreviewRequest.model_validate(
        {
            "composition": working.model_dump(mode="json"),
            "operation": "orchestrate_selected_tracks",
            "source_track_ids": orchestrate_ids,
            "instrumentation": {"before": before, "after": after},
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


def build_reharmonize_selection_request(
    preview: ArdourExchangePreviewV1,
    *,
    instruction: str | None,
) -> ReharmonizePreviewRequest:
    alignment = preview.alignment
    start_bar = alignment.start_bar
    end_bar = alignment.start_bar + alignment.bar_count - 1
    composition = preview.draft_composition
    source_ids = _primary_source_track_ids(composition)
    working = _promote_source_roles_for_melody(composition, source_ids)
    try:
        working = _seed_invented_harmony_if_empty(
            working,
            start_bar=start_bar,
            end_bar=end_bar,
        )
    except ArdourExchangeError:
        raise
    except Exception as exc:
        logger.warning(
            "Ardour exchange reharmonize harmony seed failed",
            extra={"reason": type(exc).__name__},
        )
        raise ArdourExchangeError(
            "ardour_exchange_realize_unsupported",
            details={"reason": "harmony_seed_failed"},
        ) from exc
    logger.debug(
        "Ardour exchange reharmonize builder",
        extra={
            "source_track_count": len(source_ids),
            "start_bar": start_bar,
            "end_bar": end_bar,
            "surface": "harmony",
            "harmony_span_count": len(working.harmony),
        },
    )
    return ReharmonizePreviewRequest.model_validate(
        {
            "composition": working.model_dump(mode="json"),
            "selection": {"start_bar": start_bar, "end_bar": end_bar},
            "operation": "reharmonize",
            "content_policy": "preserve_harmony_adapt_melody",
            "target_track_ids": source_ids,
            "engine": "deterministic",
            "instruction": instruction,
        }
    )


def _normalize_harmony_realize_payload(response: Any) -> dict[str, Any]:
    """Shape a singleton candidate list the workflow UI can enumerate."""
    raw = response.model_dump(mode="json")
    candidate_id = f"harmony-{str(raw.get('proposal_fingerprint') or 'candidate')[:16]}"
    return {
        "operation": "reharmonize",
        "engine": "deterministic",
        "content_policy": "preserve_harmony_adapt_melody",
        "candidates": [
            {
                "candidate_id": candidate_id,
                "composition": raw["composition"],
                "base_fingerprint": raw.get("base_fingerprint"),
                "proposal_fingerprint": raw.get("proposal_fingerprint"),
                "harmony_changes": raw.get("harmony_changes") or [],
                "track_changes": raw.get("track_changes") or [],
                "preservation": raw.get("preservation") or [],
                "compatibility": raw.get("compatibility"),
                "warnings": raw.get("warnings") or [],
            }
        ],
        "preview": raw,
        "base_fingerprint": raw.get("base_fingerprint"),
        "proposal_fingerprint": raw.get("proposal_fingerprint"),
        "start_tick": raw.get("start_tick"),
        "end_tick": raw.get("end_tick"),
        "active_key": raw.get("active_key"),
        "recommended_target_track_ids": raw.get("recommended_target_track_ids") or [],
        "provider": raw.get("provider"),
        "model": raw.get("model"),
        "warnings": raw.get("warnings") or [],
    }


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
            "preview_id": active.preview_id,
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
    elif intent == "add_accompaniment":
        arrangement_request = build_add_accompaniment_request(
            active,
            candidate_count=request.candidate_count,
            instruction=request.instruction,
        )
        logger.debug(
            "Ardour exchange realize surface",
            extra={
                "intent": intent,
                "surface": "arrangement",
                "track_ids": list(arrangement_request.source_track_ids),
            },
        )
        response = await run_composition_arrangement_preview(arrangement_request)
        operation = "add_accompaniment"
        surface = "arrangement"
        payload = response.model_dump(mode="json")
    elif intent == "orchestrate_selection":
        arrangement_request = build_orchestrate_selection_request(
            active,
            candidate_count=request.candidate_count,
            instruction=request.instruction,
        )
        logger.debug(
            "Ardour exchange realize surface",
            extra={
                "intent": intent,
                "surface": "arrangement",
                "track_ids": list(arrangement_request.source_track_ids),
            },
        )
        response = await run_composition_arrangement_preview(arrangement_request)
        operation = "orchestrate_selected_tracks"
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
    elif intent == "reharmonize_selection":
        reharm_request = build_reharmonize_selection_request(
            active,
            instruction=request.instruction,
        )
        logger.debug(
            "Ardour exchange realize surface",
            extra={
                "intent": intent,
                "surface": "harmony",
                "track_ids": list(reharm_request.target_track_ids),
            },
        )
        try:
            response = preview_reharmonization(reharm_request)
        except ReharmonizeError as exc:
            logger.warning(
                "Ardour exchange reharmonize mapped error",
                extra={
                    "intent": intent,
                    "reharmonize_code": exc.code,
                    "code": _REHARMONIZE_TO_EXCHANGE.get(
                        exc.code, "ardour_exchange_realize_unsupported"
                    ),
                },
            )
            raise _map_reharmonize_error(exc) from exc
        operation = "reharmonize"
        surface = "harmony"
        payload = _normalize_harmony_realize_payload(response)
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
            "preview_id": active.preview_id,
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
