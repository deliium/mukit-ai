import json
import logging
import os
import re
from typing import Any, TypedDict

from pydantic import ValidationError

from ..llm_settings import LLMProviderSettings, LLMSettings, load_llm_settings
from ..schemas import (
    Composition,
    CompositionV2,
    CompositionV2NoteEvent,
    CompositionV2Section,
    CompositionV2Track,
    GenerationPipelineId,
    GenerationRepairAction,
    GenerationValidationReport,
    LLMMusicGenerationRequest,
    NoteEvent,
    ThematicRecurrenceOutcome,
)
from ..composition_schemas import COMPOSITION_SCHEMA_VERSION_V2, CompositionV2MotifDefinition
from .composition_normalizer import (
    CompositionNormalizationError,
    INSTRUMENT_PROGRAMS,
    normalize_composition_json,
)
from .composition_planner import (
    ComposerFormPlan,
    ComposerFormSection,
    ComposerHarmonyPlan,
    ComposerThemePlan,
    ComposerTrackDraft,
    MECHANICAL_THEME_OPERATIONS,
    OversizedLLMGenerationRequestError,
    ValidationDiagnostic,
    coerce_instrumentation_labels,
    enforce_llm_generation_bounds,
    summarize_diagnostics,
    summarize_form_plan,
    summarize_theme_plan,
    summarize_track_draft,
)
from .composition_theme import (
    THEME_DIAGNOSTIC_CODES,
    THEME_IDENTITY_BELOW_THRESHOLD,
    THEME_SOURCE_EMPTY,
    THEME_TARGET_MISSING,
    THEME_TARGET_OUT_OF_BOUNDS,
    assign_draft_event_ids,
    default_theme_plan_for_form,
    empty_theme_plan,
    extract_seed_relative_cell,
    realize_theme_plan,
    thematic_report_payload,
    theme_plan_prompt_projection,
    validate_theme_plan_against_form,
)
from .composition_timing import bar_duration_ticks, derive_section_boundaries
from .composition_validator import (
    _min_events_for_complexity,
    validate_composition_integrity,
)
from .composition_analysis import analyze_composition, build_llm_analysis_context
from ..analysis_schemas import CompositionAnalysisError
from .generation_constraints import (
    DUPLICATE_INSTRUMENT_ROLE_CODE,
    GenerationConstraints,
    bounded_user_instructions_for_prompt,
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
    "plan_themes",
    "compose_melody",
    "compose_bass",
    "compose_accompaniment",
    "realize_themes",
    "assemble_composition",
    "normalize_composition",
    "validate_composition",
    "repair_composition",
)

HYBRID_COMPOSER_STAGES = (
    "plan_form",
    "plan_harmony",
    "plan_themes",
    "validate_plan",
    "repair_plan",
    "symbolic_condition",
    "symbolic_generate",
    "symbolic_decode_repair",
    "assemble_from_symbolic",
    "normalize_composition",
    "validate_composition",
    "repair_composition",
)

SYMBOLIC_PREFIX_STAGES = (
    "load_prefix",
    "symbolic_condition",
    "symbolic_generate",
    "symbolic_decode_repair",
    "assemble_from_symbolic",
    "normalize_composition",
    "validate_composition",
    "repair_composition",
)

DEFAULT_TICKS_PER_QUARTER = 480

PIPELINE_LLM_ONLY: GenerationPipelineId = "llm_only"
PIPELINE_HYBRID: GenerationPipelineId = "hybrid_plan_symbolic"
PIPELINE_CONTINUATION: GenerationPipelineId = "symbolic_continuation"
PIPELINE_VARIATION: GenerationPipelineId = "symbolic_variation"


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


class HybridPipelineUnavailableError(LLMGenerationError):
    """Raised when a hybrid/symbolic pipeline stage is not yet available."""

    def __init__(self, message: str, *, code: str = "symbolic_pipeline_unavailable") -> None:
        super().__init__(message)
        self.code = code


__all__ = [
    "GenerationConstraintViolationError",
    "HybridPipelineUnavailableError",
    "InvalidLLMOutputError",
    "LLMGenerationError",
    "NoLLMProviderConfiguredError",
    "OversizedLLMGenerationRequestError",
    "UnsupportedLLMProviderError",
    "generate_music_json",
    "resolve_generation_pipeline",
    "select_llm_provider",
]


class _GenerationState(TypedDict, total=False):
    request: LLMMusicGenerationRequest
    provider: LLMProviderSettings
    constraints: GenerationConstraints
    pipeline_id: GenerationPipelineId
    seed: int | None
    raw_output: str
    parsed_json: dict[str, Any]
    music: Composition
    retry_count: int
    warnings: list[str]
    form_plan: ComposerFormPlan
    harmony_plan: ComposerHarmonyPlan
    theme_plan: ComposerThemePlan
    composition_plan: Any  # CompositionPlan | None (avoid circular import at type time)
    melody_draft: ComposerTrackDraft
    bass_draft: ComposerTrackDraft
    accompaniment_drafts: list[ComposerTrackDraft]
    theme_motifs: list[CompositionV2MotifDefinition]
    theme_outcomes: list[dict[str, Any]]
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
    plan_ok: bool
    symbolic_ok: bool
    prefix_composition: CompositionV2 | None
    symbolic_result: Any
    symbolic_resample_count: int
    provenance_stages: list[dict[str, Any]]
    composer_model_id: str | None
    profile_soft_fragment: str
    profile_merge_provenance: dict[str, Any]
    reference_soft_fragment: str
    reference_legacy_summary: str | None
    reference_condition_provenance: dict[str, Any]


def resolve_generation_pipeline(request: LLMMusicGenerationRequest) -> GenerationPipelineId:
    """Return the request pipeline id, defaulting to llm_only."""
    pipeline = getattr(request.options, "pipeline", None) or PIPELINE_LLM_ONLY
    logger.debug("Resolved generation pipeline", extra={"pipeline_id": pipeline})
    return pipeline  # type: ignore[return-value]


