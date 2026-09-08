import json
import logging
import re
from typing import Any, TypedDict

from pydantic import ValidationError

from ..llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from ..schemas import (
    Composition,
    CompositionV2,
    CompositionV2HarmonyItem,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
    GenerationRepairAction,
    GenerationValidationReport,
    LLMMusicGenerationRequest,
    NoteEvent,
)
from ..composition_schemas import COMPOSITION_SCHEMA_VERSION_V2
from .composition_normalizer import (
    CompositionNormalizationError,
    INSTRUMENT_PROGRAMS,
    normalize_composition_json,
)
from .composition_planner import (
    ComposerFormPlan,
    ComposerFormSection,
    ComposerHarmonyPlan,
    ComposerMotifContext,
    ComposerTrackDraft,
    OversizedLLMGenerationRequestError,
    ValidationDiagnostic,
    coerce_instrumentation_labels,
    enforce_llm_generation_bounds,
    summarize_diagnostics,
    summarize_form_plan,
    summarize_track_draft,
)
from .composition_timing import bar_duration_ticks, derive_section_boundaries
from .composition_validator import validate_composition_integrity
from .composition_analysis import analyze_composition, build_llm_analysis_context
from ..analysis_schemas import CompositionAnalysisError
from .generation_constraints import (
    DUPLICATE_INSTRUMENT_ROLE_CODE,
    GenerationConstraints,
    build_generation_constraints,
    diagnostics_from_validation_report,
    freeze_form_resolved_fields,
    log_instrumentation_analysis,
    prompt_parameters_hard_block,
    validate_generation_constraints,
)
from .instrument_identity import (
    analyze_instrumentation,
    normalize_instrument_identity,
    normalize_role,
)
from .composition_tonality import analyze_harmony_tonality


logger = logging.getLogger(__name__)

