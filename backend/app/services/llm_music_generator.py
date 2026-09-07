import json
import logging
import re
from typing import Any, TypedDict

from pydantic import ValidationError

from ..llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from ..schemas import (
    COMPOSITION_SCHEMA_VERSION,
    Composition,
    CompositionSection,
    CompositionTrack,
    LLMMusicHarmonyItem,
    LLMMusicGenerationRequest,
    NoteEvent,
)
from .composition_normalizer import (
    CompositionNormalizationError,
    INSTRUMENT_PROGRAMS,
    normalize_composition_json,
)
from .composition_planner import (
    ComposerFormPlan,
    ComposerHarmonyPlan,
    ComposerMotifContext,
    ComposerTrackDraft,
    OversizedLLMGenerationRequestError,
    ValidationDiagnostic,
    enforce_llm_generation_bounds,
    summarize_diagnostics,
    summarize_form_plan,
    summarize_track_draft,
)
from .composition_timing import bar_duration_ticks, derive_section_boundaries
from .composition_validator import validate_composition_integrity


logger = logging.getLogger(__name__)

COMPOSER_STAGES = (
    "plan_form",
    "plan_harmony",
    "compose_melody",
    "compose_bass",
    "compose_accompaniment",
    "assemble_composition",
    "validate_composition",
    "repair_composition",
)

DEFAULT_TICKS_PER_QUARTER = 480


class LLMGenerationError(RuntimeError):
    pass


class NoLLMProviderConfiguredError(LLMGenerationError):
    pass


class UnsupportedLLMProviderError(LLMGenerationError):
    pass


class InvalidLLMOutputError(LLMGenerationError):
    pass


__all__ = [
    "InvalidLLMOutputError",
    "LLMGenerationError",
    "NoLLMProviderConfiguredError",
    "OversizedLLMGenerationRequestError",
    "UnsupportedLLMProviderError",
    "generate_music_json",
]


class _GenerationState(TypedDict, total=False):
    request: LLMMusicGenerationRequest
    provider: LLMProviderSettings
    raw_output: str
    parsed_json: dict[str, Any]
    music: Composition
    retry_count: int
    warnings: list[str]
    form_plan: ComposerFormPlan
    harmony_plan: ComposerHarmonyPlan
    motif_context: ComposerMotifContext
    melody_draft: ComposerTrackDraft
    bass_draft: ComposerTrackDraft
    accompaniment_drafts: list[ComposerTrackDraft]
    validation_diagnostics: list[ValidationDiagnostic]
    validation_ok: bool
    current_stage: str
    failed_stage: str
    repair_target: str
    stage_retry_count: int
    stage_raw_outputs: dict[str, str]


async def generate_music_json(
    request: LLMMusicGenerationRequest,
    settings: LLMSettings | None = None,
) -> tuple[Composition, list[str], LLMProviderSettings]:
    active_settings = settings or load_llm_settings()
    enforce_llm_generation_bounds(request)
    provider = _select_provider(request, active_settings)

    from .fake_llm import FakeLLMError, generate_fake_music_json, is_fake_provider

    if is_fake_provider(provider):
        logger.info(
            "Routing music generation to fake LLM provider",
            extra={
                "provider": provider.provider,
                "model": _selected_model(request, provider),
                "duration_bars": request.prompt.duration_bars,
            },
        )
        try:
            return await generate_fake_music_json(request, provider)
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    logger.info(
        "LLM music generation started",
        extra={
            "provider": provider.provider,
            "model": _selected_model(request, provider),
            "composer_stages": list(COMPOSER_STAGES),
            "duration_bars": request.prompt.duration_bars,
            "instrument_count": len(request.prompt.instruments),
        },
    )
    logger.debug("LLM prompt parameters", extra={"prompt": _sanitized_prompt(request)})

    retry_limit = request.options.max_retries
    state: _GenerationState = {
        "request": request,
        "provider": provider,
        "retry_count": 0,
        "stage_retry_count": 0,
        "warnings": [],
        "validation_diagnostics": [],
        "accompaniment_drafts": [],
        "stage_raw_outputs": {},
        "current_stage": "plan_form",
        "validation_ok": False,
    }
    logger.debug("Initialized staged composer state", extra=_stage_state_summary(state))

    while True:
        try:
            graph = _build_generation_graph()
            result = await graph.ainvoke(state)
        except InvalidLLMOutputError as exc:
            # Stage parse failures use a separate budget from integrity repair retries.
            parse_retries = int(state.get("stage_retry_count", 0))
            if parse_retries >= retry_limit:
                logger.error(
                    "LLM staged generation failed after retries",
                    extra={
                        "error_type": type(exc).__name__,
                        "retry_count": state.get("retry_count", 0),
                        "stage_retry_count": parse_retries,
                        "stage": state.get("current_stage"),
                        "detail": str(exc)[:300],
                    },
                )
                raise
            state["stage_retry_count"] = parse_retries + 1
            state.setdefault("warnings", []).append(
                f"Stage '{state.get('current_stage')}' returned invalid JSON; retrying staged generation."
            )
            logger.warning(
                "Retrying staged generation after stage parse failure",
                extra={
                    "stage_retry_count": state["stage_retry_count"],
                    "retry_count": state.get("retry_count", 0),
                    "stage": state.get("current_stage"),
                    "reason": str(exc)[:200],
                },
            )
            logger.info(
                "[FIX] Preserved integrity repair budget after stage parse retry",
                extra={
                    "stage_retry_count": state["stage_retry_count"],
                    "retry_count": state.get("retry_count", 0),
                    "retry_limit": retry_limit,
                },
            )
            continue
        except ValidationError as exc:
            # Defense-in-depth: schema failures must not be labeled as provider/API errors.
            logger.error(
                "[FIX] Unexpected ValidationError remapped to InvalidLLMOutputError",
                extra={
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:200],
                    "stage": state.get("current_stage"),
                    "retry_count": state.get("retry_count", 0),
                },
            )
            raise InvalidLLMOutputError(
                f"LLM returned invalid composition schema: {str(exc)[:200]}"
            ) from exc
        except LLMGenerationError:
            raise
        except Exception as exc:
            logger.error(
                "LLM provider/API failure",
                extra={
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:200],
                    "stage": state.get("current_stage"),
                    "retry_count": state.get("retry_count", 0),
                },
            )
            raise LLMGenerationError(f"LLM provider request failed: {type(exc).__name__}") from exc

        music = result.get("music")
        if music is None or not result.get("validation_ok"):
            diagnostics = result.get("validation_diagnostics") or []
            codes = [item.code for item in diagnostics if getattr(item, "severity", "error") == "error"]
            messages = [item.message for item in diagnostics if getattr(item, "severity", "error") == "error"]
            detail = "; ".join(messages[:5]) if messages else (", ".join(codes) or "unknown validation failure")
            raise InvalidLLMOutputError(
                "LLM returned invalid or non-playable composition after staged generation/repair: "
                f"{detail}"
            )

        warnings = list(result.get("warnings") or [])
        for item in result.get("validation_diagnostics") or []:
            if item.severity == "warning":
                warnings.append(f"{item.code}: {item.message}")

        logger.info(
            "LLM music generation completed",
            extra={
                "provider": provider.provider,
                "model": _selected_model(request, provider),
                "schema_version": music.schema_version,
                "track_count": len(music.tracks),
                "event_count": sum(len(track.events) for track in music.tracks),
                "validation_retry_count": result.get("retry_count", 0),
            },
        )
        return music, warnings, provider