async def generate_music_json(
    request: LLMMusicGenerationRequest,
    settings: LLMSettings | None = None,
) -> tuple[
    CompositionV2,
    list[str],
    LLMProviderSettings,
    GenerationValidationReport | None,
    dict[str, Any],
]:
    active_settings = settings or load_llm_settings()
    enforce_llm_generation_bounds(request)
    constraints = build_generation_constraints(request)
    # Additive soft fragment only — never mutates request.prompt / hard constraints.
    from app.reference_feature_schemas import ReferenceFeatureError
    from app.services.composer_profile_merge import (
        merge_provenance_keys,
        resolve_profile_merge,
    )
    from app.services.reference_conditioning_policy import (
        assemble_reference_conditioning,
        merge_reference_conditioning_provenance,
    )
    from app.services.reference_feature_condition import combined_reference_soft_block

    def _attach_soft_provenance(base: dict[str, Any]) -> dict[str, Any]:
        return merge_reference_conditioning_provenance(
            merge_provenance_keys(base, profile_merge),
            reference_condition,
        )

    profile_merge = resolve_profile_merge(
        profile_id=getattr(request, "profile_id", None),
        profile_strength=getattr(request, "profile_strength", "off"),
    )
    try:
        reference_condition = assemble_reference_conditioning(
            style_reference=getattr(request, "style_reference", None),
            style_references=getattr(request, "style_references", None),
            policy=getattr(request, "reference_conditioning_policy", None),
            active_project_id=getattr(request, "active_project_id", None),
            operation="generate",
            current_composition=None,
            preserve_scope=None,
        )
    except ReferenceFeatureError as exc:
        logger.warning(
            "Reference feature conditioning failed at generate",
            extra={"error_code": exc.code, "http_status": exc.http_status},
        )
        raise
    reference_soft = combined_reference_soft_block(
        masked_fragment=reference_condition.soft_fragment,
        legacy_summary=reference_condition.legacy_feature_summary,
    )
    logger.info(
        "Generate reference conditioning attached",
        extra={
            "borrow_count": len(reference_condition.applied_borrow_dimension_ids),
            "preserve_count": len(reference_condition.applied_preserve_dimension_ids),
            "regenerate_count": len(reference_condition.applied_regenerate_dimension_ids),
            "has_policy": reference_condition.policy_provenance is not None,
            "fragment_chars": len(reference_condition.soft_fragment or ""),
        },
    )
    pipeline_id = resolve_generation_pipeline(request)
    seed = request.options.seed
    composer_model_id: str | None = None
    if pipeline_id == PIPELINE_LLM_ONLY:
        provider = _select_provider(request, active_settings, operation=None)
    else:
        # Hybrid / symbolic: uncollapse planner vs composer ops.
        try:
            provider, composer_model_id = _resolve_hybrid_stage_models(request, active_settings)
        except UnsupportedLLMProviderError:
            # Continuation/variation may not need a language planner; fall back to generate
            # for any residual LLM bits while still requiring a symbolic composer below.
            if pipeline_id in {PIPELINE_CONTINUATION, PIPELINE_VARIATION}:
                provider = _select_provider(request, active_settings, operation=None)
            else:
                raise

    from .fake_llm import (
        FakeLLMError,
        generate_fake_hybrid_music_json,
        generate_fake_music_json,
        is_fake_provider,
    )
    from .symbolic_composition_generate import (
        SYMBOLIC_UNAVAILABLE,
        symbolic_composer_available,
    )

    # Refuse hybrid/symbolic when no ready composer (no silent LLM note fallback).
    if pipeline_id != PIPELINE_LLM_ONLY:
        prefer_fake = True if is_fake_provider(provider) else None
        ready, resolved_composer_id, reason = symbolic_composer_available(prefer_fake=prefer_fake)
        if composer_model_id:
            resolved_composer_id = composer_model_id
        if not ready:
            logger.error(
                "Symbolic composer unavailable for pipeline",
                extra={
                    "pipeline_id": pipeline_id,
                    "code": reason or SYMBOLIC_UNAVAILABLE,
                    "fallback_applied": False,
                    "composer_model_id": resolved_composer_id,
                },
            )
            raise HybridPipelineUnavailableError(
                "Symbolic composer is unavailable; configure Music Transformer or use LLM_FAKE_MODE",
                code=reason or SYMBOLIC_UNAVAILABLE,
            )
        composer_model_id = resolved_composer_id
        logger.info(
            "Symbolic composer available for pipeline",
            extra={
                "pipeline_id": pipeline_id,
                "composer_model_id": composer_model_id,
                "seed": seed,
                "fallback_applied": False,
            },
        )

    if is_fake_provider(provider) and pipeline_id == PIPELINE_LLM_ONLY:
        logger.info(
            "Routing music generation to fake LLM provider",
            extra={
                "provider": provider.provider,
                "model": _selected_model(request, provider),
                "duration_bars": request.prompt.duration_bars,
                "pipeline_id": pipeline_id,
            },
        )
        try:
            music, warnings, provider, validation = await generate_fake_music_json(
                request, provider, constraints=constraints
            )
            from app.services.generation_provenance import (
                attach_provenance_fragment,
                generation_config_from_request,
            )

            provenance = attach_provenance_fragment(
                {
                    "pipeline_id": pipeline_id,
                    "stages": [
                        {
                            "operation": "generate",
                            "model_id": f"{provider.provider}:{provider.model}",
                            "capability": "language_planner",
                            "runtime": "fake",
                        }
                    ],
                    "plan_schema_version": None,
                    "constraints_digest_prefix": None,
                    "seed": seed,
                },
                generation_config=generation_config_from_request(request),
            )
            return music, warnings, provider, validation, _attach_soft_provenance(
                provenance
            )
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc

    if is_fake_provider(provider) and pipeline_id == PIPELINE_HYBRID:
        logger.info(
            "Routing hybrid generation to fake planner + fake symbolic",
            extra={
                "provider": provider.provider,
                "model": _selected_model(request, provider),
                "pipeline_id": pipeline_id,
                "seed": seed,
            },
        )
        try:
            music, warnings, provider, validation, provenance = await generate_fake_hybrid_music_json(
                request,
                provider,
                constraints=constraints,
                composer_model_id=composer_model_id,
            )
            return music, warnings, provider, validation, _attach_soft_provenance(
                provenance
            )
        except FakeLLMError as exc:
            raise InvalidLLMOutputError(str(exc)) from exc
        except HybridPipelineUnavailableError:
            raise

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
            "pipeline_id": pipeline_id,
            "seed": seed,
        },
    )
    logger.debug("LLM prompt parameters", extra={"prompt": _sanitized_prompt(request)})

    retry_limit = request.options.max_retries
    state: _GenerationState = {
        "request": request,
        "provider": provider,
        "constraints": constraints,
        "pipeline_id": pipeline_id,
        "seed": seed,
        "retry_count": 0,
        "stage_retry_count": 0,
        "warnings": [],
        "validation_diagnostics": [],
        "accompaniment_drafts": [],
        "stage_raw_outputs": {},
        "current_stage": "plan_form",
        "validation_ok": False,
        "plan_ok": False,
        "symbolic_ok": False,
        "composer_model_id": composer_model_id,
        "profile_soft_fragment": profile_merge.soft_fragment,
        "profile_merge_provenance": dict(profile_merge.provenance),
        "reference_soft_fragment": reference_soft,
        "reference_legacy_summary": reference_condition.legacy_feature_summary,
        "reference_condition_provenance": {
            "binding_count": reference_condition.binding_count,
            "applied_dimension_ids": list(reference_condition.applied_dimension_ids),
            "warning_codes": list(reference_condition.warning_codes),
        },
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
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            # Local stage/assemble bugs (e.g. missing dict keys) must not look like provider outages.
            logger.error(
                "[FIX] Unexpected local generation error remapped to InvalidLLMOutputError",
                extra={
                    "error_type": type(exc).__name__,
                    "error_detail": repr(exc)[:300],
                    "stage": state.get("current_stage"),
                    "retry_count": state.get("retry_count", 0),
                },
                exc_info=True,
            )
            raise InvalidLLMOutputError(
                f"Staged generation failed locally during '{state.get('current_stage')}': "
                f"{type(exc).__name__}: {repr(exc)[:200]}"
            ) from exc
        except LLMGenerationError:
            raise
        except Exception as exc:
            logger.error(
                "LLM provider/API failure",
                extra={
                    "error_type": type(exc).__name__,
                    "error_detail": repr(exc)[:300],
                    "stage": state.get("current_stage"),
                    "retry_count": state.get("retry_count", 0),
                },
                exc_info=True,
            )
            raise LLMGenerationError(
                f"LLM provider request failed: {type(exc).__name__}: {repr(exc)[:200]}"
            ) from exc

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
                "pipeline_id": result.get("pipeline_id") or pipeline_id,
                "seed": result.get("seed", seed),
            },
        )
        from app.services.generation_provenance import generation_config_from_request

        provenance = _build_generation_provenance(
            result,
            pipeline_id=pipeline_id,
            seed=seed,
            provider=provider,
            composer_model_id=composer_model_id,
            generation_config=generation_config_from_request(request),
        )
        return music, warnings, provider, validation_report, _attach_soft_provenance(
            provenance
        )


