import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import uvicorn

from .composition_schemas import CompositionV2, UnsupportedSchemaVersionError
from .db import ensure_database
from .llm_settings import load_llm_settings
from .ready import build_readiness_report, configure_logging, parse_cors_allow_origins
from .services.collaboration_access import (
    reset_request_actor_header,
    set_request_actor_header,
)
from .routers.projects import router as projects_router
from .routers.collaboration import router as collaboration_router
from .routers.imports import router as imports_router
from .routers.transcription import router as transcription_router
from .routers.neural_audio import router as neural_audio_router
from .routers.mix_analysis import router as mix_analysis_router
from .routers.mix_plan import router as mix_plan_router
from .routers.audio_recovery import router as audio_recovery_router
from .routers.analysis import router as analysis_router
from .routers.critique import router as critique_router
from .routers.motifs import router as motifs_router
from .routers.harmony import router as harmony_router
from .routers.composition_development import router as composition_development_router
from .routers.arrangement import router as arrangement_router
from .routers.ai_models import router as ai_models_router
from .routers.plugins import router as plugins_router
from .routers.ai_agents import router as ai_agents_router
from .routers.embeddings import router as embeddings_router
from .routers.composer_profiles import router as composer_profiles_router
from .routers.preferences import router as preferences_router
from .routers.ai_scheduling import router as ai_scheduling_router
from .routers.reference_features import router as reference_features_router
from .routers.live_performance import router as live_performance_router
from .routers.film_score import router as film_score_router
from .routers.video_scoring import router as video_scoring_router
from .routers.adaptive_scores import router as adaptive_scores_router
from .routers.adaptive_engine import adaptive_engine_validation_handler
from .routers.adaptive_engine import router as adaptive_engine_router
from .routers.musical_universe import router as musical_universe_router
from .routers.musical_dependency import router as musical_dependency_router
from .routers.personal_composer import router as personal_composer_router
from .reference_feature_schemas import (
    ReferenceFeatureError,
    map_reference_feature_error_to_http,
)
from .music_transformer.settings import load_music_transformer_settings
from .schemas import (
    Composition,
    LLMCompositionEditRequest,
    LLMCompositionEditResponse,
    LLMMusicGenerationRequest,
    LLMMusicGenerationResponse,
    LLMModelsResponse,
    LLMProviderModel,
)
from .services.composition_migration import CompositionMigrationError
from .services.composition_midi import CompositionMidiError, render_midi_with_report
from .services.composition_normalizer import CompositionNormalizationError, normalize_composition_json
from .services.composition_wav import CompositionWavError, load_wav_renderer_config, render_wav_with_report
from .services.composition_planner import OversizedLLMGenerationRequestError
from .services.llm_composition_editor import edit_composition_region
from .services.llm_music_generator import (
    GenerationConstraintViolationError,
    HybridPipelineUnavailableError,
    InvalidLLMOutputError,
    LLMGenerationError,
    NoLLMProviderConfiguredError,
    UnsupportedLLMProviderError,
    generate_music_json,
)
from .services.composition_projection import (
    PROJECTION_EXPOSE_HEADERS,
    projection_issues_as_warnings,
    projection_response_headers,
)
from .services.music_json_renderer import MusicJsonRenderError, render_musicxml


logger = logging.getLogger(__name__)


def _ai_resolution_response_fields() -> dict[str, Any]:
    from app.ai_runtime.routing import get_current_resolved_model, resolution_public_fields

    return resolution_public_fields(get_current_resolved_model())