def _select_provider(request: LLMMusicGenerationRequest, settings: LLMSettings) -> LLMProviderSettings:
    if not settings.providers:
        logger.warning("LLM generation requested without configured providers")
        raise NoLLMProviderConfiguredError("No LLM providers are configured")

    requested_provider = request.selection.provider or settings.default_provider
    requested_model = request.selection.model
    for provider in settings.providers:
        if provider.provider == requested_provider:
            if requested_model and requested_model != provider.model:
                return LLMProviderSettings(
                    provider=provider.provider,
                    model=requested_model,
                    api_key=provider.api_key,
                    base_url=provider.base_url,
                    is_default=provider.is_default,
                )
            return provider

    logger.warning("Unsupported LLM provider requested", extra={"provider": requested_provider})
    raise UnsupportedLLMProviderError(f"Unsupported or unavailable LLM provider: {requested_provider}")


def _build_generation_graph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise LLMGenerationError("LangGraph dependencies are not installed") from exc

    logger.info(
        "Building staged LLM composition generation graph",
        extra={"stages": list(COMPOSER_STAGES)},
    )
    workflow = StateGraph(_GenerationState)
    workflow.add_node("plan_form", _plan_form)
    workflow.add_node("plan_harmony", _plan_harmony)
    workflow.add_node("compose_melody", _compose_melody)
    workflow.add_node("compose_bass", _compose_bass)
    workflow.add_node("compose_accompaniment", _compose_accompaniment)
    workflow.add_node("assemble_composition", _assemble_composition)
    workflow.add_node("validate_composition", _validate_composition)
    workflow.add_node("repair_composition", _repair_composition)

    workflow.set_entry_point("plan_form")
    workflow.add_edge("plan_form", "plan_harmony")
    workflow.add_edge("plan_harmony", "compose_melody")
    workflow.add_edge("compose_melody", "compose_bass")
    workflow.add_edge("compose_bass", "compose_accompaniment")
    workflow.add_edge("compose_accompaniment", "assemble_composition")
    workflow.add_edge("assemble_composition", "validate_composition")
    workflow.add_conditional_edges(
        "validate_composition",
        _route_after_validation,
        {
            "end": END,
            "repair": "repair_composition",
            "fail": END,
        },
    )
    workflow.add_conditional_edges(
        "repair_composition",
        _route_after_repair,
        {
            "plan_form": "plan_form",
            "plan_harmony": "plan_harmony",
            "compose_melody": "compose_melody",
            "compose_bass": "compose_bass",
            "compose_accompaniment": "compose_accompaniment",
            "assemble_composition": "assemble_composition",
            "validate_composition": "validate_composition",
            "fail": END,
        },
    )
    return workflow.compile()


def _route_after_validation(state: _GenerationState) -> str:
    if state.get("validation_ok"):
        return "end"
    retry_limit = state["request"].options.max_retries
    if state.get("retry_count", 0) >= retry_limit:
        logger.error(
            "Repair budget exhausted after validation failure",
            extra={
                "provider": state["provider"].provider,
                "model": _selected_model(state["request"], state["provider"]),
                "stage": state.get("failed_stage") or "validate_composition",
                "retry_count": state.get("retry_count", 0),
                "diagnostic_codes": [
                    item.code for item in (state.get("validation_diagnostics") or []) if item.severity == "error"
                ],
            },
        )
        return "fail"
    return "repair"


def _route_after_repair(state: _GenerationState) -> str:
    target = state.get("repair_target") or "assemble_composition"
    if target == "fail":
        return "fail"
    if target in {
        "plan_form",
        "plan_harmony",
        "compose_melody",
        "compose_bass",
        "compose_accompaniment",
        "assemble_composition",
        "validate_composition",
    }:
        return target
    return "assemble_composition"


async def _plan_form(state: _GenerationState) -> _GenerationState:
    return await _run_json_stage(
        state,
        stage="plan_form",
        prompt=_build_form_prompt(state),
        parser=_parse_form_plan,
        on_success=_after_form_plan,
    )


async def _plan_harmony(state: _GenerationState) -> _GenerationState:
    return await _run_json_stage(
        state,
        stage="plan_harmony",
        prompt=_build_harmony_prompt(state),
        parser=_parse_harmony_plan,
        on_success=_after_harmony_plan,
    )