def _build_generation_provenance(
    result: _GenerationState,
    *,
    pipeline_id: GenerationPipelineId,
    seed: int | None,
    provider: LLMProviderSettings,
    composer_model_id: str | None = None,
    generation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.generation_provenance import attach_provenance_fragment

    stages: list[dict[str, Any]] = []
    planner_id = f"{provider.provider}:{provider.model}"
    stages.append(
        {
            "operation": "generate_planner",
            "model_id": planner_id,
            "capability": "language_planner",
            "runtime": "fake" if provider.provider == "fake" else "openai_compatible_chat",
        }
    )
    symbolic = result.get("symbolic_result")
    if symbolic is not None:
        report = getattr(symbolic, "report", {}) or {}
        stages.append(
            {
                "operation": "generate_composer",
                "model_id": getattr(symbolic, "model_id", None) or composer_model_id or result.get("composer_model_id"),
                "capability": "symbolic_composer",
                "runtime": getattr(symbolic, "backend", None),
                "seed": getattr(symbolic, "seed", seed),
                "checkpoint_card_prefix": (report.get("checkpoint_basename") or "")[:32] or None,
                "tokenizer_version": report.get("tokenizer_version"),
            }
        )
    elif pipeline_id == PIPELINE_LLM_ONLY:
        stages = [
            {
                "operation": "generate",
                "model_id": planner_id,
                "capability": "language_planner",
                "runtime": "fake" if provider.provider == "fake" else "openai_compatible_chat",
            }
        ]
    plan = result.get("composition_plan")
    digest = getattr(plan, "constraints_digest", None) if plan is not None else None
    base = {
        "pipeline_id": pipeline_id,
        "stages": stages,
        "plan_schema_version": getattr(plan, "schema_version", None) if plan is not None else None,
        "constraints_digest_prefix": (digest or "")[:20] or None,
        "seed": seed,
    }
    return attach_provenance_fragment(base, generation_config=generation_config)


def select_llm_provider(
    *,
    provider: str | None,
    model: str | None,
    settings: LLMSettings,
    operation: "AiOperation | None" = None,
    model_id: str | None = None,
    collapse_reserved_generate: bool = True,
) -> LLMProviderSettings:
    """Resolve a configured provider/model pair shared by generate/edit/arrangement.

    Deprecated thin wrapper over ``resolve_provider_for_operation`` — prefer calling
    the AI runtime resolver with an explicit ``AiOperation``.
    """
    from app.ai_runtime.errors import (
        CapabilityMismatchError,
        FallbackNotConfiguredError,
        ModelNotFoundError,
        ModelUnavailableError,
    )
    from app.ai_runtime.operations import AiOperation
    from app.ai_runtime.routing import ModelSelectionInput, resolve_provider_for_operation

    op = operation or AiOperation.GENERATE
    try:
        provider_settings, resolved = resolve_provider_for_operation(
            op,
            ModelSelectionInput(model_id=model_id, provider=provider, model=model),
            settings,
            collapse_reserved_generate=collapse_reserved_generate,
        )
        logger.info(
            "select_llm_provider resolved via AI runtime",
            extra={
                "operation": str(op),
                "model_id": resolved.resolved_model_id,
                "runtime": resolved.descriptor.runtime,
                "primary_capability": resolved.descriptor.primary_capability,
                "fallback_applied": resolved.fallback_applied,
                "collapse_reserved_generate": collapse_reserved_generate,
            },
        )
        return provider_settings
    except (ModelUnavailableError, FallbackNotConfiguredError) as exc:
        if not settings.providers:
            logger.warning("LLM generation requested without configured providers")
            raise NoLLMProviderConfiguredError("No LLM providers are configured") from exc
        raise NoLLMProviderConfiguredError(str(exc)) from exc
    except (ModelNotFoundError, CapabilityMismatchError) as exc:
        if not settings.providers:
            logger.warning("LLM generation requested without configured providers")
            raise NoLLMProviderConfiguredError("No LLM providers are configured") from exc
        logger.warning(
            "Unsupported LLM provider/model requested",
            extra={"provider": provider, "model": model, "model_id": model_id, "error_code": exc.code},
        )
        raise UnsupportedLLMProviderError(str(exc)) from exc


def _select_provider(
    request: LLMMusicGenerationRequest,
    settings: LLMSettings,
    *,
    operation: "AiOperation | None" = None,
    collapse_reserved_generate: bool = True,
) -> LLMProviderSettings:
    from app.ai_runtime.operations import AiOperation

    return select_llm_provider(
        provider=request.selection.provider,
        model=request.selection.model,
        model_id=getattr(request.selection, "model_id", None),
        settings=settings,
        operation=operation or AiOperation.GENERATE,
        collapse_reserved_generate=collapse_reserved_generate,
    )


def _resolve_hybrid_stage_models(
    request: LLMMusicGenerationRequest,
    settings: LLMSettings,
) -> tuple[LLMProviderSettings, str | None]:
    """Resolve hybrid planner (language) and composer (symbolic) without collapsing ops."""
    from app.ai_runtime.errors import (
        CapabilityMismatchError,
        ModelNotFoundError,
        ModelUnavailableError,
    )
    from app.ai_runtime.operations import AiOperation
    from app.ai_runtime.routing import ModelSelectionInput, resolve_model_for_operation

    provider = _select_provider(
        request,
        settings,
        operation=AiOperation.GENERATE_PLANNER,
        collapse_reserved_generate=False,
    )
    composer_choice = (request.options.composer_model_id or "").strip()
    composer_selection = (
        ModelSelectionInput(model_id=composer_choice)
        if composer_choice
        else ModelSelectionInput()
    )
    # Composer selection must not inherit the language model from the request.
    composer_model_id: str | None = None
    try:
        composer = resolve_model_for_operation(
            AiOperation.GENERATE_COMPOSER,
            composer_selection,
            collapse_reserved_generate=False,
        )
        composer_model_id = composer.resolved_model_id
        logger.info(
            "Hybrid stage models resolved",
            extra={
                "planner_provider": provider.provider,
                "planner_model": provider.model,
                "composer_model_id": composer_model_id,
                "composer_capability": str(composer.descriptor.primary_capability),
                "composer_runtime": composer.descriptor.runtime,
                "collapse_reserved_generate": False,
                "fallback_applied": composer.fallback_applied,
            },
        )
    except (ModelUnavailableError, ModelNotFoundError, CapabilityMismatchError) as exc:
        logger.warning(
            "Hybrid composer resolve deferred to availability check",
            extra={
                "error_type": type(exc).__name__,
                "error_code": getattr(exc, "code", None),
                "collapse_reserved_generate": False,
            },
        )
    return provider, composer_model_id


def _build_generation_graph():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise LLMGenerationError("LangGraph dependencies are not installed") from exc

    logger.info(
        "Building staged LLM composition generation graph",
        extra={
            "stages": list(COMPOSER_STAGES),
            "hybrid_stages": list(HYBRID_COMPOSER_STAGES),
        },
    )
    workflow = StateGraph(_GenerationState)
    workflow.add_node("plan_form", _plan_form)
    workflow.add_node("plan_harmony", _plan_harmony)
    workflow.add_node("plan_themes", _plan_themes)
    workflow.add_node("compose_melody", _compose_melody)
    workflow.add_node("compose_bass", _compose_bass)
    workflow.add_node("compose_accompaniment", _compose_accompaniment)
    workflow.add_node("realize_themes", _realize_themes)
    workflow.add_node("assemble_composition", _assemble_composition)
    workflow.add_node("normalize_composition", _normalize_composition)
    workflow.add_node("validate_composition", _validate_composition)
    workflow.add_node("repair_composition", _repair_composition)
    # Hybrid / symbolic nodes (Task 4–5).
    workflow.add_node("validate_plan", _validate_plan)
    workflow.add_node("repair_plan", _repair_plan)
    workflow.add_node("symbolic_condition", _symbolic_condition)
    workflow.add_node("symbolic_generate", _symbolic_generate)
    workflow.add_node("symbolic_decode_repair", _symbolic_decode_repair)
    workflow.add_node("assemble_from_symbolic", _assemble_from_symbolic)
    workflow.add_node("load_prefix", _load_prefix)
    workflow.add_node("select_pipeline_entry", _select_pipeline_entry)

    workflow.set_entry_point("select_pipeline_entry")
    workflow.add_conditional_edges(
        "select_pipeline_entry",
        _route_pipeline_entry,
        {
            "plan": "plan_form",
            "prefix": "load_prefix",
        },
    )
    workflow.add_edge("plan_form", "plan_harmony")
    workflow.add_edge("plan_harmony", "plan_themes")
    workflow.add_conditional_edges(
        "plan_themes",
        _route_after_plan_themes,
        {
            "llm_compose": "compose_melody",
            "hybrid": "validate_plan",
            "symbolic_prefix": "load_prefix",
        },
    )
    workflow.add_edge("compose_melody", "compose_bass")
    workflow.add_edge("compose_bass", "compose_accompaniment")
    workflow.add_edge("compose_accompaniment", "realize_themes")
    workflow.add_edge("realize_themes", "assemble_composition")
    workflow.add_edge("assemble_composition", "normalize_composition")
    workflow.add_edge("normalize_composition", "validate_composition")
    workflow.add_conditional_edges(
        "validate_plan",
        _route_after_validate_plan,
        {
            "symbolic": "symbolic_condition",
            "repair_plan": "repair_plan",
            "fail": END,
        },
    )
    workflow.add_conditional_edges(
        "repair_plan",
        _route_after_repair_plan,
        {
            "validate_plan": "validate_plan",
            "fail": END,
        },
    )
    workflow.add_edge("symbolic_condition", "symbolic_generate")
    workflow.add_edge("symbolic_generate", "symbolic_decode_repair")
    workflow.add_edge("symbolic_decode_repair", "assemble_from_symbolic")
    workflow.add_edge("assemble_from_symbolic", "normalize_composition")
    workflow.add_edge("load_prefix", "symbolic_condition")
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
            "plan_themes": "plan_themes",
            "compose_melody": "compose_melody",
            "compose_bass": "compose_bass",
            "compose_accompaniment": "compose_accompaniment",
            "realize_themes": "realize_themes",
            "assemble_composition": "assemble_composition",
            "normalize_composition": "normalize_composition",
            "validate_composition": "validate_composition",
            "validate_plan": "validate_plan",
            "symbolic_generate": "symbolic_generate",
            "fail": END,
        },
    )
    return workflow.compile()


def _select_pipeline_entry(state: _GenerationState) -> _GenerationState:
    pipeline = state.get("pipeline_id") or PIPELINE_LLM_ONLY
    logger.debug(
        "Generation pipeline entry selected",
        extra={"pipeline_id": pipeline},
    )
    return state


def _route_pipeline_entry(state: _GenerationState) -> str:
    pipeline = state.get("pipeline_id") or PIPELINE_LLM_ONLY
    if pipeline in {PIPELINE_CONTINUATION, PIPELINE_VARIATION}:
        decision = "prefix"
    else:
        decision = "plan"
    logger.debug(
        "Routing pipeline entry",
        extra={"pipeline_id": pipeline, "decision": decision},
    )
    return decision


def _route_after_plan_themes(state: _GenerationState) -> str:
    pipeline = state.get("pipeline_id") or PIPELINE_LLM_ONLY
    if pipeline == PIPELINE_HYBRID:
        decision = "hybrid"
    elif pipeline in {PIPELINE_CONTINUATION, PIPELINE_VARIATION}:
        decision = "symbolic_prefix"
    else:
        decision = "llm_compose"
    logger.debug(
        "Routing after plan_themes",
        extra={"pipeline_id": pipeline, "decision": decision},
    )
    return decision


def _route_after_validate_plan(state: _GenerationState) -> str:
    if state.get("plan_ok"):
        return "symbolic"
    retry_limit = state["request"].options.max_retries
    if int(state.get("stage_retry_count", 0)) >= retry_limit:
        logger.error(
            "Plan repair budget exhausted",
            extra={
                "pipeline_id": state.get("pipeline_id"),
                "stage_retry_count": state.get("stage_retry_count", 0),
            },
        )
        return "fail"
    return "repair_plan"


def _route_after_repair_plan(state: _GenerationState) -> str:
    if state.get("repair_target") == "fail":
        return "fail"
    return "validate_plan"