# Apply LOG_LEVEL before other modules emit startup logs.
_CONFIGURED_LOG_LEVEL = configure_logging()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db_path = ensure_database()
    llm_settings = load_llm_settings()
    provider_names = [provider.provider for provider in llm_settings.providers]
    logger.info(
        "Application startup database ready",
        extra={"project_db_path": str(db_path), "log_level": _CONFIGURED_LOG_LEVEL},
    )
    logger.info(
        "Application startup LLM providers configured",
        extra={
            "providers": provider_names,
            "default_provider": llm_settings.default_provider,
            "provider_count": len(provider_names),
        },
    )
    if not provider_names:
        logger.warning("No LLM providers configured; generate/edit routes will return 503")
    try:
        from .services.plugin_lifecycle import reconcile_on_startup

        loaded = reconcile_on_startup()
        logger.info(
            "Application startup plugins reconciled",
            extra={
                "plugin_count": len(loaded),
                "enabled_count": sum(1 for item in loaded if item.status == "enabled"),
            },
        )
    except Exception as exc:
        logger.warning(
            "Application startup plugin load failed; continuing without plugins",
            extra={"error_type": type(exc).__name__},
        )
    try:
        from .services.personal_composer_service import sweep_orphaned_personal_jobs

        interrupted = sweep_orphaned_personal_jobs()
        logger.info(
            "Application startup personal composers swept",
            extra={"interrupted": interrupted},
        )
    except Exception as exc:
        logger.warning(
            "Application startup personal composer sweep failed",
            extra={"error_type": type(exc).__name__, "code": "personal_sweep_skipped"},
        )
    try:
        from .execution_node_settings import load_execution_node_settings
        from .services.execution_node_fake import ensure_fake_peer_registered
        from .services.execution_node_worker_loop import start_worker_loop

        exec_settings = load_execution_node_settings()
        logger.info(
            "Execution nodes startup",
            extra={
                "enabled": exec_settings.enabled,
                "role": exec_settings.role,
                "fake": exec_settings.fake,
            },
        )
        if exec_settings.enabled and exec_settings.fake and exec_settings.role in {
            "controller",
            "both",
        }:
            ensure_fake_peer_registered()
        if exec_settings.enabled and exec_settings.role in {"worker", "both"}:
            start_worker_loop()
    except Exception as exc:
        logger.warning(
            "Execution nodes startup skipped",
            extra={"error_type": type(exc).__name__},
        )
    yield
    from .services.adaptive_engine_service import cancel_adaptive_engine_tasks
    from .services.execution_node_worker_loop import stop_worker_loop

    cancel_adaptive_engine_tasks()
    await stop_worker_loop()
    logger.info("Application shutdown")


app = FastAPI(title="LLM Music Composer API", version="1.0.0", lifespan=lifespan)


@app.middleware("http")
async def collaboration_actor_context(request, call_next):
    """Capture X-Mukit-Actor for flag-gated checks. The value is a local selector."""
    token = set_request_actor_header(request.headers.get("x-mukit-actor"))
    try:
        return await call_next(request)
    finally:
        reset_request_actor_header(token)


_cors_origins = parse_cors_allow_origins()
logger.debug("Installing CORS middleware", extra={"origins": _cors_origins})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=list(PROJECTION_EXPOSE_HEADERS),
)

from .execution_node_settings import execution_node_role, execution_nodes_enabled

_execution_nodes_enabled = execution_nodes_enabled()
_execution_node_role = execution_node_role()
_worker_only = bool(_execution_nodes_enabled and _execution_node_role == "worker")

if _execution_nodes_enabled:
    logger.info(
        "Execution nodes feature enabled",
        extra={
            "enabled": True,
            "role": _execution_node_role,
            "worker_only": _worker_only,
        },
    )

if not _worker_only:
    app.include_router(projects_router)
    app.include_router(collaboration_router)
    app.include_router(imports_router)
    app.include_router(transcription_router)
    app.include_router(neural_audio_router)
    app.include_router(mix_analysis_router)
    app.include_router(mix_plan_router)
    app.include_router(audio_recovery_router)
    app.include_router(analysis_router)
    app.include_router(critique_router)
    app.include_router(motifs_router)
    app.include_router(harmony_router)
    app.include_router(composition_development_router)
    app.include_router(arrangement_router)
    app.include_router(ai_models_router)
    app.include_router(plugins_router)
    app.include_router(ai_agents_router)
    app.include_router(embeddings_router)
    app.include_router(composer_profiles_router)
    app.include_router(preferences_router)
    app.include_router(ai_scheduling_router)
    app.include_router(reference_features_router)
    app.include_router(live_performance_router)
    app.include_router(video_scoring_router)
    app.include_router(film_score_router)
    app.include_router(adaptive_scores_router)
    app.include_router(adaptive_engine_router)
    app.include_router(musical_universe_router)
    app.include_router(musical_dependency_router)
    app.include_router(personal_composer_router)
    app.add_exception_handler(RequestValidationError, adaptive_engine_validation_handler)

    _mt_settings = load_music_transformer_settings()
    if _mt_settings.api_enabled:
        from .routers.music_transformer import router as music_transformer_router

        app.include_router(music_transformer_router)
        logger.info(
            "Music Transformer API router enabled",
            extra={
                "device": _mt_settings.device,
                "has_default_checkpoint": _mt_settings.default_checkpoint is not None,
            },
        )
    else:
        logger.info("Music Transformer API router disabled (MUSIC_TRANSFORMER_API_ENABLED=0)")