async def _compose_melody(state: _GenerationState) -> _GenerationState:
    return await _run_json_stage(
        state,
        stage="compose_melody",
        prompt=_build_melody_prompt(state),
        parser=_parse_melody_stage,
        on_success=_after_melody_stage,
    )


async def _compose_bass(state: _GenerationState) -> _GenerationState:
    return await _run_json_stage(
        state,
        stage="compose_bass",
        prompt=_build_bass_prompt(state),
        parser=_parse_bass_stage,
        on_success=_after_bass_stage,
    )


async def _compose_accompaniment(state: _GenerationState) -> _GenerationState:
    return await _run_json_stage(
        state,
        stage="compose_accompaniment",
        prompt=_build_accompaniment_prompt(state),
        parser=_parse_accompaniment_stage,
        on_success=_after_accompaniment_stage,
    )


def _compose_stage_for_role(role: str | None) -> str:
    normalized = (role or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"melody", "lead"}:
        return "compose_melody"
    if normalized == "bass":
        return "compose_bass"
    if normalized in {"harmony", "pad", "countermelody", "rhythm", "accompaniment"}:
        return "compose_accompaniment"
    return "assemble_composition"


def _draft_overflows_duration(draft: ComposerTrackDraft, duration_ticks: int) -> bool:
    return any(event.start_tick + event.duration_ticks > duration_ticks for event in draft.events)


def _infer_assemble_failed_stage(
    exc: Exception,
    drafts: list[ComposerTrackDraft],
    *,
    draft: ComposerTrackDraft | None = None,
    duration_ticks: int | None = None,
) -> str:
    if draft is not None:
        return _compose_stage_for_role(draft.role)

    if isinstance(exc, ValidationError):
        for error in exc.errors():
            loc = error.get("loc") or ()
            if len(loc) >= 2 and loc[0] == "tracks" and isinstance(loc[1], int):
                index = loc[1]
                if 0 <= index < len(drafts):
                    return _compose_stage_for_role(drafts[index].role)

    detail = str(exc).lower()
    if duration_ticks is not None and (
        "fit within the composition duration" in detail or "composition duration" in detail
    ):
        for item in drafts:
            if _draft_overflows_duration(item, duration_ticks):
                return _compose_stage_for_role(item.role)
    if "melody" in detail or "lead" in detail:
        return "compose_melody"
    if "bass" in detail:
        return "compose_bass"
    if "harmony" in detail or "accompaniment" in detail or "pad" in detail:
        return "compose_accompaniment"
    return "assemble_composition"


def _assemble_schema_soft_fail(
    state: _GenerationState,
    *,
    stage: str,
    exc: Exception,
    failed_stage: str,
    draft: ComposerTrackDraft | None = None,
) -> _GenerationState:
    detail = str(exc)[:300]
    logger.error(
        "[FIX] Assemble composition schema validation failed",
        extra={
            "stage": stage,
            "failed_stage": failed_stage,
            "error_type": type(exc).__name__,
            "error_detail": detail,
            "track_role": draft.role if draft is not None else None,
            "track_id": draft.id if draft is not None else None,
        },
    )
    context: dict[str, Any] = {
        "error_type": type(exc).__name__,
        "failed_stage": failed_stage,
    }
    if draft is not None:
        context["track_role"] = draft.role
        context["track_id"] = draft.id
    diagnostic = ValidationDiagnostic(
        code="schema_invalid",
        message=f"Assemble failed schema validation: {detail[:400]}",
        context=context,
    )
    cleared = {key: value for key, value in state.items() if key != "music"}
    return {
        **cleared,
        "validation_ok": False,
        "validation_diagnostics": [diagnostic],
        "failed_stage": failed_stage,
        "current_stage": stage,
    }


def _assemble_composition(state: _GenerationState) -> _GenerationState:
    stage = "assemble_composition"
    logger.info(
        "Composer stage started",
        extra=_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
    )
    form = state.get("form_plan")
    harmony = state.get("harmony_plan")
    melody = state.get("melody_draft")
    bass = state.get("bass_draft")
    accompaniment = list(state.get("accompaniment_drafts") or [])
    if form is None or harmony is None or melody is None or bass is None:
        raise InvalidLLMOutputError("Cannot assemble composition; required stage outputs are missing")

    drafts = [melody, bass, *accompaniment]
    duration_ticks: int | None = None
    try:
        ticks_per_quarter = DEFAULT_TICKS_PER_QUARTER
        bar_ticks = bar_duration_ticks(form.time_signature, ticks_per_quarter)
        section_payloads = [
            {"type": section.type, "bar_count": section.bar_count} for section in form.sections
        ]
        derived_sections = derive_section_boundaries(section_payloads, form.time_signature, ticks_per_quarter)
        sections = [CompositionSection.model_validate(item) for item in derived_sections]
        duration_ticks = form.bar_count * bar_ticks

        tracks: list[CompositionTrack] = []
        used_track_ids: set[str] = set()
        for index, draft in enumerate(drafts, start=1):
            try:
                tracks.append(
                    _draft_to_track(
                        draft,
                        index,
                        used_ids=used_track_ids,
                        duration_ticks=duration_ticks,
                    )
                )
            except (ValidationError, ValueError) as exc:
                failed_stage = _infer_assemble_failed_stage(
                    exc,
                    drafts,
                    draft=draft,
                    duration_ticks=duration_ticks,
                )
                return _assemble_schema_soft_fail(
                    state,
                    stage=stage,
                    exc=exc,
                    failed_stage=failed_stage,
                    draft=draft,
                )

        harmony_items = [
            LLMMusicHarmonyItem(bar=event.bar, chord=event.chord) for event in harmony.events
        ]
        composition = Composition(
            schema_version=COMPOSITION_SCHEMA_VERSION,
            tempo=form.tempo,
            key=form.key,
            time_signature=form.time_signature,
            ticks_per_quarter=ticks_per_quarter,
            duration_ticks=duration_ticks,
            bar_count=form.bar_count,
            sections=sections,
            tracks=tracks,
            harmony=harmony_items,
        )
    except (ValidationError, ValueError) as exc:
        failed_stage = _infer_assemble_failed_stage(
            exc,
            drafts,
            duration_ticks=duration_ticks,
        )
        return _assemble_schema_soft_fail(
            state,
            stage=stage,
            exc=exc,
            failed_stage=failed_stage,
        )

    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "schema_version": composition.schema_version,
            "bar_count": composition.bar_count,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
            "duration_ticks": composition.duration_ticks,
        },
    )
    logger.debug(
        "Assembled composition track summary",
        extra={
            "track_ids": [track.id for track in composition.tracks],
            "roles": [track.role for track in composition.tracks],
            "events_by_track": {track.id: len(track.events) for track in composition.tracks},
        },
    )
    return {
        **state,
        "music": composition,
        "current_stage": stage,
        "failed_stage": "",
        "parsed_json": composition.model_dump(),
    }