def _validate_plan(state: _GenerationState) -> _GenerationState:
    """Assemble CompositionPlan from staged plan nodes and check hard constraints."""
    from ..composition_plan_schemas import (
        CompositionPlan,
        PlanDensity,
        PlanInstrumentation,
        PlanInstrumentationHint,
        PlanModulation,
    )
    from .composition_plan_constraints import attach_constraints_digest, validate_plan_against_constraints
    from .composition_theme import empty_theme_plan

    stage = "validate_plan"
    logger.info(
        "Composer stage started",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("stage_retry_count", 0)),
            "pipeline_id": state.get("pipeline_id"),
        },
    )
    form = state.get("form_plan")
    if form is None:
        diagnostic = ValidationDiagnostic(
            code="plan_invalid",
            message="validate_plan requires a form plan",
            severity="error",
            context={"stage": stage},
        )
        return {
            **state,
            "plan_ok": False,
            "current_stage": stage,
            "failed_stage": stage,
            "validation_diagnostics": [diagnostic],
        }

    hints: list[PlanInstrumentationHint] = []
    for label in form.instrumentation:
        hints.append(PlanInstrumentationHint(family=label))
    plan = CompositionPlan(
        form=form,
        harmony=state.get("harmony_plan") or ComposerHarmonyPlan(),
        motifs_themes=state.get("theme_plan") or empty_theme_plan(reason="missing_theme_plan"),
        modulation=PlanModulation(),
        instrumentation=PlanInstrumentation(hints=hints),
        density=PlanDensity(),
        stylistic_instructions=bounded_user_instructions_for_prompt(state["request"]),
    )
    constraints = state["constraints"]
    locked = attach_constraints_digest(plan, constraints)
    diagnostics = validate_plan_against_constraints(locked, constraints, stage=stage)
    errors = [item for item in diagnostics if item.severity == "error"]
    plan_ok = not errors
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("stage_retry_count", 0)),
            "pipeline_id": state.get("pipeline_id"),
            "plan_ok": plan_ok,
            "error_codes": [item.code for item in errors],
            "warning_codes": [item.code for item in diagnostics if item.severity == "warning"],
        },
    )
    return {
        **state,
        "composition_plan": locked,
        "plan_ok": plan_ok,
        "validation_diagnostics": diagnostics if errors else list(state.get("validation_diagnostics") or []),
        "current_stage": stage,
        "failed_stage": "" if plan_ok else stage,
    }


def _repair_plan(state: _GenerationState) -> _GenerationState:
    """Bounded plan repair — coerce form hard fields to constraints, rebuild plan, requeue validate."""
    stage = "repair_plan"
    attempt = int(state.get("stage_retry_count", 0)) + 1
    retry_limit = int(state["request"].options.max_retries)
    logger.warning(
        "Plan repair attempt",
        extra={
            "pipeline_id": state.get("pipeline_id"),
            "attempt": attempt,
            "repair_lane": "plan",
            "failed_stage": state.get("failed_stage"),
            "retry_limit": retry_limit,
        },
    )
    form = state.get("form_plan")
    constraints = state.get("constraints")
    updates: dict[str, Any] = {}
    if form is not None and constraints is not None:
        frozen, diagnostics = freeze_form_resolved_fields(constraints, form)
        state = {**state, "constraints": frozen}
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
        corrected = ComposerFormPlan.model_validate(corrected.model_dump())
        state = {**state, "form_plan": corrected, "composition_plan": None}
        if diagnostics or updates:
            logger.info(
                "Plan repair coerced form hard fields",
                extra={
                    "codes": [item.code for item in diagnostics],
                    "coerced_fields": list(updates.keys()),
                    "attempt": attempt,
                },
            )
    if attempt > retry_limit:
        logger.error(
            "Plan repair exhausted retries",
            extra={
                "pipeline_id": state.get("pipeline_id"),
                "attempt": attempt,
                "code": "plan_invalid",
                "repair_lane": "plan",
            },
        )
        return {
            **state,
            "stage_retry_count": attempt,
            "current_stage": stage,
            "repair_target": "fail",
            "plan_ok": False,
            "failed_stage": stage,
        }
    return {
        **state,
        "stage_retry_count": attempt,
        "current_stage": stage,
        "repair_target": "validate_plan",
        "plan_ok": False,
    }


def _symbolic_condition(state: _GenerationState) -> _GenerationState:
    stage = "symbolic_condition"
    plan = state.get("composition_plan")
    if plan is None and state.get("pipeline_id") == PIPELINE_HYBRID:
        logger.error("symbolic_condition missing composition_plan", extra={"pipeline_id": state.get("pipeline_id")})
        raise HybridPipelineUnavailableError(
            "symbolic_condition requires a validated CompositionPlan",
            code="plan_invalid",
        )
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "pipeline_id": state.get("pipeline_id"),
            "seed": state.get("seed"),
            "has_plan": plan is not None,
            "has_prefix": state.get("prefix_composition") is not None,
        },
    )
    return {**state, "current_stage": stage}


def _plugin_composer_model_id(stored_id: str | None) -> str | None:
    """Pass a stored composer id through when its runtime is plugin or personal."""
    if not stored_id:
        return None
    from app.ai_runtime.errors import ModelNotFoundError
    from app.ai_runtime.registry import get_model

    try:
        descriptor = get_model(stored_id)
    except ModelNotFoundError:
        logger.debug("stored composer model id is not registered", extra={"model_id": stored_id})
        return None
    if descriptor.runtime not in {"plugin", "personal_composer"}:
        return None
    logger.info(
        "Forwarding stored composer model",
        extra={"composer_model_id": stored_id, "engine": descriptor.runtime},
    )
    return stored_id


def _symbolic_generate(state: _GenerationState) -> _GenerationState:
    from .symbolic_composition_generate import (
        SymbolicCompositionGenerateError,
        generate_symbolic_composition,
    )

    stage = "symbolic_generate"
    plan = state.get("composition_plan")
    if plan is None:
        raise HybridPipelineUnavailableError(
            "symbolic_generate requires composition_plan",
            code="plan_invalid",
        )
    attempt = int(state.get("symbolic_resample_count", 0))
    plugin_model_id = _plugin_composer_model_id(state.get("composer_model_id"))
    logger.info(
        "Composer stage started",
        extra={
            **_stage_log_extra(state, stage, attempt=attempt),
            "pipeline_id": state.get("pipeline_id"),
            "seed": state.get("seed"),
            "repair_lane": "tokens" if attempt else None,
            "plugin_model_id": plugin_model_id,
        },
    )
    try:
        result = generate_symbolic_composition(
            plan,
            seed=state.get("seed"),
            prefix_composition=state.get("prefix_composition"),
            genre=state["constraints"].genre,
            mood=state["constraints"].mood,
            prefer_fake=None,
            resample_attempt=attempt,
            model_id=plugin_model_id,
        )
    except SymbolicCompositionGenerateError as exc:
        logger.error(
            "Symbolic generate failed",
            extra={
                "pipeline_id": state.get("pipeline_id"),
                "code": exc.code,
                "seed": state.get("seed"),
                "attempt": attempt,
            },
        )
        return {
            **state,
            "symbolic_ok": False,
            "current_stage": stage,
            "failed_stage": stage,
            "validation_diagnostics": [
                ValidationDiagnostic(
                    code=exc.code,
                    message=str(exc)[:400],
                    severity="error",
                    context={"stage": stage, "attempt": attempt},
                )
            ],
        }
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=attempt),
            "pipeline_id": state.get("pipeline_id"),
            "model_id": result.model_id,
            "backend": result.backend,
            "seed": result.seed,
            "note_count": sum(len(t.events) for t in result.composition.tracks),
            "fallback_applied": False,
        },
    )
    return {
        **state,
        "symbolic_result": result,
        "symbolic_ok": True,
        "music": result.composition,
        "current_stage": stage,
        "failed_stage": "",
    }


def _symbolic_decode_repair(state: _GenerationState) -> _GenerationState:
    """Token/decode repair lane — re-sample once on symbolic failure."""
    stage = "symbolic_decode_repair"
    if state.get("symbolic_ok"):
        logger.info(
            "symbolic_decode_repair skipped; symbolic_ok",
            extra={"pipeline_id": state.get("pipeline_id"), "seed": state.get("seed")},
        )
        return {**state, "current_stage": stage}

    attempt = int(state.get("symbolic_resample_count", 0))
    max_attempts = int(os.environ.get("GENERATION_HYBRID_MAX_TOKEN_RETRIES", "1") or "1")
    if attempt >= max_attempts:
        logger.error(
            "Symbolic token repair budget exhausted",
            extra={
                "pipeline_id": state.get("pipeline_id"),
                "attempt": attempt,
                "max_attempts": max_attempts,
                "repair_lane": "tokens",
            },
        )
        return {**state, "current_stage": stage, "failed_stage": "symbolic_generate"}

    logger.warning(
        "Re-sampling symbolic generate after decode/token failure",
        extra={
            "pipeline_id": state.get("pipeline_id"),
            "attempt": attempt + 1,
            "seed": state.get("seed"),
            "repair_lane": "tokens",
        },
    )
    resampled = _symbolic_generate({**state, "symbolic_resample_count": attempt + 1})
    return {**resampled, "current_stage": stage}


def _assemble_from_symbolic(state: _GenerationState) -> _GenerationState:
    stage = "assemble_from_symbolic"
    music = state.get("music")
    if music is None or not state.get("symbolic_ok"):
        logger.error(
            "assemble_from_symbolic missing symbolic music",
            extra={"pipeline_id": state.get("pipeline_id"), "symbolic_ok": state.get("symbolic_ok")},
        )
        return {
            **state,
            "validation_ok": False,
            "failed_stage": "symbolic_generate",
            "current_stage": stage,
        }
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "pipeline_id": state.get("pipeline_id"),
            "track_count": len(music.tracks),
            "event_count": sum(len(track.events) for track in music.tracks),
            "schema_version": getattr(music, "schema_version", None),
        },
    )
    hard_summary = {
        "key": music.key,
        "time_signature": music.time_signature,
        "bar_count": music.bar_count,
        "tempo": music.tempo,
        "duration_ticks": music.duration_ticks,
        "section_types": [section.type for section in music.sections],
        "section_bars": [section.bar_count for section in music.sections],
    }
    return {
        **state,
        "music": music,
        "current_stage": stage,
        "failed_stage": "",
        "pre_normalize_hard_summary": hard_summary,
    }