else:
    logger.info(
        "Worker-only mount: studio routers omitted",
        extra={"role": _execution_node_role},
    )

# Controllers and workers always mount the typed surfaces; handlers return 404
# when the feature or role is off so TestClient can toggle env after import.
from .routers.execution_nodes import router as execution_nodes_router
from .routers.execution_worker import router as execution_worker_router

app.include_router(execution_nodes_router)
app.include_router(execution_worker_router)
if _execution_nodes_enabled:
    logger.info(
        "Execution node routers mounted",
        extra={"role": _execution_node_role},
    )


def _composition_export_summary(composition: CompositionV2) -> dict:
    return {
        "schema_version": composition.schema_version,
        "track_count": len(composition.tracks),
        "event_count": sum(len(track.events) for track in composition.tracks),
        "duration_ticks": composition.duration_ticks,
        "tempo": composition.tempo,
        "time_signature": composition.time_signature,
    }


_EXPORT_TITLE_KEYS = ("export_title", "project_title")


def _pop_export_title(payload: dict[str, Any]) -> str | None:
    """Optional DAW-friendly filename stem; never part of the composition contract."""
    for key in _EXPORT_TITLE_KEYS:
        if key not in payload:
            continue
        raw = payload.pop(key)
        if isinstance(raw, str):
            cleaned = raw.strip()
            if cleaned:
                return cleaned[:80]
    return None


def _safe_export_filename(
    composition: CompositionV2,
    extension: str,
    *,
    title: str | None = None,
) -> str:
    """Stable Content-Disposition stem ending in ``-export.{ext}`` for DAW handoff."""
    stem_source = (title or "").strip()
    if not stem_source:
        stem_source = f"composition-{composition.key}-{composition.tempo}bpm"
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in stem_source.lower())
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-") or "composition"
    if not safe.endswith("-export"):
        safe = f"{safe}-export"
    return f"{safe}.{extension}"


def _normalize_export_composition(payload: dict[str, Any]) -> CompositionV2:
    try:
        return normalize_composition_json(payload)
    except (
        CompositionNormalizationError,
        CompositionMigrationError,
        UnsupportedSchemaVersionError,
        ValueError,
    ) as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:500]) from exc


def _prepare_export_payload(payload: dict[str, Any] | CompositionV2 | Any) -> tuple[CompositionV2, str | None]:
    """Accept HTTP JSON dicts or already-validated composition models (direct handler tests)."""
    if isinstance(payload, CompositionV2):
        return payload, None
    if hasattr(payload, "model_dump") and getattr(payload, "schema_version", None):
        # CompositionV1 or similar pydantic model — normalize via dump.
        data = payload.model_dump(mode="json")
        return _normalize_export_composition(data), None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Export payload must be a composition object")
    data = dict(payload)
    title = _pop_export_title(data)
    return _normalize_export_composition(data), title