def _validate_composition(state: _GenerationState) -> _GenerationState:
    stage = "validate_composition"
    music = state.get("music")
    request = state["request"]
    logger.info(
        "Composer stage started",
        extra=_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
    )
    if music is None:
        existing = list(state.get("validation_diagnostics") or [])
        failed_stage = state.get("failed_stage") or "assemble_composition"
        if existing:
            diagnostics = existing
            logger.info(
                "[FIX] Preserving assemble schema diagnostics through validate",
                extra={
                    "failed_stage": failed_stage,
                    "diagnostic_codes": [item.code for item in diagnostics],
                },
            )
        else:
            diagnostics = [
                ValidationDiagnostic(
                    code="schema_invalid",
                    message="No assembled composition available for validation",
                )
            ]
        return {
            **state,
            "validation_ok": False,
            "validation_diagnostics": diagnostics,
            "failed_stage": failed_stage,
            "current_stage": stage,
        }

    result = validate_composition_integrity(
        music,
        requested_instruments=request.prompt.instruments,
        complexity=request.prompt.complexity,
    )
    failed_stage = _infer_failed_stage(result.errors) if not result.ok else ""
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "validation_ok": result.ok,
            "error_codes": result.error_codes(),
            "warning_count": len(result.warnings),
        },
    )
    return {
        **state,
        "validation_ok": result.ok,
        "validation_diagnostics": result.diagnostics,
        "failed_stage": failed_stage,
        "current_stage": stage,
    }


async def _repair_composition(state: _GenerationState) -> _GenerationState:
    stage = "repair_composition"
    retry_count = int(state.get("retry_count", 0)) + 1
    diagnostics = [item for item in (state.get("validation_diagnostics") or []) if item.severity == "error"]
    codes = [item.code for item in diagnostics]
    repair_target = state.get("failed_stage") or "assemble_composition"
    if repair_target not in {
        "plan_form",
        "plan_harmony",
        "compose_melody",
        "compose_bass",
        "compose_accompaniment",
        "assemble_composition",
        "validate_composition",
    }:
        repair_target = "assemble_composition"

    logger.warning(
        "Composer repair attempt started",
        extra={
            **_stage_log_extra(state, stage, attempt=retry_count),
            "repair_target": repair_target,
            "diagnostic_codes": codes,
        },
    )

    # Store structured diagnostics into warnings for frontend visibility, then route back.
    warnings = list(state.get("warnings") or [])
    warnings.append(
        "Staged composition failed validation; retrying "
        f"{repair_target} with diagnostics: {', '.join(codes) or 'unspecified'}."
    )
    updated: _GenerationState = {
        **state,
        "retry_count": retry_count,
        "repair_target": repair_target,
        "current_stage": stage,
        "warnings": warnings,
        "validation_ok": False,
    }
    logger.info(
        "Composer repair routed to stage",
        extra={
            **_stage_log_extra(updated, stage, attempt=retry_count),
            "repair_target": repair_target,
            "diagnostic_codes": codes,
        },
    )
    return updated


async def _run_json_stage(
    state: _GenerationState,
    *,
    stage: str,
    prompt: str,
    parser,
    on_success,
) -> _GenerationState:
    request = state["request"]
    provider = state["provider"]
    attempt = state.get("retry_count", 0)
    logger.info(
        "Composer stage started",
        extra={
            **_stage_log_extra(state, stage, attempt=attempt),
            "prompt_length": len(prompt),
        },
    )
    logger.debug(
        "Composer stage prompt parameters",
        extra={
            "stage": stage,
            "prompt_preview": prompt[:240],
            "sanitized_request": _sanitized_prompt(request),
        },
    )

    if state.get("repair_target") == stage and state.get("validation_diagnostics"):
        prompt = _append_repair_diagnostics(prompt, state.get("validation_diagnostics") or [])

    raw_output = await _invoke_chat(state, prompt)
    stage_raw_outputs = dict(state.get("stage_raw_outputs") or {})
    stage_raw_outputs[stage] = raw_output

    try:
        parsed = _extract_json(raw_output)
        parsed_model = parser(parsed, state)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        logger.warning(
            "Composer stage validation failed",
            extra={
                **_stage_log_extra(state, stage, attempt=attempt),
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:300],
            },
        )
        raise InvalidLLMOutputError(
            f"LLM stage '{stage}' returned invalid JSON: {str(exc)[:200]}"
        ) from exc

    updated = on_success(state, parsed_model, parsed)
    updated = {
        **updated,
        "raw_output": raw_output,
        "stage_raw_outputs": stage_raw_outputs,
        "current_stage": stage,
        "failed_stage": "",
        "repair_target": "",
    }
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(updated, stage, attempt=attempt),
            "response_length": len(raw_output),
            **_stage_completion_metrics(stage, updated),
        },
    )
    logger.debug("Composer stage state summary", extra=_stage_state_summary(updated))
    return updated