def _load_prefix(state: _GenerationState) -> _GenerationState:
    """Load prefix composition for continuation/variation (from request options when present)."""
    stage = "load_prefix"
    prefix = state.get("prefix_composition")
    options = state["request"].options
    raw_prefix = getattr(options, "prefix_composition", None)
    if prefix is None and raw_prefix is not None:
        prefix = CompositionV2.model_validate(raw_prefix) if not isinstance(raw_prefix, CompositionV2) else raw_prefix
    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=0),
            "pipeline_id": state.get("pipeline_id"),
            "has_prefix": prefix is not None,
            "prefix_bar_count": getattr(prefix, "bar_count", None) if prefix else None,
            "seed": state.get("seed"),
        },
    )
    if prefix is None:
        raise HybridPipelineUnavailableError(
            "symbolic_continuation/variation requires options.prefix_composition",
            code="symbolic_prefix_missing",
        )
    # Light plan fragment from prefix metadata when composition_plan absent.
    from ..composition_plan_schemas import CompositionPlan, PlanDensity, PlanInstrumentation, PlanModulation
    from .composition_planner import ComposerFormPlan, ComposerFormSection
    from .composition_plan_constraints import attach_constraints_digest
    from .composition_theme import empty_theme_plan

    sections = [
        ComposerFormSection(
            type=section.type,
            start_bar=section.start_bar,
            bar_count=section.bar_count,
        )
        for section in prefix.sections
    ]
    form = ComposerFormPlan(
        tempo=prefix.tempo,
        key=prefix.key,
        time_signature=prefix.time_signature,
        bar_count=prefix.bar_count,
        sections=sections,
        instrumentation=[track.instrument for track in prefix.tracks],
    )
    plan = CompositionPlan(
        form=form,
        harmony=ComposerHarmonyPlan(),
        motifs_themes=empty_theme_plan(reason="prefix_pipeline"),
        modulation=PlanModulation(),
        instrumentation=PlanInstrumentation(),
        density=PlanDensity(),
    )
    locked = attach_constraints_digest(plan, state["constraints"])
    # Align hard duration/meter/key locks with the prefix score for continuation/variation.
    from dataclasses import replace as dc_replace

    constraints = state["constraints"]
    updates: dict[str, Any] = {
        "duration_bars": prefix.bar_count,
        "time_signature": prefix.time_signature,
    }
    if not constraints.key_user_specified:
        updates["key"] = prefix.key
    aligned = dc_replace(constraints, **updates)
    locked = attach_constraints_digest(plan, aligned)
    return {
        **state,
        "prefix_composition": prefix,
        "composition_plan": locked,
        "form_plan": form,
        "constraints": aligned,
        "plan_ok": True,
        "current_stage": stage,
    }


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
                "pipeline_id": state.get("pipeline_id"),
                "diagnostic_codes": [
                    item.code for item in (state.get("validation_diagnostics") or []) if item.severity == "error"
                ],
            },
        )
        return "fail"
    # Hybrid default: re-sample symbolic rather than LLM note rewrite.
    pipeline = state.get("pipeline_id") or PIPELINE_LLM_ONLY
    if pipeline in {PIPELINE_HYBRID, PIPELINE_CONTINUATION, PIPELINE_VARIATION}:
        allow_llm_repair = bool(
            getattr(state["request"].options, "allow_llm_composition_repair", False)
        ) if hasattr(state["request"].options, "allow_llm_composition_repair") else False
        if not allow_llm_repair:
            logger.info(
                "Hybrid validation failure routing to symbolic re-sample",
                extra={
                    "pipeline_id": pipeline,
                    "retry_count": state.get("retry_count", 0),
                    "repair_lane": "symbolic_resample",
                },
            )
            return "repair"
    return "repair"