COMPOSER_STAGES = (
    "plan_form",
    "plan_harmony",
    "compose_melody",
    "compose_bass",
    "compose_accompaniment",
    "assemble_composition",
    "normalize_composition",
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


class GenerationConstraintViolationError(InvalidLLMOutputError):
    """Raised when hard generation constraints remain violated after repair."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: list[ValidationDiagnostic] | None = None,
        report: GenerationValidationReport | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = list(diagnostics or [])
        self.report = report


__all__ = [
    "GenerationConstraintViolationError",
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
    constraints: GenerationConstraints
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
    validation_report: GenerationValidationReport
    validation_ok: bool
    current_stage: str
    failed_stage: str
    repair_target: str
    stage_retry_count: int
    stage_raw_outputs: dict[str, str]
    pre_normalize_hard_summary: dict[str, Any]
    repair_actions: list[GenerationRepairAction]
    repair_analysis_context: str


async def generate_music_json(
    request: LLMMusicGenerationRequest,
    settings: LLMSettings | None = None,
) -> tuple[CompositionV2, list[str], LLMProviderSettings, GenerationValidationReport | None]:
    active_settings = settings or load_llm_settings()
    enforce_llm_generation_bounds(request)
    constraints = build_generation_constraints(request)
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
            return await generate_fake_music_json(request, provider, constraints=constraints)
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
            "constraint_key": constraints.key,
            "key_user_specified": constraints.key_user_specified,
        },
    )
    logger.debug("LLM prompt parameters", extra={"prompt": _sanitized_prompt(request)})

    retry_limit = request.options.max_retries
    state: _GenerationState = {
        "request": request,
        "provider": provider,
        "constraints": constraints,
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
        validation_report = result.get("validation_report")
        if music is None or not result.get("validation_ok"):
            diagnostics = result.get("validation_diagnostics") or []
            codes = [item.code for item in diagnostics if getattr(item, "severity", "error") == "error"]
            messages = [item.message for item in diagnostics if getattr(item, "severity", "error") == "error"]
            detail = "; ".join(messages[:5]) if messages else (", ".join(codes) or "unknown validation failure")
            report = validation_report or GenerationValidationReport(
                status="failed",
                errors=[],
                warnings=[],
                repair_attempts=int(result.get("retry_count", 0)),
            )
            raise GenerationConstraintViolationError(
                "LLM returned invalid or non-playable composition after staged generation/repair: "
                f"{detail}",
                diagnostics=diagnostics,
                report=report,
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
                "validation_status": validation_report.status if validation_report else None,
            },
        )
        return music, warnings, provider, validation_report


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
    workflow.add_node("normalize_composition", _normalize_composition)
    workflow.add_node("validate_composition", _validate_composition)
    workflow.add_node("repair_composition", _repair_composition)

    workflow.set_entry_point("plan_form")
    workflow.add_edge("plan_form", "plan_harmony")
    workflow.add_edge("plan_harmony", "compose_melody")
    workflow.add_edge("compose_melody", "compose_bass")
    workflow.add_edge("compose_bass", "compose_accompaniment")
    workflow.add_edge("compose_accompaniment", "assemble_composition")
    workflow.add_edge("assemble_composition", "normalize_composition")
    workflow.add_edge("normalize_composition", "validate_composition")
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
            "normalize_composition": "normalize_composition",
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
        "normalize_composition",
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
    constraints = state.get("constraints")
    if form is None or harmony is None or melody is None or bass is None:
        raise InvalidLLMOutputError("Cannot assemble composition; required stage outputs are missing")
    if constraints is None:
        raise InvalidLLMOutputError("Cannot assemble composition; generation constraints are missing")

    drafts = [melody, bass, *accompaniment]
    duration_ticks: int | None = None
    try:
        ticks_per_quarter = DEFAULT_TICKS_PER_QUARTER
        locked_key = constraints.key or form.key
        locked_meter = constraints.time_signature
        locked_bars = constraints.duration_bars
        locked_tempo = form.tempo
        if locked_tempo < constraints.tempo_min:
            locked_tempo = constraints.tempo_min
        elif locked_tempo > constraints.tempo_max:
            locked_tempo = constraints.tempo_max

        bar_ticks = bar_duration_ticks(locked_meter, ticks_per_quarter)
        if constraints.sections is not None:
            section_payloads = [
                {"type": section.type, "bar_count": section.bar_count} for section in constraints.sections
            ]
        else:
            section_payloads = [
                {"type": section.type, "bar_count": section.bar_count} for section in form.sections
            ]
        derived_sections = derive_section_boundaries(section_payloads, locked_meter, ticks_per_quarter)
        sections = [
            CompositionV2Section.model_validate({**item, "id": f"section-{index}"})
            for index, item in enumerate(derived_sections, start=1)
        ]
        duration_ticks = locked_bars * bar_ticks

        tracks: list[CompositionV2Track] = []
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
            CompositionV2HarmonyItem(bar=event.bar, chord=event.chord) for event in harmony.events
        ]
        composition = CompositionV2(
            schema_version=COMPOSITION_SCHEMA_VERSION_V2,
            tempo=locked_tempo,
            key=locked_key,
            time_signature=locked_meter,
            ticks_per_quarter=ticks_per_quarter,
            duration_ticks=duration_ticks,
            bar_count=locked_bars,
            sections=sections,
            tracks=tracks,
            harmony=harmony_items,
            tempo_changes=[],
            time_signature_changes=[],
            key_changes=[],
            markers=[],
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

    hard_summary = {
        "key": composition.key,
        "time_signature": composition.time_signature,
        "bar_count": composition.bar_count,
        "tempo": composition.tempo,
        "duration_ticks": composition.duration_ticks,
        "section_types": [section.type for section in composition.sections],
        "section_bars": [section.bar_count for section in composition.sections],
    }
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "schema_version": composition.schema_version,
            "bar_count": composition.bar_count,
            "track_count": len(composition.tracks),
            "event_count": sum(len(track.events) for track in composition.tracks),
            "duration_ticks": composition.duration_ticks,
            "tempo_change_count": len(composition.tempo_changes),
            "marker_count": len(composition.markers),
            "articulation_note_count": sum(
                1
                for track in composition.tracks
                for event in track.events
                if getattr(event, "articulations", None)
            ),
        },
    )
    logger.debug(
        "Assembled composition track summary",
        extra={
            "track_ids": [track.id for track in composition.tracks],
            "roles": [track.role for track in composition.tracks],
            "events_by_track": {track.id: len(track.events) for track in composition.tracks},
            "hard_fields": hard_summary,
        },
    )
    return {
        **state,
        "music": composition,
        "pre_normalize_hard_summary": hard_summary,
        "current_stage": stage,
        "failed_stage": "",
        "parsed_json": composition.model_dump(),
    }


def _normalize_composition(state: _GenerationState) -> _GenerationState:
    stage = "normalize_composition"
    music = state.get("music")
    constraints = state.get("constraints")
    logger.info(
        "Composer stage started",
        extra=_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
    )
    if music is None:
        return {
            **state,
            "current_stage": stage,
            "validation_ok": False,
            "failed_stage": state.get("failed_stage") or "assemble_composition",
        }
    if constraints is None:
        raise InvalidLLMOutputError("normalize_composition requires generation constraints")

    pre = state.get("pre_normalize_hard_summary") or {
        "key": music.key,
        "time_signature": music.time_signature,
        "bar_count": music.bar_count,
        "tempo": music.tempo,
        "duration_ticks": music.duration_ticks,
        "section_types": [section.type for section in music.sections],
        "section_bars": [section.bar_count for section in music.sections],
    }
    try:
        normalized = normalize_composition_json(music.model_dump(mode="json"))
    except (CompositionNormalizationError, ValidationError, ValueError) as exc:
        diagnostic = ValidationDiagnostic(
            code="normalization_failed",
            message=f"Normalization failed: {str(exc)[:200]}",
            severity="error",
            context={"stage": stage},
        )
        logger.error(
            "Normalization failed",
            extra={"error_type": type(exc).__name__, "detail": str(exc)[:200]},
        )
        return {
            **state,
            "validation_ok": False,
            "validation_diagnostics": [diagnostic],
            "failed_stage": "assemble_composition",
            "current_stage": stage,
        }

    post = {
        "key": normalized.key,
        "time_signature": normalized.time_signature,
        "bar_count": normalized.bar_count,
        "tempo": normalized.tempo,
        "duration_ticks": normalized.duration_ticks,
        "section_types": [section.type for section in normalized.sections],
        "section_bars": [section.bar_count for section in normalized.sections],
    }
    rewritten_fields = [name for name in pre if pre.get(name) != post.get(name)]
    if rewritten_fields:
        diagnostic = ValidationDiagnostic(
            code="constraint_normalization_rewrite",
            message="Normalization attempted to rewrite locked hard fields",
            severity="error",
            context={"fields": rewritten_fields, "stage": stage},
        )
        logger.error(
            "Normalization rewrote locked hard fields",
            extra={"fields": rewritten_fields},
        )
        return {
            **state,
            "music": normalized,
            "validation_ok": False,
            "validation_diagnostics": [diagnostic],
            "failed_stage": "normalize_composition",
            "current_stage": stage,
        }

    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "normalization_path": "canonical",
            "hard_field_count": len(post),
        },
    )
    logger.debug(
        "Normalization hard-field comparison",
        extra={"pre": pre, "post": post},
    )
    return {
        **state,
        "music": normalized,
        "current_stage": stage,
        "parsed_json": normalized.model_dump(),
    }


def _validate_composition(state: _GenerationState) -> _GenerationState:
    stage = "validate_composition"
    music = state.get("music")
    request = state["request"]
    constraints = state.get("constraints")
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

    integrity = validate_composition_integrity(
        music,
        complexity=request.prompt.complexity,
    )
    logger.debug(
        "Integrity validation skipped requested-instrument checks; generation constraints own them",
        extra={
            "requested_instrument_count": len(request.prompt.instruments),
            "ownership": "generation_constraints",
        },
    )
    constraint_report: GenerationValidationReport | None = None
    constraint_diagnostics: list[ValidationDiagnostic] = []
    if constraints is not None:
        constraint_report = validate_generation_constraints(
            music,
            constraints,
            repair_attempts=int(state.get("retry_count", 0)),
        )
        constraint_diagnostics = diagnostics_from_validation_report(constraint_report)

    diagnostics = [*integrity.diagnostics, *constraint_diagnostics]
    # Prefer existing stage diagnostics (e.g. normalization rewrite) when present.
    prior = [
        item
        for item in (state.get("validation_diagnostics") or [])
        if item.severity == "error"
        and item.code in {"constraint_normalization_rewrite", "normalization_failed", "schema_invalid"}
    ]
    if prior:
        diagnostics = [*prior, *diagnostics]

    errors = [item for item in diagnostics if item.severity == "error"]
    ok = integrity.ok and (constraint_report.ok if constraint_report is not None else True) and not prior
    if constraint_report is not None and not constraint_report.ok:
        ok = False
    failed_stage = _infer_failed_stage(errors) if not ok else ""

    if constraint_report is not None and ok and int(state.get("retry_count", 0)) > 0:
        constraint_report = constraint_report.model_copy(update={"status": "repaired"})

    repair_actions = list(state.get("repair_actions") or [])
    if constraint_report is not None and repair_actions:
        constraint_report = constraint_report.model_copy(update={"repair_actions": repair_actions})
        logger.debug(
            "Attached repair actions to validation report",
            extra={
                "repair_action_count": len(repair_actions),
                "targets": [action.target for action in repair_actions],
            },
        )

    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "validation_ok": ok,
            "integrity_ok": integrity.ok,
            "constraint_status": constraint_report.status if constraint_report else None,
            "error_codes": [item.code for item in errors],
            "warning_count": len([item for item in diagnostics if item.severity == "warning"]),
        },
    )
    logger.debug(
        "Validation gate result counts",
        extra={
            "integrity_errors": len(integrity.errors),
            "integrity_warnings": len(integrity.warnings),
            "constraint_errors": len(constraint_report.errors) if constraint_report else 0,
            "constraint_warnings": len(constraint_report.warnings) if constraint_report else 0,
        },
    )
    return {
        **state,
        "validation_ok": ok,
        "validation_diagnostics": diagnostics,
        "validation_report": constraint_report,
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
        "normalize_composition",
        "validate_composition",
    }:
        repair_target = "assemble_composition"

    # Capture advisory analysis from the post-assembly candidate before clearing music.
    repair_analysis_context = _safe_repair_analysis_context(state.get("music"))

    # Preserve valid upstream drafts when repairing a later stage.
    cleared: _GenerationState = {**state}
    if repair_target == "plan_form":
        cleared.pop("form_plan", None)
        cleared.pop("harmony_plan", None)
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("motif_context", None)
        cleared.pop("music", None)
    elif repair_target == "plan_harmony":
        cleared.pop("harmony_plan", None)
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("motif_context", None)
        cleared.pop("music", None)
    elif repair_target == "compose_melody":
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("motif_context", None)
        cleared.pop("music", None)
    elif repair_target == "compose_bass":
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("music", None)
    elif repair_target == "compose_accompaniment":
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("music", None)
    elif repair_target in {"assemble_composition", "normalize_composition"}:
        cleared.pop("music", None)

    logger.warning(
        "Composer repair attempt started",
        extra={
            **_stage_log_extra(state, stage, attempt=retry_count),
            "repair_target": repair_target,
            "diagnostic_codes": codes,
            "preserved_form": cleared.get("form_plan") is not None,
            "preserved_harmony": cleared.get("harmony_plan") is not None,
        },
    )

    affected_requirements: list[str] = []
    affected_track_ids: list[str] = []
    for item in diagnostics:
        context = item.context or {}
        missing = context.get("missing_families") or context.get("missing_requirements")
        if isinstance(missing, list):
            affected_requirements.extend(str(value) for value in missing)
        track_ids = context.get("track_ids")
        if isinstance(track_ids, list):
            affected_track_ids.extend(str(value) for value in track_ids)
        track_id = context.get("track_id")
        if isinstance(track_id, str) and track_id:
            affected_track_ids.append(track_id)
    # Stable unique order
    affected_requirements = list(dict.fromkeys(affected_requirements))
    affected_track_ids = list(dict.fromkeys(affected_track_ids))
    repair_action = GenerationRepairAction(
        target=repair_target,
        attempt=retry_count,
        diagnostic_codes=codes,
        affected_requirements=affected_requirements,
        affected_track_ids=affected_track_ids,
        detail=f"Retry {repair_target} after {', '.join(codes) or 'unspecified'}",
    )
    repair_actions = list(state.get("repair_actions") or [])
    repair_actions.append(repair_action)
    logger.info(
        "Recorded generation repair action",
        extra={
            "target": repair_action.target,
            "attempt": repair_action.attempt,
            "diagnostic_codes": repair_action.diagnostic_codes,
            "affected_requirements": repair_action.affected_requirements,
            "affected_track_ids": repair_action.affected_track_ids,
        },
    )

    warnings = list(state.get("warnings") or [])
    warnings.append(
        "Staged composition failed validation; retrying "
        f"{repair_target} with diagnostics: {', '.join(codes) or 'unspecified'}."
    )
    updated: _GenerationState = {
        **cleared,
        "retry_count": retry_count,
        "repair_target": repair_target,
        "current_stage": stage,
        "warnings": warnings,
        "validation_ok": False,
        "repair_actions": repair_actions,
        "repair_analysis_context": repair_analysis_context,
    }
    logger.info(
        "Composer repair routed to stage",
        extra={
            **_stage_log_extra(updated, stage, attempt=retry_count),
            "repair_target": repair_target,
            "diagnostic_codes": codes,
            "diagnostic_count": len(diagnostics),
            "repair_analysis_chars": len(repair_analysis_context),
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
            "prompt_length": len(prompt),
            "has_repair_diagnostics": bool(
                state.get("repair_target") == stage and state.get("validation_diagnostics")
            ),
            "sanitized_request": _sanitized_prompt(request),
            "constraint_summary": (
                state["constraints"].hard_summary() if state.get("constraints") else None
            ),
        },
    )

    if state.get("repair_target") == stage and state.get("validation_diagnostics"):
        prompt = _append_repair_diagnostics(
            prompt,
            state.get("validation_diagnostics") or [],
            analysis_context=state.get("repair_analysis_context") or "",
        )

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
    raw_instrumentation = parsed.get("instrumentation")
    needs_coercion = isinstance(raw_instrumentation, list) and any(
        not isinstance(item, str) for item in raw_instrumentation
    )
    if needs_coercion:
        coerced = coerce_instrumentation_labels(raw_instrumentation)
        logger.info(
            "[FIX] Coerced plan_form instrumentation objects to family strings",
            extra={
                "raw_entry_count": len(raw_instrumentation),
                "coerced_count": len(coerced),
                "coerced_labels": coerced,
                "raw_entry_types": [type(item).__name__ for item in raw_instrumentation],
            },
        )
        parsed = {**parsed, "instrumentation": coerced}
    return ComposerFormPlan.model_validate(parsed)


def _after_form_plan(state: _GenerationState, form: ComposerFormPlan, parsed: dict[str, Any]) -> _GenerationState:
    constraints = state.get("constraints")
    if constraints is None:
        raise InvalidLLMOutputError("Form stage requires generation constraints")

    frozen, drift_diagnostics = freeze_form_resolved_fields(constraints, form)
    updates: dict[str, Any] = {}
    if constraints.key_user_specified and form.key != constraints.key:
        updates["key"] = constraints.key
    if form.time_signature != constraints.time_signature:
        updates["time_signature"] = constraints.time_signature
    if form.bar_count != constraints.duration_bars:
        updates["bar_count"] = constraints.duration_bars
    if form.tempo < constraints.tempo_min:
        updates["tempo"] = constraints.tempo_min
    elif form.tempo > constraints.tempo_max:
        updates["tempo"] = constraints.tempo_max
    if constraints.sections_user_specified and constraints.sections is not None:
        updates["sections"] = [
            ComposerFormSection(
                type=section.type,
                start_bar=section.start_bar,
                bar_count=section.bar_count,
            )
            for section in constraints.sections
        ]

    corrected = form.model_copy(update=updates) if updates else form
    # Re-validate contiguous sections after coercion.
    corrected = ComposerFormPlan.model_validate(corrected.model_dump())

    for item in drift_diagnostics:
        logger.warning(
            "Form stage hard-constraint drift",
            extra={"code": item.code, "expected": item.context.get("expected"), "actual": item.context.get("actual")},
        )

    logger.info(
        "Resolved form plan",
        extra={
            "bar_count": corrected.bar_count,
            "section_count": len(corrected.sections),
            "tempo": corrected.tempo,
            "key": corrected.key,
            "time_signature": corrected.time_signature,
            "constraint_check": "fail" if drift_diagnostics else "pass",
            "key_source": "user" if frozen.key_user_specified else "form",
            "sections_source": "user" if frozen.sections_user_specified else "form",
        },
    )
    logger.debug(
        "Form constraint freeze summary",
        extra={
            "constraint_ids": list(frozen.hard_summary().keys()),
            "drift_codes": [item.code for item in drift_diagnostics],
            "coerced_fields": list(updates.keys()),
        },
    )
    return {
        **state,
        "form_plan": corrected,
        "constraints": frozen,
        "parsed_json": parsed,
        "validation_diagnostics": list(state.get("validation_diagnostics") or []) + drift_diagnostics,
    }


def _parse_harmony_plan(parsed: dict[str, Any], state: _GenerationState) -> ComposerHarmonyPlan:
    if "events" not in parsed and isinstance(parsed.get("harmony"), list):
        parsed = {"events": parsed["harmony"]}
    return ComposerHarmonyPlan.model_validate(parsed)


def _after_harmony_plan(
    state: _GenerationState, harmony: ComposerHarmonyPlan, parsed: dict[str, Any]
) -> _GenerationState:
    form = state.get("form_plan")
    constraints = state.get("constraints")
    section_coverage = sorted({event.section_type for event in harmony.events if event.section_type})
    diagnostics = list(state.get("validation_diagnostics") or [])
    if constraints and constraints.key and harmony.events:
        tonal = analyze_harmony_tonality(
            [(event.bar, event.chord) for event in harmony.events],
            constraints.key,
            bar_count=form.bar_count if form else max((event.bar for event in harmony.events), default=1),
            boundary_bars=[section.start_bar for section in form.sections] if form else None,
        )
        if tonal.contradicts:
            diagnostics.append(
                ValidationDiagnostic(
                    code="constraint_tonality_center",
                    message="Harmony plan tonal center contradicts the locked key",
                    severity="error",
                    context={
                        "expected": constraints.key,
                        "actual": tonal.winning_candidate,
                        "stage": "plan_harmony",
                        "reason": tonal.reason,
                    },
                )
            )
            logger.warning(
                "Harmony stage constraint check failed",
                extra={"code": "constraint_tonality_center", "winning_candidate": tonal.winning_candidate},
            )
        else:
            logger.info("Harmony stage constraint check passed", extra={"requested_key": constraints.key})
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
    return {**state, "harmony_plan": harmony, "parsed_json": parsed, "validation_diagnostics": diagnostics}


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
    diagnostics = list(state.get("validation_diagnostics") or [])
    constraints = state.get("constraints")
    assignment = _resolve_upstream_instrument_assignments(state)
    upstream_tracks = [
        track
        for track in (state.get("melody_draft"), state.get("bass_draft"))
        if track is not None
    ]
    all_tracks = [*upstream_tracks, *tracks]
    if not any(track.role in {"harmony", "pad", "rhythm", "countermelody"} for track in tracks):
        logger.warning(
            "Accompaniment stage missing required harmonic role",
            extra={"roles": [track.role for track in tracks]},
        )
    if constraints is not None:
        analysis = analyze_instrumentation(
            list(constraints.requested_instruments),
            all_tracks,
            allow_extra=constraints.allow_extra_instrument_families,
        )
        log_instrumentation_analysis(analysis, stage="compose_accompaniment")
        missing = list(analysis.missing_keys)
        unexpected = list(analysis.unexpected_identities)
        if missing:
            diagnostics.append(
                ValidationDiagnostic(
                    code="constraint_missing_instrument_family",
                    message="Requested instrument families were not assigned across generated tracks",
                    severity="error",
                    context={
                        "missing_families": missing,
                        "stage": "compose_accompaniment",
                        "present_instruments": [track.instrument for track in all_tracks],
                    },
                )
            )
        if unexpected:
            diagnostics.append(
                ValidationDiagnostic(
                    code="constraint_unexpected_instrument_family",
                    message="Unrequested instrument families were generated",
                    severity="error",
                    context={
                        "unexpected_families": unexpected,
                        "stage": "compose_accompaniment",
                    },
                )
            )
        for group in analysis.actionable_duplicate_groups:
            # Prefer accompaniment-stage ownership when a draft repeats an upstream
            # instrument/role or duplicates within accompaniment.
            diagnostics.append(
                ValidationDiagnostic(
                    code=DUPLICATE_INSTRUMENT_ROLE_CODE,
                    message=(
                        "Accompaniment reuses a reserved or duplicate normalized "
                        "instrument/role assignment"
                    ),
                    severity="error",
                    context={
                        "identity": group.identity,
                        "role": group.role,
                        "track_ids": list(group.track_ids),
                        "event_counts": list(group.event_counts),
                        "content_relationship": group.content_relationship,
                        "stage": "compose_accompaniment",
                        "repairable": True,
                    },
                )
            )
            logger.warning(
                "Attempted redundant instrument/role assignment in accompaniment",
                extra={
                    "identity": group.identity,
                    "role": group.role,
                    "track_ids": list(group.track_ids),
                    "content_relationship": group.content_relationship,
                },
            )
        logger.info(
            "Accompaniment stage constraint check",
            extra={
                "pass": not missing and not unexpected and not analysis.actionable_duplicate_groups,
                "missing_families": missing,
                "unexpected_families": unexpected,
                "actionable_duplicate_count": len(analysis.actionable_duplicate_groups),
                "already_satisfied": assignment["already_satisfied"],
                "instrument_assignments": [track.instrument for track in all_tracks],
            },
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
        "validation_diagnostics": diagnostics,
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
) -> CompositionV2Track:
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
            CompositionV2NoteEvent(
                pitch=event.pitch,
                start_tick=event.start_tick,
                duration_ticks=event.duration_ticks,
                velocity=event.velocity,
                staff=event.staff,
                id=event.id,
                articulations=[],
                tie=None,
            )
            for event in draft.events
        ]
    else:
        fitted = _fit_events_to_duration(
            draft.events,
            duration_ticks,
            track_role=draft.role,
            preferred_track_id=preferred,
        )
        events = [
            CompositionV2NoteEvent(
                pitch=event.pitch,
                start_tick=event.start_tick,
                duration_ticks=event.duration_ticks,
                velocity=event.velocity,
                staff=event.staff,
                id=event.id,
                articulations=[],
                tie=None,
            )
            for event in fitted
        ]
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
    return CompositionV2Track(
        id=track_id,
        name=draft.name or f"{instrument.title()} {draft.role}",
        instrument=instrument,
        role=draft.role,
        midi_program=midi_program,
        channel=channel,
        is_drum=draft.is_drum,
        volume=draft.volume,
        pan=draft.pan,
        expression=127,
        staff=draft.staff,
        events=events,
        dynamic_marks=[],
        sustain_pedals=[],
        automation=[],
    )


def _build_form_prompt(state: _GenerationState) -> str:
    request = state["request"]
    prompt = request.prompt
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    sections = [section.model_dump() for section in prompt.sections]
    key_rule = (
        f'- key MUST be exactly "{constraints.key}" (immutable hard constraint)'
        if constraints.key_user_specified and constraints.key
        else '- key format like "C minor" or "F# major"; once chosen it becomes the locked tonal center'
    )
    sections_rule = (
        f"- sections MUST match this exact sequence and bar counts: {json.dumps(hard_block['hard']['sections'])}"
        if constraints.sections_user_specified
        else f"- sections must be contiguous from start_bar=1 and sum exactly to bar_count={constraints.duration_bars}"
    )
    return f"""
You are planning musical form for a composition.v2 generator.
IMMUTABLE HARD CONSTRAINTS (must not violate):
{json.dumps(hard_block["hard"], ensure_ascii=True)}
SOFT creative preferences (may guide style only):
{json.dumps(hard_block["soft"], ensure_ascii=True)}

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
- tempo MUST be within inclusive bounds {constraints.tempo_min}..{constraints.tempo_max}
{key_rule}
- time_signature MUST be exactly {constraints.time_signature}
- bar_count MUST equal {constraints.duration_bars}
{sections_rule}
- section types: intro, verse, pre_chorus, chorus, bridge, solo, breakdown, outro
- instrumentation MUST be a JSON array of instrument family strings only (e.g. ["piano", "bass", "violin"]); never objects, never role fields
- instrumentation MUST cover required families {list(constraints.required_instrument_families)}; melody/bass/accompaniment roles are assigned in later stages
- do NOT invent unrequested instrument families unless allow_extra_instrument_families is true
- soft preferences: genre={prompt.genre}, mood={prompt.mood}, complexity={prompt.complexity}
- freeform instructions present: {constraints.has_instructions}
- requested sections hint: {json.dumps(sections) if sections else "design coherent structure totaling duration_bars"}
- requested instruments: {", ".join(prompt.instruments)}
""".strip()


def _build_harmony_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    locked_key = constraints.key or form.key
    return f"""
You are writing harmonic progression metadata for composition.v2.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
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
- tonal center MUST remain {locked_key}; do not drift to a competing key
- secondary dominants, borrowed chords, and chromatic color are allowed when the aggregate tonic stays {locked_key}
- soft preferences: genre={request.prompt.genre}, mood={request.prompt.mood}, complexity={request.prompt.complexity}
""".strip()


def _build_melody_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    locked_key = constraints.key or form.key
    return f"""
You are composing the primary melody track in canonical tick timing.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
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
- tonal center MUST remain {locked_key}; chromatic passing tones are allowed
- instrument MUST use a requested family from {list(constraints.required_instrument_families)}: {", ".join(request.prompt.instruments)}
- complexity={request.prompt.complexity}
""".strip()


def _build_bass_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    melody = state.get("melody_draft")
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    melody_summary = summarize_track_draft(melody)
    locked_key = constraints.key or form.key
    return f"""
You are composing the bass track in canonical tick timing.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
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
- follow harmonic roots and cadences in locked key {locked_key}
- stay in bass range C1-C4
- ticks_per_quarter={ticks}; bar length={bar_ticks}
- composition duration ticks={form.bar_count * bar_ticks}
- include enough notes for the full form (roughly one event per bar minimum; avoid long empty stretches)
- instrument MUST satisfy a requested bass-capable family from {list(constraints.required_instrument_families)}
- reflect section intensity from the form plan
""".strip()


def _build_accompaniment_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    motif = state.get("motif_context") or ComposerMotifContext()
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    assignment = _resolve_upstream_instrument_assignments(state)
    melody = state.get("melody_draft")
    bass = state.get("bass_draft")
    reserved_ids = [track.id for track in (melody, bass) if track is not None and track.id]
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    locked_key = constraints.key or form.key
    missing = assignment["missing_requirements"]
    example_tracks = _assignment_aware_accompaniment_example(
        missing_requirements=missing,
        reserved_roles=assignment["reserved_instrument_roles"],
        ticks=ticks,
        bar_ticks=bar_ticks,
    )
    logger.info(
        "Resolved accompaniment assignment context",
        extra={
            "already_satisfied": assignment["already_satisfied"],
            "missing_requirements": missing,
            "reserved_instrument_roles": assignment["reserved_instrument_roles"],
            "upstream_assignments": assignment["upstream_assignments"],
        },
    )
    logger.debug(
        "Accompaniment assignment details",
        extra={
            "upstream_assignments": assignment["upstream_assignments"],
            "satisfied_keys": assignment["already_satisfied"],
            "missing_keys": missing,
            "reserved_pairs": assignment["reserved_instrument_roles"],
        },
    )
    return f"""
You are composing accompaniment / harmonic support tracks in canonical tick timing.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}
Motif context:
{json.dumps(motif.model_dump(), ensure_ascii=True)}
Requested instruments: {", ".join(request.prompt.instruments)}
Required sound sources: {list(constraints.required_instrument_families)}
Reserved track IDs already used by melody/bass (do not reuse): {", ".join(reserved_ids) if reserved_ids else "none"}