async def _invoke_chat(state: _GenerationState, prompt: str) -> str:
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

    client = ChatOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=_selected_model(request, provider),
        temperature=temperature,
        timeout=timeout_seconds,
    )
    logger.debug(
        "Calling LLM provider",
        extra={
            "provider": provider.provider,
            "model": _selected_model(request, provider),
            "stage": state.get("current_stage"),
            "prompt_length": len(prompt),
        },
    )
    try:
        response = await client.ainvoke(prompt)
    except Exception as exc:
        logger.error(
            "LLM provider call failed",
            extra={
                "provider": provider.provider,
                "model": _selected_model(request, provider),
                "stage": state.get("current_stage"),
                "retry_count": state.get("retry_count", 0),
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:200],
            },
        )
        raise LLMGenerationError(
            f"LLM provider request failed during stage '{state.get('current_stage')}': {type(exc).__name__}"
        ) from exc

    raw_output = getattr(response, "content", str(response))
    if isinstance(raw_output, list):
        raw_output = "".join(str(part) for part in raw_output)
    return str(raw_output)


def _parse_form_plan(parsed: dict[str, Any], state: _GenerationState) -> ComposerFormPlan:
    return ComposerFormPlan.model_validate(parsed)


def _after_form_plan(state: _GenerationState, form: ComposerFormPlan, parsed: dict[str, Any]) -> _GenerationState:
    request = state["request"]
    warnings = list(state.get("warnings") or [])
    if request.prompt.key and form.key != request.prompt.key:
        warnings.append(f"Form stage chose key {form.key} instead of requested {request.prompt.key}.")
    if form.time_signature != request.prompt.time_signature:
        warnings.append(
            f"Form stage chose meter {form.time_signature} instead of requested {request.prompt.time_signature}."
        )
    if abs(form.bar_count - request.prompt.duration_bars) > 0:
        logger.warning(
            "Form stage bar count mismatch corrected downstream if needed",
            extra={"requested_bars": request.prompt.duration_bars, "form_bars": form.bar_count},
        )
    logger.info(
        "Resolved form plan",
        extra={
            "bar_count": form.bar_count,
            "section_count": len(form.sections),
            "tempo": form.tempo,
            "key": form.key,
            "time_signature": form.time_signature,
        },
    )
    return {**state, "form_plan": form, "parsed_json": parsed, "warnings": warnings}


def _parse_harmony_plan(parsed: dict[str, Any], state: _GenerationState) -> ComposerHarmonyPlan:
    if "events" not in parsed and isinstance(parsed.get("harmony"), list):
        parsed = {"events": parsed["harmony"]}
    return ComposerHarmonyPlan.model_validate(parsed)


def _after_harmony_plan(
    state: _GenerationState, harmony: ComposerHarmonyPlan, parsed: dict[str, Any]
) -> _GenerationState:
    form = state.get("form_plan")
    section_coverage = sorted({event.section_type for event in harmony.events if event.section_type})
    logger.info(
        "Resolved harmony plan",
        extra={
            "harmony_item_count": len(harmony.events),
            "section_coverage": section_coverage,
            "form_sections": [section.type for section in form.sections] if form else [],
        },
    )
    logger.debug(
        "Harmony preview",
        extra={
            "first_chords": [
                {"bar": event.bar, "chord": event.chord} for event in harmony.events[:5]
            ]
        },
    )
    return {**state, "harmony_plan": harmony, "parsed_json": parsed}


def _parse_melody_stage(parsed: dict[str, Any], state: _GenerationState) -> dict[str, Any]:
    track_data = parsed.get("track") or parsed
    motif_data = parsed.get("motif_context") or {}
    track = ComposerTrackDraft.model_validate(track_data)
    if track.role not in {"melody", "lead"}:
        track = track.model_copy(update={"role": "melody"})
    motif = ComposerMotifContext.model_validate(motif_data)
    return {"track": track, "motif_context": motif}


def _after_melody_stage(state: _GenerationState, payload: dict[str, Any], parsed: dict[str, Any]) -> _GenerationState:
    track: ComposerTrackDraft = payload["track"]
    motif: ComposerMotifContext = payload["motif_context"]
    if not track.events:
        logger.warning("Melody stage produced empty events", extra={"track_id": track.id})
    logger.info(
        "Resolved melody draft",
        extra={
            **summarize_track_draft(track),
            "motif_ids": motif.motif_ids,
            "covered_bars_estimate": _estimate_covered_bars(track, state.get("form_plan")),
        },
    )
    return {
        **state,
        "melody_draft": track,
        "motif_context": motif,
        "parsed_json": parsed,
    }


def _parse_bass_stage(parsed: dict[str, Any], state: _GenerationState) -> ComposerTrackDraft:
    track_data = parsed.get("track") or parsed
    track = ComposerTrackDraft.model_validate(track_data)
    if track.role != "bass":
        track = track.model_copy(update={"role": "bass"})
    return track


def _after_bass_stage(
    state: _GenerationState, track: ComposerTrackDraft, parsed: dict[str, Any]
) -> _GenerationState:
    if not track.events:
        logger.warning("Bass stage produced empty events", extra={"track_id": track.id})
    logger.info(
        "Resolved bass draft",
        extra={
            **summarize_track_draft(track),
            "covered_bars_estimate": _estimate_covered_bars(track, state.get("form_plan")),
        },
    )
    return {**state, "bass_draft": track, "parsed_json": parsed}


def _parse_accompaniment_stage(parsed: dict[str, Any], state: _GenerationState) -> dict[str, Any]:
    tracks_raw = parsed.get("tracks")
    if tracks_raw is None and parsed.get("track") is not None:
        tracks_raw = [parsed["track"]]
    if not isinstance(tracks_raw, list) or not tracks_raw:
        raise ValueError("Accompaniment stage must return at least one track")
    tracks = [ComposerTrackDraft.model_validate(item) for item in tracks_raw]
    skipped = parsed.get("skipped") or []
    return {"tracks": tracks, "skipped": skipped}