def _route_after_repair(state: _GenerationState) -> str:
    target = state.get("repair_target") or "assemble_composition"
    if target == "fail":
        return "fail"
    if target in {
        "plan_form",
        "plan_harmony",
        "plan_themes",
        "compose_melody",
        "compose_bass",
        "compose_accompaniment",
        "realize_themes",
        "assemble_composition",
        "normalize_composition",
        "validate_composition",
        "validate_plan",
        "symbolic_generate",
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


async def _plan_themes(state: _GenerationState) -> _GenerationState:
    return await _run_json_stage(
        state,
        stage="plan_themes",
        prompt=_build_theme_prompt(state),
        parser=_parse_theme_plan,
        on_success=_after_theme_plan,
    )


async def _compose_melody(state: _GenerationState) -> _GenerationState:
    theme = state.get("theme_plan")
    if theme is not None and theme.enabled and theme.seed is not None:
        seed_state = await _run_json_stage(
            state,
            stage="compose_melody",
            prompt=_build_melody_seed_prompt(state),
            parser=_parse_melody_seed_stage,
            on_success=_after_melody_seed_stage,
        )
        return await _run_json_stage(
            seed_state,
            stage="compose_melody",
            prompt=_build_melody_continuation_prompt(seed_state),
            parser=_parse_melody_continuation_stage,
            on_success=_after_melody_continuation_stage,
        )
    return await _run_json_stage(
        state,
        stage="compose_melody",
        prompt=_build_melody_prompt(state),
        parser=_parse_melody_stage,
        on_success=_after_melody_stage,
    )


def _realize_themes(state: _GenerationState) -> _GenerationState:
    stage = "realize_themes"
    logger.info(
        "Composer stage started",
        extra=_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
    )
    form = state.get("form_plan")
    melody = state.get("melody_draft")
    theme = state.get("theme_plan") or empty_theme_plan(reason="missing_theme_plan")
    if form is None or melody is None:
        diagnostic = ValidationDiagnostic(
            code=THEME_SOURCE_EMPTY,
            message="Cannot realize themes without form and melody drafts",
            severity="error",
            context={"stage": stage},
        )
        return {
            **state,
            "validation_ok": False,
            "validation_diagnostics": [diagnostic],
            "failed_stage": stage,
            "current_stage": stage,
            "theme_motifs": [],
            "theme_outcomes": [],
        }

    result = realize_theme_plan(
        theme_plan=theme,
        melody_draft=melody,
        form=form,
        ticks_per_quarter=DEFAULT_TICKS_PER_QUARTER,
    )
    error_diagnostics = [item for item in result.diagnostics if item.severity == "error"]
    if error_diagnostics:
        # Route plan failures to plan_themes; creative identity to melody; else realize_themes.
        codes = {item.code for item in error_diagnostics}
        if codes & {THEME_SOURCE_EMPTY, THEME_TARGET_MISSING, THEME_TARGET_OUT_OF_BOUNDS}:
            failed_stage = "plan_themes"
        elif THEME_IDENTITY_BELOW_THRESHOLD in codes:
            failed_stage = "compose_melody"
        else:
            failed_stage = "realize_themes"
        logger.warning(
            "Theme realization produced diagnostics",
            extra={
                "codes": sorted(codes),
                "failed_stage": failed_stage,
                "outcome_count": len(result.outcomes),
            },
        )
        return {
            **state,
            "melody_draft": result.melody_draft,
            "theme_plan": result.theme_plan,
            "theme_motifs": list(result.motifs),
            "theme_outcomes": thematic_report_payload(result.outcomes),
            "validation_ok": False,
            "validation_diagnostics": list(result.diagnostics),
            "failed_stage": failed_stage,
            "current_stage": stage,
        }

    logger.info(
        "Composer stage completed",
        extra={
            **_stage_log_extra(state, stage, attempt=state.get("retry_count", 0)),
            "realized_count": sum(
                1 for item in result.outcomes if item.status in {"realized", "verified"}
            ),
            "motif_count": len(result.motifs),
        },
    )
    # Drop resolved theme errors so validate cannot resurrect theme_target_missing
    # from an earlier failed attempt after a clean re-realize.
    remaining_diagnostics = [
        item
        for item in (state.get("validation_diagnostics") or [])
        if item.code not in THEME_DIAGNOSTIC_CODES
    ]
    return {
        **state,
        "melody_draft": result.melody_draft,
        "theme_plan": result.theme_plan,
        "theme_motifs": list(result.motifs),
        "theme_outcomes": thematic_report_payload(result.outcomes),
        "validation_diagnostics": remaining_diagnostics,
        "current_stage": stage,
        "failed_stage": "",
    }


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

        in_range_events = [event for event in harmony.events if 1 <= int(event.bar) <= locked_bars]
        dropped_harmony = len(harmony.events) - len(in_range_events)
        if dropped_harmony:
            logger.warning(
                "Dropped out-of-range harmony bars during assemble",
                extra={
                    "code": "harmony_legacy_bar_out_of_range",
                    "dropped_count": dropped_harmony,
                    "bar_count": locked_bars,
                },
            )
        harmony_items = [{"bar": event.bar, "chord": event.chord} for event in in_range_events]
        theme_motifs = list(state.get("theme_motifs") or [])
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
            motifs=theme_motifs,
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
        and item.code
        in {
            "constraint_normalization_rewrite",
            "normalization_failed",
            "schema_invalid",
            *THEME_DIAGNOSTIC_CODES,
        }
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

    theme_outcomes_raw = list(state.get("theme_outcomes") or [])
    if constraint_report is not None and theme_outcomes_raw:
        thematic = [
            ThematicRecurrenceOutcome.model_validate(item)
            if not isinstance(item, ThematicRecurrenceOutcome)
            else item
            for item in theme_outcomes_raw
        ]
        constraint_report = constraint_report.model_copy(update={"thematic": thematic})
        logger.debug(
            "Attached thematic outcomes to validation report",
            extra={
                "thematic_count": len(thematic),
                "statuses": [item.status for item in thematic],
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
    pipeline = state.get("pipeline_id") or PIPELINE_LLM_ONLY
    # Hybrid composition repair prefers symbolic re-sample (not LLM note rewrite).
    if pipeline in {PIPELINE_HYBRID, PIPELINE_CONTINUATION, PIPELINE_VARIATION}:
        allow_llm = bool(getattr(state["request"].options, "allow_llm_composition_repair", False))
        if not allow_llm:
            repair_target = "symbolic_generate"
            logger.info(
                "Hybrid composition repair selecting symbolic re-sample",
                extra={
                    "pipeline_id": pipeline,
                    "repair_lane": "composition_via_symbolic",
                    "retry_count": retry_count,
                    "codes": codes,
                },
            )
    if repair_target not in {
        "plan_form",
        "plan_harmony",
        "plan_themes",
        "compose_melody",
        "compose_bass",
        "compose_accompaniment",
        "realize_themes",
        "assemble_composition",
        "normalize_composition",
        "validate_composition",
        "validate_plan",
        "symbolic_generate",
    }:
        repair_target = "assemble_composition"

    # Capture advisory analysis from the post-assembly candidate before clearing music.
    repair_analysis_context = _safe_repair_analysis_context(state.get("music"))

    # Preserve valid upstream drafts when repairing a later stage.
    cleared: _GenerationState = {**state}
    if repair_target == "plan_form":
        cleared.pop("form_plan", None)
        cleared.pop("harmony_plan", None)
        cleared.pop("theme_plan", None)
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("theme_motifs", None)
        cleared.pop("theme_outcomes", None)
        cleared.pop("music", None)
    elif repair_target == "plan_harmony":
        cleared.pop("harmony_plan", None)
        cleared.pop("theme_plan", None)
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("theme_motifs", None)
        cleared.pop("theme_outcomes", None)
        cleared.pop("music", None)
    elif repair_target == "plan_themes":
        cleared.pop("theme_plan", None)
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("theme_motifs", None)
        cleared.pop("theme_outcomes", None)
        cleared.pop("music", None)
    elif repair_target == "compose_melody":
        # Preserve theme_plan; regenerate only melody and dependents.
        cleared.pop("melody_draft", None)
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("theme_motifs", None)
        cleared.pop("theme_outcomes", None)
        cleared.pop("music", None)
    elif repair_target == "compose_bass":
        cleared.pop("bass_draft", None)
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("music", None)
    elif repair_target == "compose_accompaniment":
        cleared.pop("accompaniment_drafts", None)
        cleared.pop("music", None)
    elif repair_target == "realize_themes":
        cleared.pop("theme_motifs", None)
        cleared.pop("theme_outcomes", None)
        cleared.pop("music", None)
    elif repair_target == "symbolic_generate":
        cleared.pop("music", None)
        cleared["symbolic_ok"] = False
        cleared["symbolic_resample_count"] = int(state.get("symbolic_resample_count", 0)) + 1
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
        # Keep the failing diagnostics for the repair-stage prompt; successful
        # realize_themes / stage completion must strip resolved codes so validate
        # does not resurrect them via the prior-theme merge.
        "validation_diagnostics": list(diagnostics),
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
        updated = on_success(state, parsed_model, parsed)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logger.warning(
            "Composer stage validation failed",
            extra={
                **_stage_log_extra(state, stage, attempt=attempt),
                "error_type": type(exc).__name__,
                "error_detail": repr(exc)[:300],
            },
        )
        raise InvalidLLMOutputError(
            f"LLM stage '{stage}' returned invalid JSON: {type(exc).__name__}: {repr(exc)[:200]}"
        ) from exc

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
    from app.ai_runtime.invoke_text import ainvoke_text_for_resolved
    from app.ai_runtime.routing import get_current_resolved_model
    from .llm_chat_client import ainvoke_chat_text, build_chat_openai

    request = state["request"]
    provider = state["provider"]

    timeout_seconds = request.options.timeout_seconds or load_llm_settings().request_timeout_seconds
    temperature = request.options.temperature
    if temperature is None:
        temperature = load_llm_settings().temperature

    model_name = _selected_model(request, provider)
    resolved = get_current_resolved_model()
    if resolved is not None and resolved.descriptor.runtime == "execution_node":
        logger.debug(
            "Calling execution-node invoke seam",
            extra={
                "provider": provider.provider,
                "model": resolved.resolved_model_id,
                "stage": state.get("current_stage"),
                "timeout_seconds": timeout_seconds,
                "prompt_length": len(prompt),
            },
        )
        try:
            return await ainvoke_text_for_resolved(
                resolved,
                prompt,
                purpose="music_generation",
                provider=provider,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            logger.error(
                "[FIX] Execution-node provider call failed",
                extra={
                    "provider": provider.provider,
                    "model": resolved.resolved_model_id,
                    "stage": state.get("current_stage"),
                    "retry_count": state.get("retry_count", 0),
                    "timeout_seconds": timeout_seconds,
                    "error_type": type(exc).__name__,
                    "error_detail": str(exc)[:200],
                },
            )
            raise LLMGenerationError(
                f"LLM provider request failed during stage '{state.get('current_stage')}': "
                f"{type(exc).__name__} (timeout_seconds={timeout_seconds})"
            ) from exc

    try:
        client = build_chat_openai(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=model_name,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            purpose="music_generation",
        )
    except ImportError as exc:
        raise LLMGenerationError("LangChain OpenAI dependencies are not installed") from exc
    logger.debug(
        "Calling LLM provider",
        extra={
            "provider": provider.provider,
            "model": model_name,
            "stage": state.get("current_stage"),
            "timeout_seconds": timeout_seconds,
            "prompt_length": len(prompt),
        },
    )
    try:
        return await ainvoke_chat_text(client, prompt, purpose="music_generation")
    except Exception as exc:
        logger.error(
            "[FIX] LLM provider call failed",
            extra={
                "provider": provider.provider,
                "model": model_name,
                "stage": state.get("current_stage"),
                "retry_count": state.get("retry_count", 0),
                "timeout_seconds": timeout_seconds,
                "error_type": type(exc).__name__,
                "error_detail": str(exc)[:200],
            },
        )
        raise LLMGenerationError(
            f"LLM provider request failed during stage '{state.get('current_stage')}': "
            f"{type(exc).__name__} (timeout_seconds={timeout_seconds})"
        ) from exc


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


def _parse_theme_plan(parsed: dict[str, Any], state: _GenerationState) -> ComposerThemePlan:
    if "theme_plan" in parsed and isinstance(parsed["theme_plan"], dict):
        parsed = parsed["theme_plan"]
    coerced = _coerce_theme_plan_payload(parsed, state)
    try:
        return ComposerThemePlan.model_validate(coerced)
    except ValidationError:
        if coerced.get("enabled") is False or coerced.get("no_theme"):
            return empty_theme_plan(reason=str(coerced.get("no_theme_reason") or "provider_disabled"))
        raise


def _coerce_theme_plan_payload(parsed: dict[str, Any], state: _GenerationState) -> dict[str, Any]:
    """Fill missing mechanical deployment params so incomplete LLM plans still validate."""
    del state  # reserved for future form-aware defaults
    if not isinstance(parsed, dict):
        return parsed
    deployments = parsed.get("deployments")
    if not isinstance(deployments, list) or not deployments:
        return parsed

    coerced_ops: list[str] = []
    fixed: list[Any] = []
    for item in deployments:
        if not isinstance(item, dict):
            fixed.append(item)
            continue
        deployment = dict(item)
        operation = str(deployment.get("operation") or "").strip().lower()
        params_raw = deployment.get("parameters")
        params = dict(params_raw) if isinstance(params_raw, dict) else {}

        # LLMs sometimes put mechanical knobs on the deployment root.
        for key in (
            "transpose_semitones",
            "inversion_axis_pitch",
            "time_scale_numerator",
            "time_scale_denominator",
            "sequence_steps",
            "sequence_interval_semitones",
            "sequence_step_ticks",
        ):
            if params.get(key) is None and deployment.get(key) is not None:
                params[key] = deployment.get(key)

        if operation in MECHANICAL_THEME_OPERATIONS and deployment.get("variation_strength") is not None:
            deployment["variation_strength"] = None
            coerced_ops.append(f"{operation}:strip_variation_strength")

        if operation == "transpose" and params.get("transpose_semitones") is None:
            params["transpose_semitones"] = 5
            coerced_ops.append("transpose:default_semitones")
        elif operation == "augmentation" and (
            params.get("time_scale_numerator") is None or params.get("time_scale_denominator") is None
        ):
            params["time_scale_numerator"] = 2
            params["time_scale_denominator"] = 1
            coerced_ops.append("augmentation:default_time_scale")
        elif operation == "diminution" and (
            params.get("time_scale_numerator") is None or params.get("time_scale_denominator") is None
        ):
            params["time_scale_numerator"] = 1
            params["time_scale_denominator"] = 2
            coerced_ops.append("diminution:default_time_scale")
        elif operation == "sequence" and (
            params.get("sequence_steps") is None
            or params.get("sequence_interval_semitones") is None
            or params.get("sequence_step_ticks") is None
        ):
            # Incomplete sequence params often overflow short forms when defaulted
            # aggressively; demote to repeat so plan_themes still succeeds.
            deployment["operation"] = "repeat"
            params = {
                key: value
                for key, value in params.items()
                if key
                not in {
                    "sequence_steps",
                    "sequence_interval_semitones",
                    "sequence_step_ticks",
                }
            }
            coerced_ops.append("sequence:demote_repeat")
            operation = "repeat"

        deployment["parameters"] = params
        fixed.append(deployment)

    if not coerced_ops:
        return parsed

    logger.info(
        "[FIX] Coerced incomplete theme deployment parameters",
        extra={
            "coercion_count": len(coerced_ops),
            "coercions": coerced_ops[:12],
            "deployment_count": len(fixed),
        },
    )
    return {**parsed, "deployments": fixed}


def _after_theme_plan(
    state: _GenerationState, plan: ComposerThemePlan, parsed: dict[str, Any]
) -> _GenerationState:
    form = state.get("form_plan")
    diagnostics = list(state.get("validation_diagnostics") or [])
    if form is None:
        raise InvalidLLMOutputError("plan_themes requires a resolved form plan")

    if not plan.enabled and len(form.sections) >= 2:
        if not plan.no_theme_reason or plan.no_theme_reason in {"", "none", "provider_disabled"}:
            plan = default_theme_plan_for_form(form)

    corrected, plan_diagnostics = validate_theme_plan_against_form(plan, form)
    diagnostics.extend(plan_diagnostics)
    logger.info(
        "Resolved theme plan",
        extra={
            **summarize_theme_plan(corrected),
            "instructions_length": state["constraints"].instructions_length,
        },
    )
    logger.debug(
        "Theme plan stage summary",
        extra={
            "enabled": corrected.enabled,
            "deployment_count": len(corrected.deployments),
            "truncated": corrected.truncated,
            "has_instructions": state["constraints"].has_instructions,
            "instructions_length": state["constraints"].instructions_length,
        },
    )
    return {
        **state,
        "theme_plan": corrected,
        "parsed_json": parsed,
        "validation_diagnostics": diagnostics,
    }


def _parse_melody_stage(parsed: dict[str, Any], state: _GenerationState) -> dict[str, Any]:
    track_data = parsed.get("track") or parsed
    track = ComposerTrackDraft.model_validate(track_data)
    if track.role not in {"melody", "lead"}:
        track = track.model_copy(update={"role": "melody"})
    return {"track": track}


def _after_melody_stage(state: _GenerationState, payload: dict[str, Any], parsed: dict[str, Any]) -> _GenerationState:
    track: ComposerTrackDraft = payload["track"]
    track = assign_draft_event_ids(track, id_prefix="gen-mel")
    if not track.events:
        logger.warning("Melody stage produced empty events", extra={"track_id": track.id})
    logger.info(
        "Resolved melody draft",
        extra={
            **summarize_track_draft(track),
            "covered_bars_estimate": _estimate_covered_bars(track, state.get("form_plan")),
            "theme": summarize_theme_plan(state.get("theme_plan")),
        },
    )
    return {
        **state,
        "melody_draft": track,
        "parsed_json": parsed,
    }


def _parse_melody_seed_stage(parsed: dict[str, Any], state: _GenerationState) -> dict[str, Any]:
    return _parse_melody_stage(parsed, state)


def _after_melody_seed_stage(
    state: _GenerationState, payload: dict[str, Any], parsed: dict[str, Any]
) -> _GenerationState:
    track: ComposerTrackDraft = payload["track"]
    track = assign_draft_event_ids(track, id_prefix="gen-seed")
    theme = state.get("theme_plan")
    form = state.get("form_plan")
    if theme is None or not theme.enabled or theme.seed is None or form is None:
        raise InvalidLLMOutputError("Melody seed stage requires an enabled theme plan")
    try:
        cells, seed_ids = extract_seed_relative_cell(
            track,
            form=form,
            seed=theme.seed,
            ticks_per_quarter=DEFAULT_TICKS_PER_QUARTER,
        )
    except Exception as exc:
        raise InvalidLLMOutputError(f"Theme seed extraction failed: {str(exc)[:200]}") from exc
    frozen_seed = theme.seed.model_copy(
        update={
            "relative_cell": cells,
            "seed_event_ids": seed_ids,
            "prior_section_handoff": "seed stated; continue with planned deployments",
        }
    )
    frozen_theme = theme.model_copy(update={"seed": frozen_seed})
    logger.info(
        "Resolved melody seed cell",
        extra={
            "seed_event_count": len(seed_ids),
            "relative_cell_count": len(cells),
            "seed_section_index": frozen_seed.section_index,
        },
    )
    return {
        **state,
        "melody_draft": track,
        "theme_plan": frozen_theme,
        "parsed_json": parsed,
    }


def _parse_melody_continuation_stage(parsed: dict[str, Any], state: _GenerationState) -> dict[str, Any]:
    return _parse_melody_stage(parsed, state)


def _after_melody_continuation_stage(
    state: _GenerationState, payload: dict[str, Any], parsed: dict[str, Any]
) -> _GenerationState:
    from .composition_theme import section_tick_bounds

    continuation: ComposerTrackDraft = payload["track"]
    seed_draft = state.get("melody_draft")
    theme = state.get("theme_plan")
    form = state.get("form_plan")
    if seed_draft is None or theme is None or theme.seed is None or form is None:
        raise InvalidLLMOutputError("Melody continuation requires seed draft and theme plan")

    seed_ids = set(theme.seed.seed_event_ids)
    seed_events = [event for event in seed_draft.events if event.id in seed_ids]
    start_tick, end_tick = section_tick_bounds(
        form,
        theme.seed.section_index,
        ticks_per_quarter=DEFAULT_TICKS_PER_QUARTER,
        start_bar_offset=theme.seed.start_bar_offset,
        bar_span=theme.seed.bar_span,
    )
    later_events = [
        event
        for event in continuation.events
        if event.start_tick < start_tick or event.start_tick >= end_tick
    ]
    merged = sorted(
        [*seed_events, *later_events],
        key=lambda item: (item.start_tick, item.duration_ticks, item.id or ""),
    )
    track = continuation.model_copy(update={"events": merged, "id": seed_draft.id or continuation.id})
    track = assign_draft_event_ids(track, id_prefix="gen-mel")
    try:
        cells, seed_ids_list = extract_seed_relative_cell(
            track,
            form=form,
            seed=theme.seed,
            ticks_per_quarter=DEFAULT_TICKS_PER_QUARTER,
        )
        frozen_seed = theme.seed.model_copy(
            update={"relative_cell": cells, "seed_event_ids": seed_ids_list}
        )
        frozen_theme = theme.model_copy(update={"seed": frozen_seed})
    except Exception:
        frozen_theme = theme

    logger.info(
        "Resolved melody continuation with immutable seed",
        extra={
            **summarize_track_draft(track),
            "seed_event_count": len(frozen_theme.seed.seed_event_ids) if frozen_theme.seed else 0,
            "covered_bars_estimate": _estimate_covered_bars(track, form),
        },
    )
    return {
        **state,
        "melody_draft": track,
        "theme_plan": frozen_theme,
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
    tracks_raw = payload.get("tracks")
    if not isinstance(tracks_raw, list):
        raise ValueError("Accompaniment stage payload missing tracks list")
    tracks: list[ComposerTrackDraft] = tracks_raw
    skipped = payload.get("skipped") or []
    if not isinstance(skipped, list):
        skipped = []
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
                "check_passed": not missing
                and not unexpected
                and not analysis.actionable_duplicate_groups,
                "missing_families": missing,
                "unexpected_families": unexpected,
                "actionable_duplicate_count": len(analysis.actionable_duplicate_groups),
                "already_satisfied": assignment.get("already_satisfied") or [],
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


def _profile_soft_block(state: _GenerationState) -> str:
    """Additive composer-profile soft fragment (after prompt soft lines)."""
    fragment = (state.get("profile_soft_fragment") or "").strip()
    if not fragment:
        return ""
    return f"\n{fragment}\n"


def _reference_soft_block(state: _GenerationState) -> str:
    """Additive masked/legacy reference soft fragment (after profile)."""
    fragment = (state.get("reference_soft_fragment") or "").strip()
    if not fragment:
        return ""
    return f"\n{fragment}\n"


def _build_form_prompt(state: _GenerationState) -> str:
    request = state["request"]
    prompt = request.prompt
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    sections = [section.model_dump() for section in prompt.sections]
    instructions = bounded_user_instructions_for_prompt(request)
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
    instructions_block = (
        f"- user instructions (bounded): {json.dumps(instructions, ensure_ascii=True)}"
        if instructions
        else f"- freeform instructions present: {constraints.has_instructions} (length={constraints.instructions_length})"
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
{instructions_block}
- requested sections hint: {json.dumps(sections) if sections else "design coherent structure totaling duration_bars"}
- requested instruments: {", ".join(prompt.instruments)}
{_profile_soft_block(state)}
{_reference_soft_block(state)}
""".strip()


def _build_theme_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    instructions = bounded_user_instructions_for_prompt(request)
    bar_ticks = bar_duration_ticks(form.time_signature, DEFAULT_TICKS_PER_QUARTER)
    section_summary = [
        {
            "index": index,
            "type": section.type,
            "start_bar": section.start_bar,
            "bar_count": section.bar_count,
        }
        for index, section in enumerate(form.sections)
    ]
    instructions_block = (
        f"User instructions (bounded):\n{json.dumps(instructions, ensure_ascii=True)}\n"
        if instructions
        else "User instructions: none\n"
    )
    return f"""
You are planning thematic development for composition.v2 generation.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
Form sections (0-based indexes):
{json.dumps(section_summary, ensure_ascii=True)}
{instructions_block}
Return JSON only:
{{
  "enabled": true,
  "motif_id": "motif-a",
  "motif_label": "Motif A",
  "seed": {{
    "section_index": 0,
    "track_role": "melody",
    "start_bar_offset": 0,
    "bar_span": 1
  }},
  "deployments": [
    {{
      "id": "dep-1",
      "target_section_index": 2,
      "target_track_role": "melody",
      "start_bar_offset": 0,
      "operation": "transpose",
      "parameters": {{"transpose_semitones": 5}},
      "variation_strength": null
    }},
    {{
      "id": "dep-2",
      "target_section_index": 1,
      "target_track_role": "melody",
      "start_bar_offset": 0,
      "operation": "sequence",
      "parameters": {{
        "sequence_steps": 3,
        "sequence_interval_semitones": 2,
        "sequence_step_ticks": {bar_ticks}
      }},
      "variation_strength": null
    }}
  ],
  "truncated": false,
  "no_theme_reason": null
}}

Rules:
- if the form has only one section or thematic recurrence is unsuitable, return enabled=false with no_theme_reason
- when enabled, choose a seed section and at least one later recurrence target
- mechanical operations: repeat, transpose, inversion, augmentation, diminution, sequence
- sequence REQUIRES parameters.sequence_steps, parameters.sequence_interval_semitones, and parameters.sequence_step_ticks (use one bar = {bar_ticks} ticks when unsure)
- transpose REQUIRES parameters.transpose_semitones; augmentation/diminution REQUIRE time_scale_numerator+denominator
- mechanical operations must set variation_strength to null
- creative operations: rhythmic_variation, melodic_variation, answer, counterphrase (require variation_strength 0..1)
- do not invent note events; relative cells are filled after melody seed composition
- honor user instructions about motif/theme when present (e.g. invert opening motif in the bridge)
- max {4} deployments; set truncated=true if you would exceed the bound
- soft preferences: genre={request.prompt.genre}, mood={request.prompt.mood}, complexity={request.prompt.complexity}
{_profile_soft_block(state)}
{_reference_soft_block(state)}
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
{_profile_soft_block(state)}
{_reference_soft_block(state)}
""".strip()


def _melody_min_events(bar_count: int, complexity: str) -> int:
    """Integrity density floor for melody/lead prompts (matches composition_validator)."""
    return _min_events_for_complexity(bar_count, complexity)


def _build_melody_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    locked_key = constraints.key or form.key
    theme_ctx = theme_plan_prompt_projection(state.get("theme_plan"))
    min_melody_events = _melody_min_events(form.bar_count, request.prompt.complexity)
    return f"""
You are composing the primary melody track in canonical tick timing.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony events:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}
Theme plan (compact):
{json.dumps(theme_ctx, ensure_ascii=True)}

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
  }}
}}

Rules:
- ticks_per_quarter={ticks}; one bar = {bar_ticks} ticks for {form.time_signature}
- events must stay within 0..{form.bar_count * bar_ticks - 1} start and not exceed composition end
- MUST include at least {min_melody_events} melody note events across the full {form.bar_count}-bar form (integrity rejects fewer)
- cover every section with playable notes; do not leave long empty stretches for later theme realization
- when a theme plan is enabled, state the seed clearly in the seed section (at least 3 notes in the seed bar span)
- melody pitch range commonly C4-C6 unless instrument requires otherwise
- tonal center MUST remain {locked_key}; chromatic passing tones are allowed
- instrument MUST use a requested family from {list(constraints.required_instrument_families)}: {", ".join(request.prompt.instruments)}
- complexity={request.prompt.complexity}
""".strip()


def _build_melody_seed_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    theme = state["theme_plan"]
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    locked_key = constraints.key or form.key
    seed = theme.seed
    assert seed is not None
    section = form.sections[seed.section_index]
    seed_start_bar = section.start_bar + seed.start_bar_offset
    seed_end_bar = seed_start_bar + seed.bar_span - 1
    return f"""
You are composing ONLY the theme seed for the primary melody track.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony events:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}
Seed placement: section_index={seed.section_index} ({section.type}), bars {seed_start_bar}..{seed_end_bar}

