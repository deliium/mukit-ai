"""LangGraph-backed partial Composition V1 region editing."""

from __future__ import annotations

import json
import logging
from typing import Any, TypedDict

from pydantic import ValidationError

from ..llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from ..schemas import (
    Composition,
    CompositionRegionReplacementPatch,
    LLMCompositionEditRequest,
)
from .composition_region_patch import (
    CompositionRegionPatchError,
    RegionSelectionSummary,
    apply_region_replacement_patch,
    summarize_region_selection,
)
from .llm_music_generator import (
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    _extract_json,
    _select_provider,
)


logger = logging.getLogger(__name__)

EDIT_STAGES = (
    "analyze_edit_scope",
    "draft_region_patch",
    "validate_patch",
    "repair_patch",
    "apply_patch",
)

MAX_EDIT_REPAIR_ATTEMPTS = 1


class _EditState(TypedDict, total=False):
    request: LLMCompositionEditRequest
    provider: LLMProviderSettings
    scope_summary: RegionSelectionSummary
    raw_output: str
    parsed_json: dict[str, Any]
    patch: CompositionRegionReplacementPatch
    composition: Composition
    warnings: list[str]
    diagnostics: list[dict[str, Any]]
    validation_ok: bool
    current_stage: str
    failed_stage: str
    repair_count: int
    retry_limit: int


def _selected_edit_model(request: LLMCompositionEditRequest, provider: LLMProviderSettings) -> str:
    return request.selection.model or provider.model


def _sanitized_instruction(instruction: str) -> str:
    return instruction.strip()[:200]


def _scope_log_extra(summary: RegionSelectionSummary | None) -> dict[str, Any]:
    if summary is None:
        return {}
    return {
        "start_bar": summary.bounds.start_bar,
        "end_bar": summary.bounds.end_bar,
        "target_track_count": len(summary.target_track_ids),
        "in_region_event_count": summary.total_in_region_events,
        "outside_region_event_count": summary.total_outside_region_events,
    }


async def edit_composition_region(
    request: LLMCompositionEditRequest,
    settings: LLMSettings | None = None,
) -> tuple[Composition, CompositionRegionReplacementPatch, list[str], LLMProviderSettings]:
    """Edit only the selected Composition V1 region via a replace_region patch graph."""
    active_settings = settings or load_llm_settings()
    # Reuse provider selection from the full-generation service; request shape shares selection fields.
    provider = _select_provider(request, active_settings)  # type: ignore[arg-type]
    model_name = _selected_edit_model(request, provider)

    from .fake_llm import FakeLLMError, edit_fake_composition_region, is_fake_provider

    if is_fake_provider(provider):
        logger.info(
            "Routing composition region edit to fake LLM provider",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "start_bar": request.edit.selection.start_bar,
                "end_bar": request.edit.selection.end_bar,
            },
        )
        try:
            return await edit_fake_composition_region(request, provider)
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    logger.info(
        "LLM composition region edit started",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "start_bar": request.edit.selection.start_bar,
            "end_bar": request.edit.selection.end_bar,
            "target_track_count": len(request.edit.selection.track_ids or []),
            "allow_harmony_changes": request.edit.allow_harmony_changes,
            "allow_added_tracks": request.edit.allow_added_tracks,
            "instruction_length": len(request.edit.instruction),
        },
    )
    logger.debug(
        "LLM composition region edit scope request",
        extra={
            "schema_version": request.composition.schema_version,
            "bar_count": request.composition.bar_count,
            "track_count": len(request.composition.tracks),
            "instruction_preview": _sanitized_instruction(request.edit.instruction),
            "track_ids": request.edit.selection.track_ids,
            "section_type": request.edit.selection.section_type,
        },
    )

    # Original composition object must never be mutated.
    original = request.composition.model_copy(deep=True)
    state: _EditState = {
        "request": request.model_copy(update={"composition": original}),
        "provider": provider,
        "warnings": [],
        "diagnostics": [],
        "validation_ok": False,
        "repair_count": 0,
        "retry_limit": min(request.options.max_retries, MAX_EDIT_REPAIR_ATTEMPTS),
        "current_stage": "analyze_edit_scope",
    }

    graph = _build_edit_graph()
    final_state: _EditState = await graph.ainvoke(state)

    if not final_state.get("validation_ok") or final_state.get("composition") is None or final_state.get("patch") is None:
        diagnostics = final_state.get("diagnostics") or []
        logger.error(
            "LLM composition region edit failed after validation/repair",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "failed_stage": final_state.get("failed_stage") or final_state.get("current_stage"),
                "repair_count": final_state.get("repair_count", 0),
                "diagnostic_count": len(diagnostics),
                "diagnostic_codes": [item.get("code") for item in diagnostics[:10]],
            },
        )
        detail = diagnostics[0].get("message") if diagnostics else "Region edit patch validation failed"
        raise InvalidLLMOutputError(str(detail)[:500])

    composition = final_state["composition"]
    patch = final_state["patch"]
    warnings = list(final_state.get("warnings") or [])
    event_count = sum(len(track.events) for track in composition.tracks)
    logger.info(
        "LLM composition region edit completed",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "operation": patch.operation,
            "start_bar": patch.start_bar,
            "end_bar": patch.end_bar,
            "target_track_count": len(patch.target_track_ids or []),
            "patch_warning_count": len(patch.warnings),
            "warning_count": len(warnings),
            "final_event_count": event_count,
            "repair_count": final_state.get("repair_count", 0),
        },
    )
    return composition, patch, warnings, provider