def _after_accompaniment_stage(
    state: _GenerationState, payload: dict[str, Any], parsed: dict[str, Any]
) -> _GenerationState:
    tracks: list[ComposerTrackDraft] = payload["tracks"]
    skipped = payload.get("skipped") or []
    if not any(track.role in {"harmony", "pad", "rhythm", "countermelody"} for track in tracks):
        logger.warning(
            "Accompaniment stage missing required harmonic role",
            extra={"roles": [track.role for track in tracks]},
        )
    logger.info(
        "Resolved accompaniment drafts",
        extra={
            "roles": [track.role for track in tracks],
            "instruments": [track.instrument for track in tracks],
            "event_counts": {track.id: len(track.events) for track in tracks},
            "skipped": skipped[:10],
        },
    )
    warnings = list(state.get("warnings") or [])
    for item in skipped:
        if isinstance(item, dict):
            warnings.append(
                f"Skipped optional instrument {item.get('instrument')}: {item.get('reason', 'unspecified')}"
            )
    return {
        **state,
        "accompaniment_drafts": tracks,
        "parsed_json": parsed,
        "warnings": warnings,
    }


def _allocate_unique_track_id(
    preferred: str | None,
    instrument: str,
    index: int,
    used_ids: set[str],
) -> str:
    candidates: list[str] = []
    if preferred:
        candidates.append(preferred)
    fallback = _track_id(instrument, index)
    if fallback not in candidates:
        candidates.append(fallback)
    for candidate in candidates:
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
    base = preferred or fallback
    suffix = 2
    while True:
        candidate = f"{base}-{suffix}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate
        suffix += 1


def _fit_events_to_duration(
    draft_events: list,
    duration_ticks: int,
    *,
    track_role: str,
    preferred_track_id: str | None,
) -> list[NoteEvent]:
    fitted: list[NoteEvent] = []
    truncated = 0
    dropped = 0
    for event in draft_events:
        if event.start_tick >= duration_ticks:
            dropped += 1
            continue
        duration = event.duration_ticks
        if event.start_tick + duration > duration_ticks:
            duration = duration_ticks - event.start_tick
            if duration <= 0:
                dropped += 1
                continue
            truncated += 1
        fitted.append(
            NoteEvent(
                pitch=event.pitch,
                start_tick=event.start_tick,
                duration_ticks=duration,
                velocity=event.velocity,
                staff=event.staff,
                id=event.id,
            )
        )
    if truncated or dropped:
        logger.info(
            "[FIX] Clamped assemble events to composition duration",
            extra={
                "track_role": track_role,
                "track_id": preferred_track_id,
                "duration_ticks": duration_ticks,
                "truncated_count": truncated,
                "dropped_count": dropped,
                "kept_count": len(fitted),
            },
        )
    return fitted


def _draft_to_track(
    draft: ComposerTrackDraft,
    index: int,
    *,
    used_ids: set[str] | None = None,
    duration_ticks: int | None = None,
) -> CompositionTrack:
    instrument = draft.instrument
    midi_program = draft.midi_program
    if midi_program is None:
        midi_program = _midi_program_for_instrument(instrument)
    channel = draft.channel
    if channel is None:
        channel = 10 if draft.is_drum else _melodic_channel(index)
    preferred = draft.id or None
    if duration_ticks is None:
        events = [
            NoteEvent(
                pitch=event.pitch,
                start_tick=event.start_tick,
                duration_ticks=event.duration_ticks,
                velocity=event.velocity,
                staff=event.staff,
                id=event.id,
            )
            for event in draft.events
        ]
    else:
        events = _fit_events_to_duration(
            draft.events,
            duration_ticks,
            track_role=draft.role,
            preferred_track_id=preferred,
        )
    if used_ids is None:
        track_id = preferred or _track_id(instrument, index)
    else:
        track_id = _allocate_unique_track_id(preferred, instrument, index, used_ids)
        if preferred and track_id != preferred:
            logger.info(
                "[FIX] Remapped duplicate assemble track id",
                extra={
                    "preferred_track_id": preferred,
                    "resolved_track_id": track_id,
                    "track_role": draft.role,
                    "instrument": instrument,
                    "assemble_index": index,
                },
            )
    return CompositionTrack(
        id=track_id,
        name=draft.name or f"{instrument.title()} {draft.role}",
        instrument=instrument,
        role=draft.role,
        midi_program=midi_program,
        channel=channel,
        is_drum=draft.is_drum,
        volume=draft.volume,
        pan=draft.pan,
        staff=draft.staff,
        events=events,
    )


def _build_form_prompt(state: _GenerationState) -> str:
    request = state["request"]
    prompt = request.prompt
    sections = [section.model_dump() for section in prompt.sections]
    return f"""
You are planning musical form for a composition.v1 generator.
Return JSON only with this shape:
{{
  "tempo": 80,
  "key": "A minor",
  "time_signature": "4/4",
  "bar_count": 16,
  "sections": [
    {{"type": "intro", "start_bar": 1, "bar_count": 4, "intensity": "quiet", "dynamic_notes": "sparse"}},
    {{"type": "verse", "start_bar": 5, "bar_count": 8, "intensity": "building", "dynamic_notes": "motif enters"}},
    {{"type": "outro", "start_bar": 13, "bar_count": 4, "intensity": "resolved", "dynamic_notes": "cadence"}}
  ],
  "instrumentation": ["piano", "bass", "strings"]
}}

Rules:
- tempo between {prompt.tempo_min} and {prompt.tempo_max}
- key format like "C minor" or "F# major"; prefer requested key when provided
- time_signature must be {prompt.time_signature}
- bar_count should equal requested duration_bars={prompt.duration_bars}
- sections must be contiguous from start_bar=1 and sum to bar_count
- section types: intro, verse, pre_chorus, chorus, bridge, solo, breakdown, outro
- instrumentation should reflect requested instruments and needed roles (melody, accompaniment/harmony, bass, optional strings/pad)
- respect genre={prompt.genre}, mood={prompt.mood}, complexity={prompt.complexity}
- instructions: {prompt.instructions or "none"}
- requested sections hint: {json.dumps(sections) if sections else "choose coherent structure"}
- requested instruments: {", ".join(prompt.instruments)}
""".strip()