Return JSON only with a melody track whose events cover the seed bars (minimum 3 notes, maximum 32):
{{
  "track": {{
    "id": "melody-1",
    "name": "Melody",
    "instrument": "piano",
    "role": "melody",
    "staff": "treble",
    "events": [
      {{"pitch": "A4", "start_tick": {(seed_start_bar - 1) * bar_ticks}, "duration_ticks": {ticks}, "velocity": 78, "staff": "treble"}}
    ]
  }}
}}

Rules:
- compose the seed first; do not write later-section material yet
- ticks_per_quarter={ticks}; one bar = {bar_ticks} ticks
- keep events inside the seed bar span
- tonal center MUST remain {locked_key}
- instrument from {list(constraints.required_instrument_families)}
""".strip()


def _build_melody_continuation_prompt(state: _GenerationState) -> str:
    form = state["form_plan"]
    harmony = state["harmony_plan"]
    theme = state["theme_plan"]
    request = state["request"]
    constraints = state["constraints"]
    hard_block = prompt_parameters_hard_block(constraints)
    ticks = DEFAULT_TICKS_PER_QUARTER
    bar_ticks = bar_duration_ticks(form.time_signature, ticks)
    locked_key = constraints.key or form.key
    theme_ctx = theme_plan_prompt_projection(theme)
    min_melody_events = _melody_min_events(form.bar_count, request.prompt.complexity)
    return f"""