ASSIGNMENT CONTEXT (machine-readable; recompute from current drafts):
{json.dumps({
    "already_satisfied": assignment["already_satisfied"],
    "missing_requirements": missing,
    "reserved_instrument_roles": assignment["reserved_instrument_roles"],
    "upstream_assignments": assignment["upstream_assignments"],
}, ensure_ascii=True)}

Return JSON only:
{{
  "tracks": {json.dumps(example_tracks, ensure_ascii=True)},
  "skipped": []
}}

Rules:
- always include at least one harmony/accompaniment role track with playable events
- cover EVERY entry in missing_requirements using track.instrument (not display name)
- you MAY reuse an already_satisfied instrument only for a DISTINCT role
- do NOT emit another track with a reserved_instrument_roles pair (same normalized instrument + role)
- use instrument-qualified display names (e.g. "Piano Accompaniment", "Strings Pad"); names are UI metadata only
- for piano accompaniment prefer one piano track with staff "grand" and per-note staff treble/bass
- do NOT add unrequested instrument identities unless allow_extra_instrument_families is true
- if a non-required color instrument is skipped, list it under skipped with a short reason
- track ids must be unique within this response and must not reuse reserved melody/bass ids
- strings/pad pitches should stay within C2-C7 (cello lows OK; avoid sub-bass mud)
- tonal center MUST remain {locked_key}
- ticks_per_quarter={ticks}; bar length={bar_ticks}; total bars={form.bar_count}
- do not rely on harmony metadata as audible content
""".strip()


def _resolve_upstream_instrument_assignments(state: _GenerationState) -> dict[str, Any]:
    """Derive satisfied/missing requirements and reserved instrument/role pairs."""
    constraints = state.get("constraints")
    request = state.get("request")
    requested = list(
        constraints.requested_instruments
        if constraints is not None
        else (request.prompt.instruments if request is not None else [])
    )
    upstream: list[ComposerTrackDraft] = [
        track
        for track in (state.get("melody_draft"), state.get("bass_draft"))
        if track is not None
    ]
    analysis = analyze_instrumentation(
        requested,
        upstream,
        allow_extra=bool(constraints.allow_extra_instrument_families) if constraints else False,
    )
    upstream_assignments = []
    reserved_pairs: list[dict[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for track in upstream:
        identity = normalize_instrument_identity(track.instrument) or track.instrument.strip().lower()
        role = normalize_role(track.role)
        upstream_assignments.append(
            {
                "track_id": track.id,
                "instrument": track.instrument,
                "identity": identity,
                "role": role,
            }
        )
        pair = (identity, role)
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            reserved_pairs.append({"identity": identity, "role": role})
    return {
        "already_satisfied": list(analysis.satisfied_keys),
        "missing_requirements": list(analysis.missing_keys),
        "reserved_instrument_roles": reserved_pairs,
        "upstream_assignments": upstream_assignments,
    }


def _assignment_aware_accompaniment_example(
    *,
    missing_requirements: list[str],
    reserved_roles: list[dict[str, str]],
    ticks: int,
    bar_ticks: int,
) -> list[dict[str, Any]]:
    """Build a neutral/assignment-aware example that avoids reserved pairs."""
    reserved = {(item["identity"], item["role"]) for item in reserved_roles}
    examples: list[dict[str, Any]] = []

    def _can_use(identity: str, role: str) -> bool:
        return (identity, role) not in reserved

    # Prefer covering missing requirements with distinct roles.
    for key in missing_requirements:
        if key == "strings" and _can_use("strings", "pad"):
            examples.append(
                {
                    "id": "strings-1",
                    "name": "Strings Pad",
                    "instrument": "strings",
                    "role": "pad",
                    "events": [
                        {"pitch": "E4", "start_tick": 0, "duration_ticks": bar_ticks, "velocity": 60}
                    ],
                }
            )
        elif key == "piano" and _can_use("piano", "harmony"):
            examples.append(
                {
                    "id": "harmony-1",
                    "name": "Piano Accompaniment",
                    "instrument": "piano",
                    "role": "harmony",
                    "staff": "grand",
                    "events": [
                        {
                            "pitch": "A3",
                            "start_tick": 0,
                            "duration_ticks": ticks,
                            "velocity": 70,
                            "staff": "bass",
                        },
                        {
                            "pitch": "C5",
                            "start_tick": 0,
                            "duration_ticks": ticks,
                            "velocity": 68,
                            "staff": "treble",
                        },
                    ],
                }
            )
        elif key not in {"bass", "drums"} and _can_use(key, "harmony"):
            examples.append(
                {
                    "id": f"{key}-harmony-1",
                    "name": f"{key.title()} Accompaniment",
                    "instrument": key,
                    "role": "harmony",
                    "events": [
                        {"pitch": "C4", "start_tick": 0, "duration_ticks": ticks, "velocity": 70}
                    ],
                }
            )

    if not examples and _can_use("piano", "harmony"):
        examples.append(
            {
                "id": "harmony-1",
                "name": "Piano Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "staff": "grand",
                "events": [
                    {
                        "pitch": "A3",
                        "start_tick": 0,
                        "duration_ticks": ticks,
                        "velocity": 70,
                        "staff": "bass",
                    },
                    {
                        "pitch": "C5",
                        "start_tick": 0,
                        "duration_ticks": ticks,
                        "velocity": 68,
                        "staff": "treble",
                    },
                ],
            }
        )
    elif not examples:
        # Fully neutral fallback that still demonstrates the schema.
        examples.append(
            {
                "id": "harmony-1",
                "name": "Accompaniment",
                "instrument": "piano",
                "role": "harmony",
                "events": [
                    {"pitch": "C4", "start_tick": 0, "duration_ticks": ticks, "velocity": 70}
                ],
            }
        )
    return examples


def _safe_repair_analysis_context(music: Composition | None) -> str:
    """Build advisory analysis from a post-assembly candidate for repair prompts only."""
    if music is None:
        return ""
    try:
        normalized = normalize_composition_json(music)
        report = analyze_composition(normalized, {"kind": "composition"})
        context = build_llm_analysis_context(report, purpose="generation_repair")
        logger.debug(
            "Repair analysis context ready",
            extra={
                "char_count": len(context),
                "report_status": report.status,
                "warning_count": len(report.warnings),
                "scope_kind": report.resolved_scope.kind,
            },
        )
        return context
    except CompositionAnalysisError as exc:
        logger.warning(
            "Repair analysis context skipped",
            extra={"error_code": exc.code},
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Repair analysis context skipped",
            extra={"error_type": type(exc).__name__},
        )
        return ""


def _append_repair_diagnostics(
    prompt: str,
    diagnostics: list[ValidationDiagnostic],
    *,
    analysis_context: str = "",
) -> str:
    payload = [item.model_dump() for item in diagnostics[:20]]
    text = (
        prompt
        + "\n\nPrevious output failed validation. Return corrected JSON only for this stage.\n"
        + "Diagnostics:\n"
        + json.dumps(payload, ensure_ascii=True)
        + "\nHard constraints above remain immutable and MUST be satisfied.\n"
    )
    if analysis_context:
        text += f"\n{analysis_context}\n"
    return text


def _infer_failed_stage(errors: list[ValidationDiagnostic]) -> str:
    codes = {item.code for item in errors}
    messages = " ".join(item.message.lower() for item in errors)
    contexts = " ".join(
        str(item.context).lower() for item in errors if getattr(item, "context", None)
    )
    role_haystack = f"{messages} {contexts}"

    def _stage_from_context() -> str | None:
        for item in errors:
            stage = (item.context or {}).get("stage") if item.context else None
            if isinstance(stage, str) and stage:
                return stage
        return None

    context_stage = _stage_from_context()

    if codes & {
        "constraint_key_mismatch",
        "constraint_meter_mismatch",
        "constraint_bar_count_mismatch",
        "constraint_tempo_out_of_range",
        "constraint_sections_mismatch",
        "constraint_duration_mismatch",
        "constraint_tonality_metadata",
    }:
        return "plan_form"
    if "constraint_tonality_center" in codes:
        # Prefer harmony restart; note-heavy failures still start at melody via context.
        if context_stage == "compose_melody" or "note" in role_haystack:
            return "compose_melody"
        return "plan_harmony"
    if codes & {
        "constraint_missing_instrument_family",
        "constraint_unexpected_instrument_family",
        "constraint_duplicate_instrument_role",
    }:
        return "compose_accompaniment"
    if "constraint_normalization_rewrite" in codes or "normalization_failed" in codes:
        return "assemble_composition"
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
        return "compose_melody"
    if "schema_invalid" in codes:
        for item in errors:
            failed = (item.context or {}).get("failed_stage") if item.context else None
            if isinstance(failed, str) and failed:
                return failed
        return "assemble_composition"
    if context_stage:
        return context_stage
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
        warnings.append("LLM returned legacy music JSON; normalized to composition.v2.")
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