def _build_harmony_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    request = state["request"]
    return f"""
You are writing harmonic progression metadata for composition.v1.
Form plan:
{json.dumps(form.model_dump(), ensure_ascii=True)}

Return JSON only:
{{
  "events": [
    {{"bar": 1, "chord": "Am", "section_type": "intro", "cadence": null, "function": "tonic"}},
    {{"bar": 4, "chord": "E7", "section_type": "intro", "cadence": "half", "function": "dominant"}}
  ]
}}

Rules:
- harmony is metadata only; do not invent note events
- cover each section start and important cadences
- bars must be within 1..{form.bar_count}
- prefer diatonic chords in key {form.key}
- genre={request.prompt.genre}, mood={request.prompt.mood}, complexity={request.prompt.complexity}
""".strip()


def _build_melody_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    request = state["request"]
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    return f"""
You are composing the primary melody track in canonical tick timing.
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony events:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}

Return JSON only:
{{
  "track": {{
    "id": "melody-1",
    "name": "Melody",
    "instrument": "piano",
    "role": "melody",
    "staff": "treble",
    "events": [
      {{"pitch": "A4", "start_tick": 0, "duration_ticks": {ticks}, "velocity": 78, "staff": "treble"}}
    ]
  }},
  "motif_context": {{
    "motif_ids": ["m1"],
    "interval_cells": ["0,+2,-1"],
    "rhythm_cells": ["1,1,2"],
    "section_notes": ["intro states motif", "verse develops motif"],
    "handoff": "repeat motif with sequence into outro"
  }}
}}

Rules:
- ticks_per_quarter={ticks}; one bar = {bar_ticks} ticks for {form.time_signature}
- events must stay within 0..{form.bar_count * bar_ticks - 1} start and not exceed composition end
- include enough melody notes for immediate playback across sections (at least ~1 event per bar on average)
- carry and develop motifs across sections using motif_context
- melody pitch range commonly C4-C6 unless instrument requires otherwise
- choose instrument from requested instruments when practical: {", ".join(request.prompt.instruments)}
- complexity={request.prompt.complexity}
""".strip()


def _build_bass_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    melody = state.get("melody_draft")
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    melody_summary = summarize_track_draft(melody)
    return f"""
You are composing the bass track in canonical tick timing.
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}
Melody summary:
{json.dumps(melody_summary, ensure_ascii=True)}

Return JSON only:
{{
  "track": {{
    "id": "bass-1",
    "name": "Bass",
    "instrument": "bass",
    "role": "bass",
    "events": [
      {{"pitch": "A2", "start_tick": 0, "duration_ticks": {bar_ticks}, "velocity": 84}}
    ]
  }}
}}

Rules:
- follow harmonic roots and cadences
- stay in bass range C1-C4
- ticks_per_quarter={ticks}; bar length={bar_ticks}
- composition duration ticks={form.bar_count * bar_ticks}
- include enough notes for the full form (roughly one event per bar minimum; avoid long empty stretches)
- reflect section intensity from the form plan
""".strip()


def _build_accompaniment_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    motif = state.get("motif_context") or ComposerMotifContext()
    request = state["request"]
    melody = state.get("melody_draft")
    bass = state.get("bass_draft")
    reserved_ids = [track.id for track in (melody, bass) if track is not None and track.id]
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    return f"""
You are composing accompaniment / harmonic support tracks in canonical tick timing.
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}
Motif context:
{json.dumps(motif.model_dump(), ensure_ascii=True)}
Requested instruments: {", ".join(request.prompt.instruments)}
Reserved track IDs already used by melody/bass (do not reuse): {", ".join(reserved_ids) if reserved_ids else "none"}

Return JSON only:
{{
  "tracks": [
    {{
      "id": "harmony-1",
      "name": "Piano Accompaniment",
      "instrument": "piano",
      "role": "harmony",
      "staff": "grand",
      "events": [
        {{"pitch": "A3", "start_tick": 0, "duration_ticks": {ticks}, "velocity": 70, "staff": "bass"}},
        {{"pitch": "C5", "start_tick": 0, "duration_ticks": {ticks}, "velocity": 68, "staff": "treble"}}
      ]
    }},
    {{
      "id": "strings-1",
      "name": "Strings",
      "instrument": "strings",
      "role": "pad",
      "events": [
        {{"pitch": "E4", "start_tick": 0, "duration_ticks": {bar_ticks}, "velocity": 60}}
      ]
    }}
  ],
  "skipped": []
}}

Rules:
- always include at least one harmony/accompaniment role track with playable events
- for piano accompaniment prefer one piano track with staff "grand" and per-note staff treble/bass
- add practical optional tracks such as strings/pad when requested
- if an optional instrument is skipped, list it under skipped with a short reason
- track ids must be unique within this response and must not reuse reserved melody/bass ids
- strings/pad pitches should stay within C2-C7 (cello lows OK; avoid sub-bass mud)
- ticks_per_quarter={ticks}; bar length={bar_ticks}; total bars={form.bar_count}
- do not rely on harmony metadata as audible content
""".strip()


def _append_repair_diagnostics(prompt: str, diagnostics: list[ValidationDiagnostic]) -> str:
    payload = [item.model_dump() for item in diagnostics[:20]]
    return (
        prompt
        + "\n\nPrevious output failed validation. Return corrected JSON only for this stage.\n"
        + "Diagnostics:\n"
        + json.dumps(payload, ensure_ascii=True)
    )