def _build_edit_graph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise LLMGenerationError("LangGraph dependencies are not installed") from exc

    logger.info("Building LLM composition region edit graph", extra={"stages": list(EDIT_STAGES)})
    workflow = StateGraph(_EditState)
    workflow.add_node("analyze_edit_scope", _analyze_edit_scope)
    workflow.add_node("draft_region_patch", _draft_region_patch)
    workflow.add_node("validate_patch", _validate_patch)
    workflow.add_node("repair_patch", _repair_patch)
    workflow.add_node("apply_patch", _apply_patch_noop)

    workflow.set_entry_point("analyze_edit_scope")
    workflow.add_edge("analyze_edit_scope", "draft_region_patch")
    workflow.add_edge("draft_region_patch", "validate_patch")
    workflow.add_conditional_edges(
        "validate_patch",
        _route_after_validation,
        {
            "apply": "apply_patch",
            "repair": "repair_patch",
            "fail": END,
        },
    )
    workflow.add_edge("repair_patch", "validate_patch")
    workflow.add_edge("apply_patch", END)
    return workflow.compile()


def _analyze_edit_scope(state: _EditState) -> _EditState:
    request = state["request"]
    logger.debug(
        "Edit graph stage transition",
        extra={"stage": "analyze_edit_scope", "from_stage": state.get("current_stage")},
    )
    try:
        summary = summarize_region_selection(request.composition, request.edit.selection)
    except CompositionRegionPatchError as exc:
        logger.error(
            "Edit scope analysis failed",
            extra={"stage": "analyze_edit_scope", "error_type": type(exc).__name__, "code": exc.code},
        )
        raise InvalidLLMOutputError(str(exc)) from exc

    logger.debug(
        "Analyzed composition edit scope",
        extra={
            "stage": "analyze_edit_scope",
            **_scope_log_extra(summary),
            "target_track_ids": summary.target_track_ids,
        },
    )
    return {
        **state,
        "scope_summary": summary,
        "current_stage": "analyze_edit_scope",
        "failed_stage": "",
    }


async def _draft_region_patch(state: _EditState) -> _EditState:
    request = state["request"]
    summary = state["scope_summary"]
    logger.debug(
        "Edit graph stage transition",
        extra={"stage": "draft_region_patch", "from_stage": state.get("current_stage")},
    )
    prompt = _build_draft_prompt(request, summary, diagnostics=None)
    try:
        raw_output = await _invoke_edit_chat(state, prompt)
        parsed = _extract_json(raw_output)
        patch = CompositionRegionReplacementPatch.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError, LLMGenerationError) as exc:
        logger.error(
            "Draft region patch failed",
            extra={
                "stage": "draft_region_patch",
                "error_type": type(exc).__name__,
                **_scope_log_extra(summary),
            },
        )
        if isinstance(exc, LLMGenerationError) and not isinstance(exc, InvalidLLMOutputError):
            raise
        raise InvalidLLMOutputError(f"LLM returned invalid region patch JSON: {str(exc)[:200]}") from exc

    logger.debug(
        "Drafted region patch shape",
        extra={
            "stage": "draft_region_patch",
            "operation": patch.operation,
            "start_bar": patch.start_bar,
            "end_bar": patch.end_bar,
            "replace_track_count": len(patch.replace_tracks),
            "added_track_count": len(patch.added_tracks),
            "has_harmony_patch": patch.harmony_patch is not None,
            "warning_count": len(patch.warnings),
        },
    )
    return {
        **state,
        "raw_output": raw_output,
        "parsed_json": parsed,
        "patch": patch,
        "current_stage": "draft_region_patch",
        "failed_stage": "",
        "validation_ok": False,
    }