You are composing the remaining melody sections AFTER an immutable theme seed.
IMMUTABLE HARD CONSTRAINTS:
{json.dumps(hard_block["hard"], ensure_ascii=True)}
Form:
{json.dumps(form.model_dump(), ensure_ascii=True)}
Harmony events:
{json.dumps([event.model_dump() for event in harmony.events[:48]], ensure_ascii=True)}
Immutable theme seed / plan:
{json.dumps(theme_ctx, ensure_ascii=True)}

Return JSON only for the full melody track (seed bars may be omitted or repeated identically; later sections required):
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
  }}
}}

Rules:
- do NOT alter the immutable relative_cell of the seed; treat it as fixed thematic identity
- mechanical theme deployments may overwrite target bars; still write playable notes across the full form
- for creative deployments (rhythmic_variation, melodic_variation, answer, counterphrase), write recognizable variants in the target section (target bars must not be empty)
- MUST include at least {min_melody_events} melody note events across the full {form.bar_count}-bar form (integrity rejects fewer)
- cover every non-seed section with playable notes (~1+ event per bar)
- ticks_per_quarter={ticks}; bar length={bar_ticks}; duration ticks={form.bar_count * bar_ticks}
- tonal center MUST remain {locked_key}
- instruments: {", ".join(request.prompt.instruments)}
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
    theme_ctx = theme_plan_prompt_projection(state.get("theme_plan"))
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
Theme plan (compact; do not invent melody notes from theme metadata):
{json.dumps({k: theme_ctx[k] for k in ("enabled", "motif_id", "deployments") if k in theme_ctx}, ensure_ascii=True)}

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
    theme_ctx = theme_plan_prompt_projection(state.get("theme_plan"))
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
Theme plan (compact context only; do not synthesize notes from harmony metadata):
{json.dumps(theme_ctx, ensure_ascii=True)}
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
- pad/harmony may use sustained whole/half notes (~1 event every 2 bars is enough); still cover the form
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
    # Theme failures before empty-track heuristics: empty_required_track messages often
    # mention "melody" and would otherwise steal routing from theme_target_missing.
    if codes & THEME_DIAGNOSTIC_CODES:
        if codes & {THEME_SOURCE_EMPTY, THEME_TARGET_MISSING, THEME_TARGET_OUT_OF_BOUNDS}:
            return "plan_themes"
        if THEME_IDENTITY_BELOW_THRESHOLD in codes:
            return "compose_melody"
        return "realize_themes"
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
        "pipeline_id": state.get("pipeline_id"),
        "seed": state.get("seed"),
        "current_stage": state.get("current_stage"),
        "failed_stage": state.get("failed_stage"),
        "repair_target": state.get("repair_target"),
        "retry_count": state.get("retry_count", 0),
        "stage_retry_count": state.get("stage_retry_count", 0),
        "form": summarize_form_plan(state.get("form_plan")),
        "harmony_event_count": len((state.get("harmony_plan").events if state.get("harmony_plan") else []) or []),
        "theme": summarize_theme_plan(state.get("theme_plan")),
        "melody": summarize_track_draft(state.get("melody_draft")),
        "bass": summarize_track_draft(state.get("bass_draft")),
        "accompaniment_count": len(accompaniment),
        "accompaniment_roles": [draft.role for draft in accompaniment],
        "diagnostics": summarize_diagnostics(state.get("validation_diagnostics")),
        "validation_ok": state.get("validation_ok"),
        "plan_ok": state.get("plan_ok"),
        "symbolic_ok": state.get("symbolic_ok"),
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
    """Log-safe prompt summary — never include instruction text content."""
    data = request.prompt.model_dump()
    raw_instructions = data.get("instructions")
    if raw_instructions:
        text = str(raw_instructions)
        data["instructions"] = None
        data["has_instructions"] = True
        data["instructions_length"] = len(text.strip())
    else:
        data["has_instructions"] = False
        data["instructions_length"] = 0
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