def _infer_failed_stage(errors: list[ValidationDiagnostic]) -> str:
    codes = {item.code for item in errors}
    messages = " ".join(item.message.lower() for item in errors)
    contexts = " ".join(
        str(item.context).lower() for item in errors if getattr(item, "context", None)
    )
    role_haystack = f"{messages} {contexts}"
    if "missing_required_track" in codes or "empty_required_track" in codes:
        if "melody" in role_haystack or "lead" in role_haystack:
            return "compose_melody"
        if "bass" in role_haystack:
            return "compose_bass"
        if "harmony" in role_haystack or "accompaniment" in role_haystack or "pad" in role_haystack:
            return "compose_accompaniment"
        return "compose_accompaniment"
    if "sparse_harmony" in codes:
        return "plan_harmony"
    if "bar_overflow" in codes or "event_out_of_range" in codes:
        if "melody" in role_haystack or "lead" in role_haystack:
            return "compose_melody"
        if "bass" in role_haystack:
            return "compose_bass"
        if "harmony" in role_haystack or "accompaniment" in role_haystack or "pad" in role_haystack:
            return "compose_accompaniment"
        # Bounds failures are compose-stage content issues, not assemble merges.
        return "compose_melody"
    if "schema_invalid" in codes:
        for item in errors:
            failed = (item.context or {}).get("failed_stage") if item.context else None
            if isinstance(failed, str) and failed:
                return failed
        return "assemble_composition"
    return "assemble_composition"


def _estimate_covered_bars(track: ComposerTrackDraft | None, form: ComposerFormPlan | None) -> int:
    if track is None or form is None or not track.events:
        return 0
    bar_ticks = bar_duration_ticks(form.time_signature, DEFAULT_TICKS_PER_QUARTER)
    bars = {(event.start_tick // bar_ticks) + 1 for event in track.events}
    return len(bars)


def _stage_completion_metrics(stage: str, state: _GenerationState) -> dict[str, Any]:
    if stage == "plan_form":
        return summarize_form_plan(state.get("form_plan"))
    if stage == "plan_harmony":
        harmony = state.get("harmony_plan")
        return {"harmony_event_count": len(harmony.events) if harmony else 0}
    if stage == "compose_melody":
        return summarize_track_draft(state.get("melody_draft"))
    if stage == "compose_bass":
        return summarize_track_draft(state.get("bass_draft"))
    if stage == "compose_accompaniment":
        drafts = state.get("accompaniment_drafts") or []
        return {
            "accompaniment_count": len(drafts),
            "roles": [draft.role for draft in drafts],
            "event_counts": {draft.id: len(draft.events) for draft in drafts},
        }
    return {}


def _stage_log_extra(state: _GenerationState, stage: str, *, attempt: int) -> dict[str, Any]:
    provider = state.get("provider")
    request = state.get("request")
    return {
        "stage": stage,
        "attempt": attempt,
        "provider": provider.provider if provider else None,
        "model": _selected_model(request, provider) if request and provider else None,
    }


def _stage_state_summary(state: _GenerationState) -> dict[str, Any]:
    accompaniment = state.get("accompaniment_drafts") or []
    return {
        "current_stage": state.get("current_stage"),
        "failed_stage": state.get("failed_stage"),
        "repair_target": state.get("repair_target"),
        "retry_count": state.get("retry_count", 0),
        "stage_retry_count": state.get("stage_retry_count", 0),
        "form": summarize_form_plan(state.get("form_plan")),
        "harmony_event_count": len((state.get("harmony_plan").events if state.get("harmony_plan") else []) or []),
        "motif_ids": list((state.get("motif_context").motif_ids if state.get("motif_context") else []) or []),
        "melody": summarize_track_draft(state.get("melody_draft")),
        "bass": summarize_track_draft(state.get("bass_draft")),
        "accompaniment_count": len(accompaniment),
        "accompaniment_roles": [draft.role for draft in accompaniment],
        "diagnostics": summarize_diagnostics(state.get("validation_diagnostics")),
        "validation_ok": state.get("validation_ok"),
        "has_music": state.get("music") is not None,
    }


def _parse_and_validate(state: _GenerationState) -> _GenerationState:
    """Legacy single-shot parser retained for compatibility with older tests."""
    raw_output = state.get("raw_output", "")
    try:
        parsed_json = _extract_json(raw_output)
        music = normalize_composition_json(parsed_json)
    except (json.JSONDecodeError, ValidationError, CompositionNormalizationError, ValueError) as exc:
        logger.debug(
            "Invalid LLM music JSON",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:300]},
        )
        raise InvalidLLMOutputError(f"LLM returned invalid music JSON: {str(exc)[:200]}") from exc

    warnings = list(state.get("warnings", []))
    normalization_path = "canonical" if parsed_json.get("schema_version") == music.schema_version else "legacy_migrated"
    if normalization_path == "legacy_migrated":
        warnings.append("LLM returned legacy music JSON; normalized to composition.v1.")
    return {**state, "parsed_json": parsed_json, "music": music, "warnings": warnings}


def _extract_json(raw_output: str) -> dict[str, Any]:
    content = raw_output.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in LLM output")

    parsed = json.loads(content[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("LLM output JSON must be an object")
    return parsed


def _selected_model(request: LLMMusicGenerationRequest, provider: LLMProviderSettings) -> str:
    return request.selection.model or provider.model


def _sanitized_prompt(request: LLMMusicGenerationRequest) -> dict[str, Any]:
    data = request.prompt.model_dump()
    if data.get("instructions"):
        data["instructions"] = str(data["instructions"])[:200]
    return data


def _track_id(instrument: str, index: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", instrument.lower()).strip("-") or "track"
    return f"{slug}-{index}"


def _midi_program_for_instrument(instrument: str) -> int:
    normalized = instrument.strip().lower()
    for token, program in INSTRUMENT_PROGRAMS.items():
        if token in normalized:
            return program
    return 0


def _melodic_channel(track_index: int) -> int:
    channel = ((track_index - 1) % 15) + 1
    return channel + 1 if channel >= 10 else channel