def _validate_patch(state: _EditState) -> _EditState:
    request = state["request"]
    patch = state.get("patch")
    summary = state.get("scope_summary")
    logger.debug(
        "Edit graph stage transition",
        extra={"stage": "validate_patch", "from_stage": state.get("current_stage")},
    )
    if patch is None:
        diagnostic = {"code": "missing_patch", "message": "No replace_region patch was produced"}
        logger.warning(
            "Region patch validation rejected",
            extra={"stage": "validate_patch", "diagnostic_codes": [diagnostic["code"]]},
        )
        return {
            **state,
            "validation_ok": False,
            "diagnostics": [diagnostic],
            "failed_stage": "validate_patch",
            "current_stage": "validate_patch",
        }

    try:
        result = apply_region_replacement_patch(
            request.composition,
            patch,
            selection=request.edit.selection,
            allow_added_tracks=request.edit.allow_added_tracks,
            allow_harmony_changes=request.edit.allow_harmony_changes,
            validate_integrity=True,
        )
    except CompositionRegionPatchError as exc:
        diagnostic = {"code": exc.code, "message": str(exc), "context": exc.context}
        logger.warning(
            "Region patch validation rejected",
            extra={
                "stage": "validate_patch",
                "diagnostic_codes": [exc.code],
                "repair_count": state.get("repair_count", 0),
                **_scope_log_extra(summary),
            },
        )
        return {
            **state,
            "validation_ok": False,
            "diagnostics": [diagnostic],
            "failed_stage": "validate_patch",
            "current_stage": "validate_patch",
        }

    warnings = list(state.get("warnings") or []) + list(result.warnings)
    logger.debug(
        "Region patch validation passed",
        extra={
            "stage": "validate_patch",
            "replaced_event_count": result.replaced_event_count,
            "preserved_event_count": result.preserved_event_count,
            "added_track_count": result.added_track_count,
            **_scope_log_extra(summary),
        },
    )
    return {
        **state,
        "composition": result.composition,
        "patch": result.patch,
        "warnings": warnings,
        "diagnostics": [],
        "validation_ok": True,
        "failed_stage": "",
        "current_stage": "validate_patch",
    }


async def _repair_patch(state: _EditState) -> _EditState:
    request = state["request"]
    summary = state["scope_summary"]
    diagnostics = state.get("diagnostics") or []
    repair_count = int(state.get("repair_count") or 0) + 1
    logger.warning(
        "Attempting region patch repair",
        extra={
            "stage": "repair_patch",
            "target_stage": "draft_region_patch",
            "repair_count": repair_count,
            "diagnostic_count": len(diagnostics),
            "diagnostic_codes": [item.get("code") for item in diagnostics[:10]],
            **_scope_log_extra(summary),
        },
    )
    prompt = _build_draft_prompt(request, summary, diagnostics=diagnostics)
    try:
        raw_output = await _invoke_edit_chat(state, prompt)
        parsed = _extract_json(raw_output)
        patch = CompositionRegionReplacementPatch.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError, LLMGenerationError) as exc:
        logger.error(
            "Region patch repair exhausted",
            extra={
                "stage": "repair_patch",
                "error_type": type(exc).__name__,
                "repair_count": repair_count,
                "diagnostic_count": len(diagnostics),
            },
        )
        if isinstance(exc, LLMGenerationError) and not isinstance(exc, InvalidLLMOutputError):
            raise
        raise InvalidLLMOutputError(f"LLM repair returned invalid region patch JSON: {str(exc)[:200]}") from exc

    logger.debug(
        "Repaired region patch shape",
        extra={
            "stage": "repair_patch",
            "operation": patch.operation,
            "replace_track_count": len(patch.replace_tracks),
            "added_track_count": len(patch.added_tracks),
            "repair_count": repair_count,
        },
    )
    return {
        **state,
        "raw_output": raw_output,
        "parsed_json": parsed,
        "patch": patch,
        "repair_count": repair_count,
        "validation_ok": False,
        "current_stage": "repair_patch",
        "failed_stage": "",
    }


def _apply_patch_noop(state: _EditState) -> _EditState:
    """Terminal stage after successful validation; composition already applied immutably."""
    logger.debug(
        "Edit graph stage transition",
        extra={"stage": "apply_patch", "from_stage": state.get("current_stage"), "validation_ok": state.get("validation_ok")},
    )
    return {**state, "current_stage": "apply_patch"}


def _route_after_validation(state: _EditState) -> str:
    if state.get("validation_ok"):
        return "apply"
    repair_count = int(state.get("repair_count") or 0)
    retry_limit = int(state.get("retry_limit") or 0)
    if repair_count < retry_limit:
        return "repair"
    logger.error(
        "Region patch repair exhausted",
        extra={
            "stage": "validate_patch",
            "repair_count": repair_count,
            "retry_limit": retry_limit,
            "diagnostic_codes": [item.get("code") for item in (state.get("diagnostics") or [])[:10]],
        },
    )
    return "fail"