def _export_response_headers(*extra: dict[str, str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in extra:
        headers.update(item)
    return headers


@app.get("/")
async def root():
    return {"message": "Music Composer API is running!"}

@app.get("/health")
async def health_check():
    """Liveness probe — process is up."""
    return {"status": "healthy"}


@app.get("/ready")
async def readiness_check():
    """Readiness probe — DB openable; LLM/WAV reported as non-secret flags."""
    report = build_readiness_report()
    if not report.get("ready"):
        raise HTTPException(status_code=503, detail=report)
    return report

@app.get("/llm/models", response_model=LLMModelsResponse)
async def get_llm_models():
    """Return configured LLM provider/model options without exposing API keys."""
    logger.info("LLM model discovery requested")
    settings = load_llm_settings()
    logger.debug(
        "LLM provider availability summary",
        extra={"providers": [provider.provider for provider in settings.providers]},
    )

    from .services.fake_llm import FAKE_DISPLAY_NAME, is_fake_provider
    from .local_llm_settings import LOCAL_PROVIDER, load_local_llm_settings
    from .ai_runtime.registry import get_model, reload_registry
    from .ai_runtime.errors import ModelNotFoundError

    reload_registry()
    local_settings = load_local_llm_settings()
    models: list[LLMProviderModel] = []
    for provider in settings.providers:
        model_id = f"{provider.provider}:{provider.model}"
        if provider.provider == LOCAL_PROVIDER:
            # Only list selectable local models when registry status is usable (ready).
            try:
                descriptor = get_model(model_id)
            except ModelNotFoundError:
                logger.info(
                    "Skipping local LLM in /llm/models; not in registry",
                    extra={"model_id": model_id},
                )
                continue
            if descriptor.status != "ready":
                logger.info(
                    "Skipping local LLM in /llm/models; not ready",
                    extra={"model_id": model_id, "status": descriptor.status},
                )
                continue
            display_name = local_settings.display_name
        elif is_fake_provider(provider):
            display_name = FAKE_DISPLAY_NAME
        else:
            display_name = f"{provider.provider.title()} ({provider.model})"
        models.append(
            LLMProviderModel(
                provider=provider.provider,
                model=provider.model,
                display_name=display_name,
                is_default=provider.is_default,
                model_id=model_id,
            )
        )

    default_model = next((m.model for m in models if m.is_default), None)
    if default_model is None and models:
        default_model = models[0].model
    warnings = (
        []
        if models
        else [
            "No LLM providers configured. Set OPENAI_API_KEY or DEEPSEEK_API_KEY, "
            "enable LLM_FAKE_MODE=1 for credit-free demos/tests, "
            "or use optional local AI (--profile local-ai) with LOCAL_LLM_ENABLED=1."
        ]
    )
    logger.info(
        "LLM model discovery complete",
        extra={
            "model_count": len(models),
            "local_enabled": local_settings.enabled,
            "model_ids": [m.model_id for m in models],
        },
    )

    return LLMModelsResponse(
        models=models,
        default_provider=settings.default_provider if any(
            m.provider == settings.default_provider for m in models
        ) else (models[0].provider if models else None),
        default_model=default_model,
        warnings=warnings,
    )

@app.post("/llm/generate-music-json", response_model=LLMMusicGenerationResponse)
async def generate_llm_music_json(request: LLMMusicGenerationRequest):
    """Generate validated structured music JSON and derived MusicXML with an LLM."""
    logger.info(
        "LLM music JSON request started",
        extra={"provider": request.selection.provider, "model": request.selection.model},
    )
    logger.debug(
        "LLM music JSON request parameters",
        extra={
            "genre": request.prompt.genre,
            "mood": request.prompt.mood,
            "tempo_min": request.prompt.tempo_min,
            "tempo_max": request.prompt.tempo_max,
            "track_count": len(request.prompt.instruments),
        },
    )

    try:
        settings = load_llm_settings()
        music, warnings, provider, validation, provenance = await generate_music_json(
            request, settings
        )
        musicxml, render_report = render_musicxml(music)
        all_warnings = [*warnings, *projection_issues_as_warnings(render_report)]
        logger.info(
            "LLM music JSON request completed",
            extra={
                "provider": provider.provider,
                "model": provider.model,
                "schema_version": music.schema_version,
                "warning_count": len(all_warnings),
                "validation_status": validation.status if validation else None,
                "pipeline_id": provenance.get("pipeline_id"),
                "seed": provenance.get("seed"),
                "stage_model_ids": [
                    stage.get("model_id") for stage in (provenance.get("stages") or [])
                ],
            },
        )
        logger.debug(
            "LLM music JSON response shape",
            extra={
                "schema_version": music.schema_version,
                "sections": len(music.sections),
                "tracks": len(music.tracks),
                "harmony": len(music.harmony),
                "track_ids": [track.id for track in music.tracks],
                "events": sum(len(track.events) for track in music.tracks),
                "duration_ticks": music.duration_ticks,
                "musicxml_length": len(musicxml),
                "validation_error_count": len(validation.errors) if validation else 0,
            },
        )
        return LLMMusicGenerationResponse(
            music=music,
            provider=provider.provider,
            model=provider.model,
            musicxml=musicxml,
            warnings=all_warnings,
            validation=validation,
            pipeline_id=provenance.get("pipeline_id"),
            stages=provenance.get("stages") or [],
            plan_schema_version=provenance.get("plan_schema_version"),
            constraints_digest_prefix=provenance.get("constraints_digest_prefix"),
            seed=provenance.get("seed"),
            generation_parameters=provenance.get("generation_parameters"),
            **_ai_resolution_response_fields(),
        )
    except OversizedLLMGenerationRequestError as exc:
        logger.warning(
            "LLM generation request rejected as oversized",
            extra={"code": "oversized_generation_request", "detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ReferenceFeatureError as exc:
        status, detail = map_reference_feature_error_to_http(exc)
        logger.warning(
            "LLM generation reference feature error",
            extra={"error_code": exc.code, "http_status": status},
        )
        raise HTTPException(status_code=status, detail=detail) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning("LLM provider unavailable", extra={"reason": "no_configured_providers"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning("LLM provider unavailable", extra={"reason": "unsupported_provider"})
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HybridPipelineUnavailableError as exc:
        logger.error(
            "Hybrid/symbolic pipeline unavailable",
            extra={"code": exc.code, "detail": str(exc)[:200], "fallback_applied": False},
        )
        raise HTTPException(
            status_code=503,
            detail={"message": str(exc)[:500], "code": exc.code},
        ) from exc
    except GenerationConstraintViolationError as exc:
        detail = {
            "message": str(exc)[:500],
            "codes": [item.code for item in exc.diagnostics if item.severity == "error"],
            "errors": [
                {
                    "code": item.code,
                    "message": item.message,
                    "expected": item.context.get("expected"),
                    "actual": item.context.get("actual"),
                    "stage": item.context.get("stage"),
                }
                for item in exc.diagnostics
                if item.severity == "error"
            ][:20],
            "validation": exc.report.model_dump() if exc.report else None,
        }
        logger.warning(
            "Generation constraint violation returned as structured 502",
            extra={
                "codes": detail["codes"],
                "error_count": len(detail["errors"]),
            },
        )
        raise HTTPException(status_code=502, detail=detail) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Invalid or non-playable LLM output could not be corrected",
            extra={"detail": str(exc)[:300]},
        )
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    except MusicJsonRenderError as exc:
        logger.error("MusicXML rendering failed", extra={"error_type": type(exc).__name__})
        raise HTTPException(status_code=500, detail="Generated JSON could not be rendered as MusicXML") from exc
    except LLMGenerationError as exc:
        logger.error(
            "LLM music JSON request failed",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/llm/edit-composition-region", response_model=LLMCompositionEditResponse)
async def edit_llm_composition_region(request: LLMCompositionEditRequest):
    """Apply an AI replace_region edit to a selected Composition V1 bar/track range."""
    selection = request.edit.selection
    logger.info(
        "LLM composition region edit request started",
        extra={
            "provider": request.selection.provider,
            "model": request.selection.model,
            "start_bar": selection.start_bar,
            "end_bar": selection.end_bar,
            "target_track_count": len(selection.track_ids or []),
            "bar_count": request.composition.bar_count,
            "track_count": len(request.composition.tracks),
            "instruction_length": len(request.edit.instruction),
        },
    )
    logger.debug(
        "LLM composition region edit request shape",
        extra={
            "schema_version": request.composition.schema_version,
            "track_ids": selection.track_ids,
            "section_type": selection.section_type,
            "allow_harmony_changes": request.edit.allow_harmony_changes,
            "allow_added_tracks": request.edit.allow_added_tracks,
        },
    )

    try:
        settings = load_llm_settings()
        composition, patch, warnings, provider, generation_parameters = await edit_composition_region(
            request, settings
        )
        musicxml, render_report = render_musicxml(composition)
        all_warnings = [*warnings, *projection_issues_as_warnings(render_report), *patch.warnings]
        logger.info(
            "LLM composition region edit request completed",
            extra={
                "provider": provider.provider,
                "model": provider.model,
                "schema_version": composition.schema_version,
                "operation": patch.operation,
                "start_bar": patch.start_bar,
                "end_bar": patch.end_bar,
                "warning_count": len(all_warnings),
                "event_count": sum(len(track.events) for track in composition.tracks),
                "has_generation_parameters": bool(generation_parameters),
            },
        )
        logger.debug(
            "LLM composition region edit response shape",
            extra={
                "schema_version": composition.schema_version,
                "replace_track_count": len(patch.replace_tracks),
                "added_track_count": len(patch.added_tracks),
                "has_harmony_patch": patch.harmony_patch is not None,
                "musicxml_length": len(musicxml),
                "track_ids": [track.id for track in composition.tracks],
            },
        )
        return LLMCompositionEditResponse(
            composition=composition,
            patch=patch,
            provider=provider.provider,
            model=provider.model,
            musicxml=musicxml,
            warnings=all_warnings,
            generation_parameters=generation_parameters,
            **_ai_resolution_response_fields(),
        )
    except ReferenceFeatureError as exc:
        status, detail = map_reference_feature_error_to_http(exc)
        logger.warning(
            "Reference conditioning rejected on region edit",
            extra={"error_code": exc.code, "http_status": status},
        )
        raise HTTPException(status_code=status, detail=detail) from exc
    except NoLLMProviderConfiguredError as exc:
        logger.warning(
            "LLM region edit provider unavailable",
            extra={"reason": "no_configured_providers"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedLLMProviderError as exc:
        logger.warning(
            "LLM region edit provider unavailable",
            extra={"reason": "unsupported_provider"},
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidLLMOutputError as exc:
        logger.warning(
            "Invalid LLM region edit patch could not be corrected",
            extra={"detail": str(exc)[:300]},
        )
        raise HTTPException(status_code=502, detail=str(exc)[:500]) from exc
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML rendering failed after successful region edit",
            extra={"error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=500, detail="Edited composition could not be rendered as MusicXML") from exc
    except LLMGenerationError as exc:
        logger.error(
            "LLM composition region edit request failed",
            extra={"error_type": type(exc).__name__, "error_detail": str(exc)[:200]},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/export/musicxml")
async def export_musicxml(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to a downloadable MusicXML file."""
    composition, export_title = _prepare_export_payload(payload)
    summary = _composition_export_summary(composition)
    logger.info("MusicXML export request started", extra={"format": "musicxml", **summary})
    try:
        musicxml, report = render_musicxml(composition)
        filename = _safe_export_filename(composition, "musicxml", title=export_title)
        if report.issues:
            logger.warning(
                "MusicXML export completed with projection issues",
                extra={"format": "musicxml", **report.summary_extra(), **summary},
            )
        logger.info(
            "MusicXML export request completed",
            extra={
                "format": "musicxml",
                "byte_length": len(musicxml.encode("utf-8")),
                "projection_status": report.status,
                "projection_codes": report.compact_codes(),
                **summary,
                **report.summary_extra(),
            },
        )
        logger.debug(
            "MusicXML export response metadata",
            extra={"filename": filename, "musicxml_length": len(musicxml), **report.summary_extra()},
        )
        return Response(
            content=musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
            headers=_export_response_headers(
                {"Content-Disposition": f'attachment; filename="{filename}"'},
                projection_response_headers(report),
            ),
        )
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML export failed",
            extra={"format": "musicxml", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MusicXML") from exc


@app.post("/export/musicxml/preview")
async def export_musicxml_preview(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to MusicXML text without download headers."""
    composition, _export_title = _prepare_export_payload(payload)
    summary = _composition_export_summary(composition)
    logger.info("MusicXML preview render started", extra={"format": "musicxml_preview", **summary})
    try:
        musicxml, report = render_musicxml(composition)
        if report.issues:
            logger.warning(
                "MusicXML preview completed with projection issues",
                extra={"format": "musicxml_preview", **report.summary_extra(), **summary},
            )
        logger.info(
            "MusicXML preview render completed",
            extra={
                "format": "musicxml_preview",
                "musicxml_length": len(musicxml),
                **summary,
                **report.summary_extra(),
            },
        )
        return Response(
            content=musicxml,
            media_type="application/vnd.recordare.musicxml+xml",
            headers=projection_response_headers(report),
        )
    except MusicJsonRenderError as exc:
        logger.error(
            "MusicXML preview render failed",
            extra={"format": "musicxml_preview", "error_type": type(exc).__name__, **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MusicXML") from exc


@app.post("/export/midi")
async def export_midi(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to a downloadable Standard MIDI File."""
    composition, export_title = _prepare_export_payload(payload)
    summary = _composition_export_summary(composition)
    logger.info("MIDI export request started", extra={"format": "midi", **summary})
    try:
        result = render_midi_with_report(composition)
        midi_bytes = result.midi_bytes
        report = result.report
        filename = _safe_export_filename(composition, "mid", title=export_title)
        if report.issues:
            logger.warning(
                "MIDI export completed with projection issues",
                extra={"format": "midi", **report.summary_extra(), **summary},
            )
        logger.info(
            "MIDI export request completed",
            extra={
                "format": "midi",
                "byte_length": len(midi_bytes),
                "projection_status": report.status,
                "projection_codes": report.compact_codes(),
                **summary,
                **report.summary_extra(),
            },
        )
        logger.debug(
            "MIDI export response metadata",
            extra={"filename": filename, "byte_length": len(midi_bytes), **report.summary_extra()},
        )
        return Response(
            content=midi_bytes,
            media_type="audio/midi",
            headers=_export_response_headers(
                {"Content-Disposition": f'attachment; filename="{filename}"'},
                projection_response_headers(report),
            ),
        )
    except CompositionMidiError as exc:
        logger.error(
            "MIDI export failed",
            extra={"format": "midi", "error_type": type(exc).__name__, "detail": str(exc)[:200], **summary},
        )
        raise HTTPException(status_code=500, detail="Composition could not be rendered as MIDI") from exc


@app.post("/export/wav")
async def export_wav(payload: dict[str, Any]):
    """Render canonical Composition JSON (v1 or v2) to a downloadable WAV file via FluidSynth."""
    composition, export_title = _prepare_export_payload(payload)
    summary = _composition_export_summary(composition)
    logger.info("WAV export request started", extra={"format": "wav", **summary})
    config = load_wav_renderer_config()
    logger.debug(
        "WAV export renderer config",
        extra={
            "format": "wav",
            "fluidsynth": config.fluidsynth_basename,
            "soundfont": config.soundfont_basename,
            "sample_rate": config.sample_rate,
            "gain": config.gain,
            "timeout_seconds": config.timeout_seconds,
            "fluidsynth_exists": config.fluidsynth_exists,
            "soundfont_exists": config.soundfont_exists,
        },
    )
    try:
        result = render_wav_with_report(composition)
        wav_bytes = result.wav_bytes
        report = result.report
        filename = _safe_export_filename(composition, "wav", title=export_title)
        if report.issues:
            logger.warning(
                "WAV export completed with projection issues",
                extra={"format": "wav", **report.summary_extra(), **summary},
            )
        logger.info(
            "WAV export request completed",
            extra={"format": "wav", "byte_length": len(wav_bytes), **summary, **report.summary_extra()},
        )
        logger.debug(
            "WAV export response metadata",
            extra={"filename": filename, "byte_length": len(wav_bytes), **report.summary_extra()},
        )
        return Response(
            content=wav_bytes,
            media_type="audio/wav",
            headers=_export_response_headers(
                {"Content-Disposition": f'attachment; filename="{filename}"'},
                projection_response_headers(report),
            ),
        )
    except CompositionWavError as exc:
        if exc.unavailable:
            logger.warning(
                "WAV export unavailable",
                extra={"format": "wav", "error_type": type(exc).__name__, "detail": str(exc)[:200], **summary},
            )
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        logger.error(
            "WAV export failed",
            extra={"format": "wav", "error_type": type(exc).__name__, "detail": str(exc)[:200], **summary},
        )
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc



if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8888)