def _build_draft_prompt(
    request: LLMCompositionEditRequest,
    summary: RegionSelectionSummary,
    diagnostics: list[dict[str, Any]] | None,
) -> str:
    selection = request.edit.selection
    composition = request.composition
    target_ids = summary.target_track_ids
    tracks_payload = []
    for track in composition.tracks:
        if track.id not in set(target_ids):
            continue
        in_region = [
            event.model_dump(mode="json")
            for event in track.events
            if summary.bounds.start_tick <= event.start_tick < summary.bounds.end_tick
            or (
                event.start_tick < summary.bounds.end_tick
                and event.start_tick + event.duration_ticks > summary.bounds.start_tick
            )
        ]
        tracks_payload.append(
            {
                "id": track.id,
                "name": track.name,
                "role": track.role,
                "instrument": track.instrument,
                "in_region_events": in_region,
            }
        )

    harmony_in_region = [
        item.model_dump(mode="json")
        for item in composition.harmony
        if selection.start_bar <= item.bar <= selection.end_bar
    ]

    contract = {
        "schema_version": "composition.v1",
        "operation": "replace_region",
        "start_bar": selection.start_bar,
        "end_bar": selection.end_bar,
        "target_track_ids": target_ids,
        "replace_tracks": [{"track_id": "<id>", "events": []}],
        "added_tracks": [],
        "harmony_patch": None,
        "warnings": [],
    }

    rules = [
        "Return ONLY a JSON object matching the replace_region patch contract.",
        "Do not return a full composition.",
        "Replace only notes inside the selected inclusive bar range for target tracks.",
        "All replacement event start/end ticks must stay within the selected region tick bounds.",
        "Preserve tempo, key, time_signature, ticks_per_quarter, duration_ticks, bar_count, and sections.",
        "Do not change notes outside the selected bars.",
    ]
    if not request.edit.allow_harmony_changes:
        rules.append("Keep harmony unchanged; set harmony_patch to null.")
    else:
        rules.append("harmony_patch may include only bars inside the selected range.")
    if not request.edit.allow_added_tracks:
        rules.append("Do not add tracks; added_tracks must be [].")
    else:
        rules.append("added_tracks are allowed only for explicit counter-melody requests and must use unique ids/channels.")

    supported = [
        "regenerate melody in selection",
        "make melody more active",
        "simplify accompaniment",
        "change bass line",
        "add counter-melody",
        "increase tension in the selected section",
    ]

    prompt = (
        "You are editing a canonical Composition V1 document using a replace_region patch.\n"
        f"Instruction: {request.edit.instruction.strip()}\n"
        f"Selection: bars {selection.start_bar}-{selection.end_bar} (inclusive), "
        f"ticks [{summary.bounds.start_tick}, {summary.bounds.end_tick}).\n"
        f"Target tracks: {target_ids}\n"
        f"Composition metadata: tempo={composition.tempo}, key={composition.key}, "
        f"time_signature={composition.time_signature}, ticks_per_quarter={composition.ticks_per_quarter}, "
        f"bar_count={composition.bar_count}, duration_ticks={composition.duration_ticks}.\n"
        f"Supported edit scenarios: {', '.join(supported)}.\n"
        f"Rules:\n- " + "\n- ".join(rules) + "\n"
        f"Patch contract example shape:\n{json.dumps(contract, ensure_ascii=True)}\n"
        f"Selected track region events:\n{json.dumps(tracks_payload, ensure_ascii=True)}\n"
        f"Selected harmony metadata:\n{json.dumps(harmony_in_region, ensure_ascii=True)}\n"
    )
    if diagnostics:
        prompt += (
            "Previous patch failed validation. Fix these diagnostics without changing out-of-scope notes:\n"
            f"{json.dumps(diagnostics, ensure_ascii=True)}\n"
        )
    return prompt


async def _invoke_edit_chat(state: _EditState, prompt: str) -> str:
    request = state["request"]
    provider = state["provider"]
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise LLMGenerationError("LangChain OpenAI dependencies are not installed") from exc

    timeout_seconds = request.options.timeout_seconds or load_llm_settings().request_timeout_seconds
    temperature = request.options.temperature
    if temperature is None:
        temperature = load_llm_settings().temperature

    model_name = _selected_edit_model(request, provider)
    client = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=model_name,
        temperature=temperature,
        timeout=timeout_seconds,
    )
    logger.debug(
        "Calling LLM provider for region edit",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "stage": state.get("current_stage"),
            "prompt_length": len(prompt),
        },
    )
    try:
        response = await client.ainvoke(prompt)
    except Exception as exc:
        logger.error(
            "LLM provider call failed during region edit",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "stage": state.get("current_stage"),
                "error_type": type(exc).__name__,
            },
        )
        raise LLMGenerationError(f"LLM provider call failed: {type(exc).__name__}") from exc

    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = "".join(str(part) for part in content)
    return str(content)
